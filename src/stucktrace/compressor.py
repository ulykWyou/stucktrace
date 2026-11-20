"""Trajectory Compressor.

Turns a raw event log (hundreds of runs, previews, timestamps) into a compact,
structured context that a human or an LLM can read in seconds:

1. **Error clustering** – error messages are normalised (identifiers, numbers,
   addresses stripped) into *signatures*, so ``name 'x' is not defined`` and
   ``name 'total' is not defined`` collapse into one cluster.
2. **Stuck / panic detection** – five signals per task:
   * ``rapid_fail_burst`` – >= N failed runs with short gaps between them
   * ``repeated_error``   – the same signature N times in a row
   * ``long_pause``       – a long silence right after a failure
   * ``rewrite``          – a large edit right after a failure (thrashing)
   * ``confusion``        – explicit ``mark_confusion()`` marker
   The signals are combined into a ``panic_score`` in [0, 1].
3. **Key transitions** – every failure -> success step, with the error that was
   fixed, how many attempts it took, the edit size and whether a visualization
   step preceded it.
4. **Run-length digest** – a one-line symbolic timeline, e.g.
   ``F:NameError x3 | P:140s | ?"loops" | V | S``.
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Optional, Union

from .events import CONFUSION, RUN, TASK, VIZ, Event

if TYPE_CHECKING:  # pragma: no cover
    from .tracker import Session


@dataclass
class CompressorConfig:
    panic_gap: float = 25.0       # seconds; failed runs closer than this form a burst
    panic_min_burst: int = 3      # failed runs needed for a burst
    repeat_streak: int = 3        # identical error signature in a row
    stall_gap: float = 120.0      # seconds of silence after a failure = long pause
    rewrite_ratio: float = 0.4    # edit_ratio after a failure that counts as a rewrite
    max_examples: int = 1         # code previews kept per error cluster


_NORMALISERS = [
    (re.compile(r"'[^']*'|\"[^\"]*\""), "'…'"),
    (re.compile(r"\b0x[0-9a-fA-F]+\b"), "<addr>"),
    (re.compile(r"\b\d+(\.\d+)?\b"), "<n>"),
    (re.compile(r"\s+"), " "),
]

_WEIGHTS = {"rapid_fail_burst": 1.0, "repeated_error": 0.7, "long_pause": 0.5,
            "rewrite": 0.5, "confusion": 0.9}


def error_signature(error_type: Optional[str], message: Optional[str]) -> str:
    msg = message or ""
    for rx, repl in _NORMALISERS:
        msg = rx.sub(repl, msg)
    msg = msg.strip()[:100]
    return f"{error_type or 'Error'}: {msg}" if msg else (error_type or "Error")


@dataclass
class ErrorCluster:
    signature: str
    error_type: str
    count: int
    first_attempt: int
    last_attempt: int
    max_streak: int
    resolved: bool
    example: Optional[str] = None


@dataclass
class StuckPoint:
    kind: str
    t: float                       # seconds since session start
    attempt: Optional[int] = None  # 1-based attempt number inside the task
    detail: str = ""               # English, used in the LLM prompt
    data: Dict[str, Any] = field(default_factory=dict)  # structured params for localized rendering


@dataclass
class Transition:
    fixed_error: str
    after_attempts: int
    edit_ratio: Optional[float]
    seconds: float
    viz_before: bool


@dataclass
class TaskSummary:
    task: str
    attempts: int
    failures: int
    solved: bool
    final_ok: bool
    first_success_attempt: Optional[int]
    time_on_task: float
    time_to_first_success: Optional[float]
    panic_score: float
    panic_level: str
    error_clusters: List[ErrorCluster] = field(default_factory=list)
    stuck_points: List[StuckPoint] = field(default_factory=list)
    transitions: List[Transition] = field(default_factory=list)
    confusions: List[str] = field(default_factory=list)
    viz_steps: int = 0
    viz_helped: bool = False
    digest: str = ""

    @property
    def top_error(self) -> Optional[str]:
        return self.error_clusters[0].signature if self.error_clusters else None


@dataclass
class CompressedTrajectory:
    session_id: str
    metadata: Dict[str, Any]
    duration: float
    totals: Dict[str, Any]
    tasks: List[TaskSummary]
    stats: Dict[str, Any] = field(default_factory=dict)

    # ---------------------------------------------------------------- views
    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        for t, src in zip(d["tasks"], self.tasks):
            t["top_error"] = src.top_error
        return d

    def to_json(self, indent: Optional[int] = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    def hardest(self) -> Optional[TaskSummary]:
        return max(self.tasks, key=lambda t: (t.panic_score, t.failures), default=None)

    def to_markdown(self) -> str:
        tt = self.totals
        out = [
            f"### Compressed trajectory `{self.session_id}`",
            f"{tt['tasks']} task(s) · {tt['runs']} runs · {tt['failures']} failed · "
            f"{_fmt_dur(self.duration)} · compression {self.stats.get('ratio', '?')}×",
            "",
            "| Task | Runs | Solved | Panic | Top error | Digest |",
            "|---|---|---|---|---|---|",
        ]
        for t in self.tasks:
            out.append(
                f"| {t.task} | {t.attempts} | {'✅' if t.solved else '❌'} | "
                f"{t.panic_level} ({t.panic_score:.2f}) | {t.top_error or '—'} | `{t.digest}` |"
            )
        return "\n".join(out)

    def to_prompt(self, max_chars: int = 6000) -> str:
        """Compact, LLM-friendly text (no code previews unless there is room)."""
        lines = [f"SESSION {self.session_id} duration={_fmt_dur(self.duration)} "
                 f"tasks={self.totals['tasks']} runs={self.totals['runs']} failures={self.totals['failures']}"]
        for t in self.tasks:
            lines.append(
                f"\nTASK {t.task}: attempts={t.attempts} failures={t.failures} solved={t.solved} "
                f"first_success_attempt={t.first_success_attempt} time={_fmt_dur(t.time_on_task)} "
                f"panic={t.panic_level}({t.panic_score:.2f}) viz_steps={t.viz_steps} viz_helped={t.viz_helped}"
            )
            lines.append(f"  digest: {t.digest}")
            for c in t.error_clusters:
                lines.append(f"  error x{c.count} (streak {c.max_streak}, resolved={c.resolved}): {c.signature}")
            for s in t.stuck_points:
                lines.append(f"  stuck[{s.kind}] at +{_fmt_clock(s.t)}: {s.detail}")
            for tr in t.transitions:
                lines.append(f"  fix: {tr.fixed_error} after {tr.after_attempts} failed attempt(s), "
                             f"edit={_pct(tr.edit_ratio)}, {tr.seconds:.0f}s, viz_before={tr.viz_before}")
            for c in t.confusions:
                lines.append(f'  student said: "{c}"')
        text = "\n".join(lines)
        if len(text) < max_chars * 0.7:
            extra = []
            for t in self.tasks:
                for c in t.error_clusters:
                    if c.example:
                        extra.append(f"  [{t.task}] code at '{c.error_type}':\n    " + c.example.replace("\n", "\n    "))
            if extra:
                text += "\n\nCODE SNAPSHOTS (for diagnosis only):\n" + "\n".join(extra)
        return text[:max_chars]

    def _repr_markdown_(self) -> str:
        return self.to_markdown()


# ------------------------------------------------------------------ helpers
def _fmt_dur(sec: Optional[float]) -> str:
    if sec is None:
        return "—"
    sec = max(0, int(round(sec)))
    return f"{sec // 60}m{sec % 60:02d}s" if sec >= 60 else f"{sec}s"


def _fmt_clock(sec: float) -> str:
    sec = max(0, int(sec))
    return f"{sec // 60:02d}:{sec % 60:02d}"


def _pct(r: Optional[float]) -> str:
    return "—" if r is None else f"{r * 100:.0f}%"


def _level(score: float) -> str:
    return "high" if score >= 0.66 else "medium" if score >= 0.33 else "low"


def _digest(events: List[Event], t0: float, cfg: CompressorConfig) -> str:
    tokens: List[str] = []
    prev: Optional[Event] = None
    for e in events:
        if prev is not None and prev.kind == RUN and not prev.ok and e.ts - prev.ts >= cfg.stall_gap:
            tokens.append(f"P:{e.ts - prev.ts:.0f}s")
        if e.kind == RUN:
            tokens.append("S" if e.ok else f"F:{e.error_type or 'Error'}")
        elif e.kind == CONFUSION:
            tokens.append(f'?"{(e.text or "")[:24]}"')
        elif e.kind == VIZ:
            tokens.append("V")
        if e.kind in (RUN, CONFUSION, VIZ):
            prev = e
    # run-length encode
    out: List[str] = []
    i = 0
    while i < len(tokens):
        j = i
        while j + 1 < len(tokens) and tokens[j + 1] == tokens[i]:
            j += 1
        n = j - i + 1
        out.append(tokens[i] if n == 1 else f"{tokens[i]} x{n}")
        i = j + 1
    return " | ".join(out)


def _analyze_task(name: str, events: List[Event], t0: float, cfg: CompressorConfig) -> TaskSummary:
    runs = [e for e in events if e.kind == RUN]
    fails = [e for e in runs if not e.ok]
    first_ok = next((i for i, e in enumerate(runs) if e.ok), None)
    t_start = events[0].ts
    t_end = events[-1].ts

    # ---- error clusters
    sigs = [error_signature(e.error_type, e.error_msg) if not e.ok else None for e in runs]
    clusters: Dict[str, ErrorCluster] = {}
    streak_sig, streak = None, 0
    for idx, (e, sig) in enumerate(zip(runs, sigs)):
        streak = streak + 1 if (sig is not None and sig == streak_sig) else (1 if sig else 0)
        streak_sig = sig
        if sig is None:
            continue
        c = clusters.get(sig)
        if c is None:
            c = clusters[sig] = ErrorCluster(sig, e.error_type or "Error", 0, idx + 1, idx + 1, 0, False,
                                            e.code_preview if cfg.max_examples else None)
        c.count += 1
        c.last_attempt = idx + 1
        c.max_streak = max(c.max_streak, streak)
    for c in clusters.values():
        c.resolved = any(r.ok for r in runs[c.last_attempt:])
    ordered = sorted(clusters.values(), key=lambda c: (-c.count, c.first_attempt))

    # ---- stuck points
    stuck: List[StuckPoint] = []
    # rapid-fail bursts
    burst: List[int] = []
    for idx, e in enumerate(runs + [None]):  # sentinel flushes the last burst
        cont = (e is not None and not e.ok and burst and e.ts - runs[burst[-1]].ts <= cfg.panic_gap)
        if e is not None and not e.ok and (not burst or cont):
            burst.append(idx)
            continue
        if len(burst) >= cfg.panic_min_burst:
            a, b = runs[burst[0]], runs[burst[-1]]
            span = round(b.ts - a.ts)
            stuck.append(StuckPoint("rapid_fail_burst", a.ts - t0, burst[0] + 1,
                                    f"{len(burst)} failed runs in {span}s", {"n": len(burst), "span": span}))
        burst = [idx] if (e is not None and not e.ok) else []
    # repeated identical errors
    for c in ordered:
        if c.max_streak >= cfg.repeat_streak:
            first = runs[c.first_attempt - 1]
            stuck.append(StuckPoint("repeated_error", first.ts - t0, c.first_attempt,
                                    f"same {c.error_type} {c.max_streak}x in a row",
                                    {"etype": c.error_type, "n": c.max_streak}))
    # long pauses after failure / rewrites after failure
    timeline = [e for e in events if e.kind in (RUN, CONFUSION, VIZ)]
    for a, b in zip(timeline, timeline[1:]):
        if a.kind == RUN and not a.ok and b.ts - a.ts >= cfg.stall_gap:
            secs = round(b.ts - a.ts)
            stuck.append(StuckPoint("long_pause", a.ts - t0, runs.index(a) + 1,
                                    f"{secs}s silence after {a.error_type}",
                                    {"secs": secs, "etype": a.error_type or "Error"}))
    for i in range(1, len(runs)):
        r, p = runs[i], runs[i - 1]
        if not p.ok and r.edit_ratio is not None and r.edit_ratio >= cfg.rewrite_ratio:
            stuck.append(StuckPoint("rewrite", r.ts - t0, i + 1,
                                    f"{_pct(r.edit_ratio)} of code changed after {p.error_type}",
                                    {"pct": _pct(r.edit_ratio), "etype": p.error_type or "Error"}))
    confusions = [e for e in events if e.kind == CONFUSION]
    for e in confusions:
        att = sum(1 for r in runs if r.ts <= e.ts)
        stuck.append(StuckPoint("confusion", e.ts - t0, att or None, f'"{e.text}"' if e.text else "marked",
                                {"text": e.text or "?"}))
    stuck.sort(key=lambda s: s.t)

    # ---- panic score
    kinds = Counter(s.kind for s in stuck)
    raw = sum(_WEIGHTS[k] * n for k, n in kinds.items()) + 0.1 * max(0, len(fails) - 2)
    score = round(1 - math.exp(-raw / 2.0), 3)

    # ---- transitions + viz effect
    viz = [e for e in events if e.kind == VIZ]
    transitions: List[Transition] = []
    fail_streak_start: Optional[int] = None
    for i, r in enumerate(runs):
        if not r.ok:
            if fail_streak_start is None:
                fail_streak_start = i
            continue
        if fail_streak_start is not None:
            last_fail = runs[i - 1]
            transitions.append(Transition(
                fixed_error=error_signature(last_fail.error_type, last_fail.error_msg),
                after_attempts=i - fail_streak_start,  # failed attempts before the fix
                edit_ratio=r.edit_ratio,
                seconds=round(r.ts - runs[fail_streak_start].ts, 1),
                viz_before=any(runs[fail_streak_start].ts <= v.ts <= r.ts for v in viz),
            ))
        fail_streak_start = None
    viz_helped = False
    for v in viz:
        if any(not r.ok for r in runs if r.ts <= v.ts):
            after = [r for r in runs if r.ts > v.ts][:3]
            if any(r.ok for r in after):
                viz_helped = True
                break

    return TaskSummary(
        task=name,
        attempts=len(runs),
        failures=len(fails),
        solved=first_ok is not None,
        final_ok=bool(runs and runs[-1].ok),
        first_success_attempt=None if first_ok is None else first_ok + 1,
        time_on_task=round(t_end - t_start, 1),
        time_to_first_success=None if first_ok is None else round(runs[first_ok].ts - t_start, 1),
        panic_score=score,
        panic_level=_level(score),
        error_clusters=ordered,
        stuck_points=stuck,
        transitions=transitions,
        confusions=[e.text or "" for e in confusions],
        viz_steps=len(viz),
        viz_helped=viz_helped,
        digest=_digest(events, t0, cfg),
    )


def compress(source: Union["Session", Iterable[Event], Iterable[Dict[str, Any]]],
             config: Optional[CompressorConfig] = None) -> CompressedTrajectory:
    """Compress a Session (or a list of events / event dicts) into a CompressedTrajectory."""
    cfg = config or CompressorConfig()
    session_id, metadata, started = "adhoc", {}, None
    if hasattr(source, "events"):
        session_id = source.session_id
        metadata = dict(source.metadata)
        started = source.started_at
        events = list(source.events)
    else:
        events = [e if isinstance(e, Event) else Event.from_dict(e) for e in source]
    events.sort(key=lambda e: e.ts)
    t0 = started if started is not None else (events[0].ts if events else 0.0)

    by_task: Dict[str, List[Event]] = {}
    for e in events:
        if e.kind == TASK:
            continue
        by_task.setdefault(e.task or "default", []).append(e)
    tasks = [_analyze_task(name, evs, t0, cfg) for name, evs in by_task.items() if evs]

    runs = [e for e in events if e.kind == RUN]
    all_errors = Counter(error_signature(e.error_type, e.error_msg) for e in runs if not e.ok)
    totals = {
        "tasks": len(tasks),
        "runs": len(runs),
        "failures": sum(1 for e in runs if not e.ok),
        "solved": sum(1 for t in tasks if t.solved),
        "confusions": sum(len(t.confusions) for t in tasks),
        "viz_steps": sum(t.viz_steps for t in tasks),
        "top_errors": all_errors.most_common(3),
    }
    duration = (events[-1].ts - t0) if events else 0.0
    ct = CompressedTrajectory(session_id, metadata, round(duration, 1), totals, tasks)
    raw_bytes = len(json.dumps([e.to_dict() for e in events], ensure_ascii=False).encode())
    comp_bytes = len(ct.to_prompt().encode())
    ct.stats = {"raw_events": len(events), "raw_bytes": raw_bytes, "compressed_bytes": comp_bytes,
                "ratio": round(raw_bytes / comp_bytes, 1) if comp_bytes else None}
    return ct
