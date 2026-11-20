"""Integration adapters: Moodle, OPT_Mentor, Hermes (+ offline fallback).

All adapters use the standard library only. Field names for Moodle and
OPT_Mentor are deliberately simple and configurable: when the real API
contracts are known, only this module changes.
"""
from __future__ import annotations

import csv
import io
import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from ._version import __version__
from .compressor import CompressedTrajectory, TaskSummary, _fmt_clock, _fmt_dur, _pct


# =================================================================== Moodle
class MoodleAdapter:
    """Maps a compressed trajectory onto Moodle quiz-attempt metadata.

    ``question_map`` maps stucktrace task names to Moodle question slots,
    e.g. ``{"sum_evens": 1, "fizzbuzz": 2}``.
    """

    CSV_FIELDS = ["userid", "courseid", "quizid", "attempt", "slot", "task", "attempts", "failures",
                  "solved", "first_success_attempt", "time_spent_sec", "panic_level", "panic_score",
                  "top_error", "confusions"]

    def __init__(self, course_id: Any = None, quiz_id: Any = None, user_id: Any = None,
                 attempt: Any = None, question_map: Optional[Dict[str, int]] = None) -> None:
        self.course_id, self.quiz_id, self.user_id, self.attempt = course_id, quiz_id, user_id, attempt
        self.question_map = question_map or {}

    @classmethod
    def from_attempt(cls, meta: Dict[str, Any], question_map: Optional[Dict[str, int]] = None) -> "MoodleAdapter":
        """Build from Moodle-style attempt metadata (e.g. mod_quiz attempt / LTI launch params)."""
        g = lambda *keys: next((meta[k] for k in keys if k in meta), None)  # noqa: E731
        return cls(course_id=g("courseid", "course_id", "context_id"),
                   quiz_id=g("quizid", "quiz_id", "cmid", "resource_link_id"),
                   user_id=g("userid", "user_id"),
                   attempt=g("attempt", "attemptid", "attempt_id"),
                   question_map=question_map)

    def apply_to(self, session: Any) -> None:
        session.metadata["moodle"] = self._ids()
        if self.user_id is not None:
            session.metadata.setdefault("student_id", str(self.user_id))

    def _ids(self) -> Dict[str, Any]:
        return {k: v for k, v in dict(courseid=self.course_id, quizid=self.quiz_id,
                                      userid=self.user_id, attempt=self.attempt).items() if v is not None}

    def _rows(self, ctx: CompressedTrajectory) -> List[Dict[str, Any]]:
        rows = []
        for i, t in enumerate(ctx.tasks, start=1):
            rows.append({
                "userid": self.user_id, "courseid": self.course_id, "quizid": self.quiz_id,
                "attempt": self.attempt, "slot": self.question_map.get(t.task, i), "task": t.task,
                "attempts": t.attempts, "failures": t.failures, "solved": int(t.solved),
                "first_success_attempt": t.first_success_attempt, "time_spent_sec": t.time_on_task,
                "panic_level": t.panic_level, "panic_score": t.panic_score, "top_error": t.top_error or "",
                "confusions": " | ".join(t.confusions),
            })
        return rows

    def export_attempt_json(self, ctx: CompressedTrajectory) -> Dict[str, Any]:
        return {"source": "stucktrace", "version": __version__, "session_id": ctx.session_id,
                "moodle": self._ids(), "totals": {k: v for k, v in ctx.totals.items() if k != "top_errors"},
                "questions": self._rows(ctx)}

    def export_csv(self, ctx: CompressedTrajectory, path: Optional[str] = None) -> str:
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=self.CSV_FIELDS)
        w.writeheader()
        for r in self._rows(ctx):
            w.writerow(r)
        text = buf.getvalue()
        if path:
            with open(path, "w", encoding="utf-8", newline="") as fh:
                fh.write(text)
        return text


# =============================================================== OPT_Mentor
class OPTMentorAdapter:
    """Imports visualization / tracing steps.

    Accepts Online-Python-Tutor-style traces::

        {"code": "...", "trace": [{"event": "step_line", "line": 3, "func_name": "f",
                                   "stack_to_render": [...], "exception_msg": "..."}]}

    One trace becomes ONE summarised ``viz`` event (steps, lines visited,
    max stack depth, exception) – raw steps are not stored, which keeps the
    log small. Use ``session.log_viz`` for per-step logging.
    """

    def import_trace(self, trace: Any, session: Any = None, task: Optional[str] = None) -> Optional[Dict[str, Any]]:
        from .tracker import get_session
        try:
            if isinstance(trace, (str, bytes)):
                trace = json.loads(trace)
            steps = trace.get("trace", trace if isinstance(trace, list) else [])
            lines = [s.get("line") for s in steps if isinstance(s, dict) and s.get("line") is not None]
            depth = max((len(s.get("stack_to_render") or []) for s in steps if isinstance(s, dict)), default=0)
            exc = next((s.get("exception_msg") for s in steps if isinstance(s, dict) and s.get("exception_msg")), None)
            summary = {"source": "opt_mentor", "steps": len(steps), "unique_lines": len(set(lines)),
                       "max_stack_depth": depth}
            if exc:
                summary["exception"] = str(exc)[:200]
            (session or get_session()).log_viz(event="trace_imported", task=task,
                                               note=f"OPT_Mentor trace, {len(steps)} steps", **summary)
            return summary
        except Exception:
            return None

    @staticmethod
    def export_steps(session: Any) -> List[Dict[str, Any]]:
        return [e.to_dict() for e in session.events if e.kind == "viz"]


# =================================================================== Hermes
class HermesError(RuntimeError):
    pass


_SYSTEM = {
    "student": (
        "You are a supportive programming tutor writing a SELF-REFLECTION report for a student, "
        "based on a compressed telemetry log of how they worked. Rules: never give the solution or "
        "corrected code; point to concepts, habits and questions to ask themselves; be specific about "
        "where they got stuck and which error patterns repeated; acknowledge what worked; end with 3 "
        "concrete things to try next time. Use Markdown with short sections. Max 250 words."
    ),
    "instructor": (
        "You are a teaching assistant writing a DIAGNOSTIC report for an instructor, based on a compressed "
        "telemetry log of one student's session. Be factual and concise: rank tasks by difficulty, explain "
        "stuck/panic points with timestamps, name the misconceptions the error clusters suggest, note whether "
        "visualization helped, and propose 2-3 targeted interventions. Use Markdown with a summary table. "
        "Max 350 words."
    ),
}


class HermesClient:
    """Minimal client for a Hermes LLM endpoint.

    Assumes an OpenAI-compatible ``/chat/completions`` contract (the common
    way Hermes models are served). Configure via arguments or env vars
    ``HERMES_API_URL``, ``HERMES_API_KEY``, ``HERMES_MODEL``.
    """

    def __init__(self, api_url: Optional[str] = None, api_key: Optional[str] = None,
                 model: Optional[str] = None, timeout: float = 12.0) -> None:
        self.api_url = api_url or os.environ.get("HERMES_API_URL")
        self.api_key = api_key or os.environ.get("HERMES_API_KEY")
        self.model = model or os.environ.get("HERMES_MODEL", "hermes")
        self.timeout = timeout

    @property
    def available(self) -> bool:
        return bool(self.api_url)

    def build_messages(self, ctx: CompressedTrajectory, mode: str = "student", lang: str = "en") -> List[Dict[str, str]]:
        language = "Russian" if lang == "ru" else "English"
        system = _SYSTEM["instructor" if mode == "instructor" else "student"] + f" Write in {language}."
        return [{"role": "system", "content": system},
                {"role": "user", "content": "Compressed trajectory:\n" + ctx.to_prompt()}]

    def complete(self, messages: List[Dict[str, str]]) -> str:
        if not self.api_url:
            raise HermesError("HERMES_API_URL is not configured")
        body = json.dumps({"model": self.model, "messages": messages, "temperature": 0.3}).encode()
        headers = {"Content-Type": "application/json", "User-Agent": f"stucktrace/{__version__}"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(self.api_url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise HermesError(f"Hermes request failed: {exc}") from exc
        text = _extract_text(payload)
        if not text:
            raise HermesError("Hermes returned an empty / unknown response shape")
        return text

    def report(self, ctx: CompressedTrajectory, mode: str = "student", lang: str = "en") -> str:
        return self.complete(self.build_messages(ctx, mode, lang))


def _extract_text(payload: Any) -> Optional[str]:
    if not isinstance(payload, dict):
        return None
    try:
        return payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        pass
    for key in ("output", "text", "response", "content", "report"):
        v = payload.get(key)
        if isinstance(v, str) and v.strip():
            return v
    return None


# ===================================================== Offline report engine
_HINTS = {
    "en": {
        "NameError": "a name is used before it exists — check spelling and where the variable is first assigned",
        "TypeError": "two values of incompatible types meet — print the type() of each operand before the failing line",
        "IndexError": "an index goes past the end — trace the loop bounds for the first and last iteration",
        "KeyError": "a dict key is missing — print the keys you actually have",
        "SyntaxError": "Python cannot parse the line — look at brackets, colons and quotes just before the arrow",
        "IndentationError": "the block structure is off — check which lines belong inside the loop/if",
        "ValueError": "the type is right but the value is not — check what input reaches the conversion",
        "ZeroDivisionError": "a divisor can be zero — which case did you not handle?",
        "AttributeError": "the object is not the type you think — print type(obj) and dir(obj)",
        "RecursionError": "the recursion never reaches its base case — write down the base case first",
        "UnboundLocalError": "a variable is assigned inside the function, so Python treats it as local",
        "AssertionError": "a test expectation failed — compare expected vs actual on the smallest input",
        "ModuleNotFoundError": "the import name or environment is wrong",
        "ImportError": "the import name or environment is wrong",
    },
    "ru": {
        "NameError": "имя используется до того, как оно создано — проверь написание и место первого присваивания",
        "TypeError": "встретились значения несовместимых типов — выведи type() каждого операнда перед строкой с ошибкой",
        "IndexError": "индекс выходит за границу — проследи границы цикла на первой и последней итерации",
        "KeyError": "в словаре нет ключа — выведи ключи, которые реально есть",
        "SyntaxError": "Python не может разобрать строку — смотри скобки, двоеточия и кавычки перед стрелкой",
        "IndentationError": "сломана структура блоков — проверь, какие строки должны быть внутри цикла/if",
        "ValueError": "тип верный, но значение нет — проверь, что приходит в преобразование",
        "ZeroDivisionError": "делитель может быть нулём — какой случай не обработан?",
        "AttributeError": "объект не того типа, что ты думаешь — выведи type(obj) и dir(obj)",
        "RecursionError": "рекурсия не доходит до базового случая — сначала запиши базовый случай",
        "UnboundLocalError": "переменная присваивается внутри функции, поэтому Python считает её локальной",
        "AssertionError": "тест не прошёл — сравни ожидаемое и фактическое на самом маленьком входе",
        "ModuleNotFoundError": "неверное имя модуля или окружение",
        "ImportError": "неверное имя модуля или окружение",
    },
}

_T = {
    "en": {
        "s_title": "## 🪞 Self-Reflection Report",
        "i_title": "## 🧭 Instructor Diagnostic Report",
        "overview": "**Session:** {tasks} task(s) · {runs} runs · {fails} failed · {dur}",
        "stuck_h": "### Where you got stuck",
        "no_stuck": "- No real stuck points — steady work. 👏",
        "patterns_h": "### Patterns you repeated",
        "no_patterns": "- No repeated error patterns.",
        "worked_h": "### What worked",
        "next_h": "### Try next time",
        "pressure": {"low": "low pressure", "medium": "some pressure", "high": "high pressure"},
        "unsolved": "not solved yet",
        "you_flagged": "you flagged: {t}",
        "fix": "On **{task}** you fixed `{err}` after {n} failed attempt(s) with a {size} edit.",
        "small": "small", "large": "large",
        "viz": "On **{task}** the visualizer helped: success came within a few runs after tracing.",
        "solved_first": "**{task}** worked on the first try.",
        "no_worked": "- You kept going — that is the part that counts. Next time, look for one small win per task.",
        "tip_burst": "When a run fails, stop for 30 seconds and read the *whole* traceback before re-running.",
        "tip_repeat": "If the same error comes back, write down (or say out loud) what you *expect* that line to do.",
        "tip_rewrite": "Change one thing at a time — big rewrites after an error hide what actually fixed it.",
        "tip_pause": "If you are silent for minutes, ask for help or open the visualizer earlier.",
        "tip_confusion": "Turn \"{t}\" into a concrete question and try the smallest possible example.",
        "tip_viz": "Keep using step-by-step visualization — it worked for you.",
        "tip_default": "Test your code on the smallest input you can think of before the full one.",
        "who": "**Student:** {sid} · **Course:** {course} · **Session:** `{sess}` · {dur}",
        "table": "| Task | Runs | Solved | 1st success | Time | Panic | Top error |\n|---|---|---|---|---|---|---|",
        "hot_h": "### Difficulty hotspots (ranked by panic score)",
        "clusters_h": "### Error clusters",
        "trans_h": "### Key transitions (error → fix)",
        "conf_h": "### Student-flagged confusion",
        "none": "- none",
        "interv_h": "### Suggested interventions",
        "iv_burst": "**{task}**: trial-and-error bursts — model a debugging routine (read traceback → hypothesis → one change).",
        "iv_repeat": "**{task}**: persistent `{etype}` — likely misconception; a 2-minute targeted explanation is worth it.",
        "iv_unsolved": "**{task}**: not solved — consider a scaffolded hint or a worked example of a *similar* task.",
        "iv_conf": "**{task}**: the student explicitly asked for help (\"{t}\") — follow up personally.",
        "iv_viz": "Visualization shortened time-to-fix on **{task}** — recommend it for similar exercises.",
        "iv_default": "No intervention needed — the session looks healthy.",
        "signals": {"rapid_fail_burst": "rapid-fail burst", "repeated_error": "repeated error",
                    "long_pause": "long pause", "rewrite": "rewrite after error", "confusion": "confusion marker"},
        "viz_note": "visualizer used {n}× (helped: {h})",
        "detail": {"rapid_fail_burst": "{n} failed runs in {span}s", "repeated_error": "same {etype} {n}× in a row",
                   "long_pause": "{secs}s of silence after {etype}", "rewrite": "{pct} of the code rewritten after {etype}",
                   "confusion": "\"{text}\""},
        "cluster": "×{count} `{sig}` — {task}, attempts {a}–{b}, max streak {s}, {res}",
        "resolved": "resolved", "unresolved": "UNRESOLVED",
        "trans": "**{task}**: `{err}` fixed after {n} failed attempt(s), {secs}s, edit {pct}{viz}",
        "after_viz": " · after visualization",
        "yes": "yes", "no": "no",
    },
    "ru": {
        "s_title": "## 🪞 Отчёт для самоанализа",
        "i_title": "## 🧭 Диагностический отчёт для преподавателя",
        "overview": "**Сессия:** задач: {tasks} · запусков: {runs} · неудачных: {fails} · {dur}",
        "stuck_h": "### Где ты застрял",
        "no_stuck": "- Явных застреваний нет — ровная работа. 👏",
        "patterns_h": "### Повторяющиеся паттерны ошибок",
        "no_patterns": "- Повторяющихся ошибок нет.",
        "worked_h": "### Что сработало",
        "next_h": "### Что попробовать в следующий раз",
        "pressure": {"low": "спокойно", "medium": "заметное напряжение", "high": "сильное напряжение"},
        "unsolved": "пока не решено",
        "you_flagged": "ты отметил: {t}",
        "fix": "В **{task}** ты исправил `{err}` после неудачных попыток: {n} — {size} правкой.",
        "small": "небольшой", "large": "крупной",
        "viz": "В **{task}** помог визуализатор: успех пришёл через пару запусков после трассировки.",
        "solved_first": "**{task}** получилось с первой попытки.",
        "no_worked": "- Ты не бросил — это главное. В следующий раз ищи одну маленькую победу на задачу.",
        "tip_burst": "После ошибки остановись на 30 секунд и прочитай traceback *целиком*, прежде чем перезапускать.",
        "tip_repeat": "Если ошибка повторяется, запиши (или проговори), что *должна* делать эта строка.",
        "tip_rewrite": "Меняй по одной вещи за раз — большие переписывания скрывают, что именно помогло.",
        "tip_pause": "Если долго молчишь — раньше проси помощи или открывай визуализатор.",
        "tip_confusion": "Преврати «{t}» в конкретный вопрос и попробуй самый маленький пример.",
        "tip_viz": "Продолжай пользоваться пошаговой визуализацией — она тебе помогла.",
        "tip_default": "Проверяй код на самом маленьком входе перед полным.",
        "who": "**Студент:** {sid} · **Курс:** {course} · **Сессия:** `{sess}` · {dur}",
        "table": "| Задача | Запуски | Решена | 1-й успех | Время | Паника | Главная ошибка |\n|---|---|---|---|---|---|---|",
        "hot_h": "### Точки затруднения (по индексу паники)",
        "clusters_h": "### Кластеры ошибок",
        "trans_h": "### Ключевые переходы (ошибка → исправление)",
        "conf_h": "### Явные маркеры непонимания",
        "none": "- нет",
        "interv_h": "### Рекомендуемые действия",
        "iv_burst": "**{task}**: серии проб и ошибок — покажите алгоритм отладки (traceback → гипотеза → одно изменение).",
        "iv_repeat": "**{task}**: устойчивая `{etype}` — вероятное заблуждение; стоит 2-минутное точечное объяснение.",
        "iv_unsolved": "**{task}**: не решена — дайте поэтапную подсказку или разбор *похожей* задачи.",
        "iv_conf": "**{task}**: студент явно просил помощи («{t}») — свяжитесь лично.",
        "iv_viz": "Визуализация ускорила исправление в **{task}** — рекомендуйте её для похожих упражнений.",
        "iv_default": "Вмешательство не требуется — сессия выглядит здоровой.",
        "signals": {"rapid_fail_burst": "серия быстрых неудач", "repeated_error": "повтор ошибки",
                    "long_pause": "долгая пауза", "rewrite": "переписывание после ошибки", "confusion": "маркер непонимания"},
        "viz_note": "визуализатор: {n}× (помог: {h})",
        "detail": {"rapid_fail_burst": "неудачных запусков подряд: {n} за {span} с", "repeated_error": "одна и та же {etype} {n}× подряд",
                   "long_pause": "{secs} с тишины после {etype}", "rewrite": "переписано {pct} кода после {etype}",
                   "confusion": "«{text}»"},
        "cluster": "×{count} `{sig}` — {task}, попытки {a}–{b}, макс. серия {s}, {res}",
        "resolved": "исправлена", "unresolved": "НЕ ИСПРАВЛЕНА",
        "trans": "**{task}**: `{err}` исправлена после неудачных попыток: {n}, {secs} с, правка {pct}{viz}",
        "after_viz": " · после визуализации",
        "yes": "да", "no": "нет",
    },
}


class LocalReportGenerator:
    """Deterministic, rule-based report writer. Works fully offline — the demo always runs."""

    def generate(self, ctx: CompressedTrajectory, mode: str = "student", lang: str = "en") -> str:
        lang = lang if lang in _T else "en"
        return self._instructor(ctx, lang) if mode == "instructor" else self._student(ctx, lang)

    # ---------------------------------------------------------- student
    def _student(self, ctx: CompressedTrajectory, lang: str) -> str:
        T, H = _T[lang], _HINTS[lang]
        tt = ctx.totals
        out = [T["s_title"], T["overview"].format(tasks=tt["tasks"], runs=tt["runs"], fails=tt["failures"],
                                                 dur=_fmt_dur(ctx.duration)), "", T["stuck_h"]]
        stuck_tasks = sorted([t for t in ctx.tasks if t.stuck_points or not t.solved],
                             key=lambda t: -t.panic_score)
        if not stuck_tasks:
            out.append(T["no_stuck"])
        for t in stuck_tasks:
            bits = [self._signal_text(s, T) for s in self._key_signals(t)]
            if not t.solved:
                bits.append(T["unsolved"])
            out.append(f"- **{t.task}** — {T['pressure'][t.panic_level]}: " + "; ".join(bits))

        out += ["", T["patterns_h"]]
        clusters = [(t, c) for t in ctx.tasks for c in t.error_clusters if c.count >= 2]
        clusters.sort(key=lambda tc: -tc[1].count)
        if not clusters:
            out.append(T["no_patterns"])
        seen = set()
        for t, c in clusters[:4]:
            hint = H.get(c.error_type, "")
            out.append(f"- `{c.error_type}` ×{c.count} ({t.task})" + (f" — {hint}" if hint and c.error_type not in seen else ""))
            seen.add(c.error_type)

        out += ["", T["worked_h"]]
        worked = []
        for t in ctx.tasks:
            if t.first_success_attempt == 1:
                worked.append(T["solved_first"].format(task=t.task))
            for tr in t.transitions[:1]:
                size = T["small"] if (tr.edit_ratio or 0) < 0.3 else T["large"]
                worked.append(T["fix"].format(task=t.task, err=tr.fixed_error.split(":")[0], n=tr.after_attempts, size=size))
            if t.viz_helped:
                worked.append(T["viz"].format(task=t.task))
        out += [f"- {w}" for w in worked[:4]] or [T["no_worked"]]

        out += ["", T["next_h"]]
        out += [f"{i}. {tip}" for i, tip in enumerate(self._tips(ctx, T), start=1)]
        return "\n".join(out)

    def _key_signals(self, t: TaskSummary):
        picked, kinds = [], set()
        for s in t.stuck_points:
            if s.kind not in kinds:
                picked.append(s)
                kinds.add(s.kind)
        return picked[:4]

    @staticmethod
    def _detail(s, T) -> str:
        try:
            return T["detail"][s.kind].format(**s.data)
        except (KeyError, IndexError):
            return s.detail

    def _signal_text(self, s, T) -> str:
        if s.kind == "confusion":
            return T["you_flagged"].format(t=self._detail(s, T))
        return self._detail(s, T)

    def _tips(self, ctx: CompressedTrajectory, T) -> List[str]:
        kinds = {s.kind for t in ctx.tasks for s in t.stuck_points}
        tips = []
        if "rapid_fail_burst" in kinds:
            tips.append(T["tip_burst"])
        if "repeated_error" in kinds:
            tips.append(T["tip_repeat"])
        if "rewrite" in kinds:
            tips.append(T["tip_rewrite"])
        if "long_pause" in kinds:
            tips.append(T["tip_pause"])
        conf = next((c for t in ctx.tasks for c in t.confusions if c), None)
        if conf:
            tips.append(T["tip_confusion"].format(t=conf))
        if any(t.viz_helped for t in ctx.tasks):
            tips.append(T["tip_viz"])
        if len(tips) < 3:
            tips.append(T["tip_default"])
        return tips[:3] if len(tips) > 3 else tips

    # ------------------------------------------------------- instructor
    def _instructor(self, ctx: CompressedTrajectory, lang: str) -> str:
        T = _T[lang]
        md = ctx.metadata
        out = [T["i_title"],
               T["who"].format(sid=md.get("student_id", "—"), course=md.get("course", "—"),
                               sess=ctx.session_id, dur=_fmt_dur(ctx.duration)),
               "", T["table"]]
        for t in ctx.tasks:
            out.append(f"| {t.task} | {t.attempts} | {'✅' if t.solved else '❌'} | {t.first_success_attempt or '—'} | "
                       f"{_fmt_dur(t.time_on_task)} | {t.panic_level} ({t.panic_score:.2f}) | "
                       f"{(t.top_error or '—').replace('|', '/')[:60]} |")

        out += ["", T["hot_h"]]
        for t in sorted(ctx.tasks, key=lambda t: -t.panic_score):
            if not t.stuck_points:
                continue
            out.append(f"- **{t.task}** — {t.panic_level} ({t.panic_score:.2f}); "
                       + T["viz_note"].format(n=t.viz_steps, h=T["yes"] if t.viz_helped else T["no"]))
            for s in t.stuck_points:
                out.append(f"  - `+{_fmt_clock(s.t)}` {T['signals'][s.kind]}: {self._detail(s, T)}")
            out.append(f"  - digest: `{t.digest}`")
        if not any(t.stuck_points for t in ctx.tasks):
            out.append(T["none"])

        out += ["", T["clusters_h"]]
        rows = [(t, c) for t in ctx.tasks for c in t.error_clusters]
        rows.sort(key=lambda tc: -tc[1].count)
        out += ["- " + T["cluster"].format(count=c.count, sig=c.signature, task=t.task, a=c.first_attempt,
                                           b=c.last_attempt, s=c.max_streak,
                                           res=T["resolved"] if c.resolved else T["unresolved"])
                for t, c in rows[:8]] or [T["none"]]

        out += ["", T["trans_h"]]
        out += ["- " + T["trans"].format(task=t.task, err=tr.fixed_error, n=tr.after_attempts,
                                         secs=f"{tr.seconds:.0f}", pct=_pct(tr.edit_ratio),
                                         viz=T["after_viz"] if tr.viz_before else "")
                for t in ctx.tasks for tr in t.transitions] or [T["none"]]

        out += ["", T["conf_h"]]
        out += [f"- **{t.task}**: “{c}”" for t in ctx.tasks for c in t.confusions if c] or [T["none"]]

        out += ["", T["interv_h"]]
        iv = []
        for t in sorted(ctx.tasks, key=lambda t: -t.panic_score):
            kinds = {s.kind for s in t.stuck_points}
            if "repeated_error" in kinds and t.error_clusters:
                iv.append(T["iv_repeat"].format(task=t.task, etype=t.error_clusters[0].error_type))
            elif "rapid_fail_burst" in kinds:
                iv.append(T["iv_burst"].format(task=t.task))
            if not t.solved:
                iv.append(T["iv_unsolved"].format(task=t.task))
            conf = next((c for c in t.confusions if c), None)
            if conf:
                iv.append(T["iv_conf"].format(task=t.task, t=conf))
            if t.viz_helped:
                iv.append(T["iv_viz"].format(task=t.task))
        out += [f"{i}. {x}" for i, x in enumerate(iv[:5], start=1)] or [f"1. {T['iv_default']}"]
        return "\n".join(out)

