"""Event collection: sessions, Jupyter hooks, decorator / context manager.

Design rule #1: telemetry must NEVER break student code. Every public entry
point is wrapped in ``_safe`` and swallows its own internal errors (they are
logged at DEBUG level on the ``stucktrace`` logger). Student exceptions are
recorded and then re-raised untouched.
"""
from __future__ import annotations

import functools
import hashlib
import inspect
import json
import logging
import re
import threading
import time
import uuid
from difflib import SequenceMatcher
from typing import Any, Callable, Dict, List, Optional

from .events import CONFUSION, NOTE, RUN, TASK, VIZ, Event

log = logging.getLogger("stucktrace")

_MAX_DIFF_CHARS = 8000


def _safe(default: Any = None) -> Callable:
    def deco(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return fn(*args, **kwargs)
            except Exception as exc:  # pragma: no cover - defensive by design
                log.debug("stucktrace internal error in %s: %r", fn.__name__, exc)
                return default
        return wrapper
    return deco


class Session:
    """An in-memory trajectory of one student working on one or more tasks."""

    def __init__(
        self,
        student_id: Optional[str] = None,
        course: Optional[str] = None,
        store_code: bool = True,
        preview_chars: int = 240,
        autosave: Optional[str] = None,
        clock: Optional[Callable[[], float]] = None,
        max_events: int = 20000,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.session_id = uuid.uuid4().hex[:12]
        self.clock = clock or time.time
        self.started_at = self.clock()
        self.events: List[Event] = []
        self.current_task: Optional[str] = None
        self.store_code = store_code
        self.preview_chars = preview_chars
        self.autosave = autosave
        self.max_events = max_events
        self.metadata: Dict[str, Any] = dict(metadata or {})
        if student_id:
            self.metadata["student_id"] = student_id
        if course:
            self.metadata["course"] = course
        self._last_code: Dict[str, str] = {}
        self._last_task: Optional[str] = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ core
    def _add(self, ev: Event) -> Event:
        with self._lock:
            if len(self.events) >= self.max_events:
                self.events.pop(0)
            self.events.append(ev)
        if self.autosave:
            try:
                with open(self.autosave, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(ev.to_dict(), ensure_ascii=False) + "\n")
            except OSError as exc:
                log.debug("autosave failed: %r", exc)
        return ev

    def _task(self, task: Optional[str]) -> str:
        # explicit > current task > task of the last recorded run (e.g. a @track'ed function)
        return task or self.current_task or self._last_task or "default"

    @_safe()
    def set_task(self, name: str) -> None:
        self.current_task = str(name)
        self._add(Event(TASK, self.clock(), task=self.current_task))

    @_safe()
    def record_run(
        self,
        code: Optional[str] = None,
        ok: bool = True,
        exc: Optional[BaseException] = None,
        duration: Optional[float] = None,
        task: Optional[str] = None,
        error_type: Optional[str] = None,
        error_msg: Optional[str] = None,
    ) -> Optional[Event]:
        t = self._task(task)
        self._last_task = t
        ev = Event(RUN, self.clock(), task=t, ok=bool(ok))
        if duration is not None:
            ev.duration = round(float(duration), 4)
        if exc is not None:
            ev.ok = False
            ev.error_type = type(exc).__name__
            ev.error_msg = str(exc)[:300]
        elif error_type:
            ev.ok = False
            ev.error_type = error_type
            ev.error_msg = (error_msg or "")[:300]
        if code is not None:
            ev.code_hash = hashlib.sha1(code.encode("utf-8", "replace")).hexdigest()[:10]
            ev.code_len = len(code)
            prev = self._last_code.get(t)
            if prev is not None:
                ratio = SequenceMatcher(None, prev[:_MAX_DIFF_CHARS], code[:_MAX_DIFF_CHARS]).ratio()
                ev.edit_ratio = round(1.0 - ratio, 3)
            self._last_code[t] = code
            if self.store_code:
                ev.code_preview = code[: self.preview_chars]
        return self._add(ev)

    @_safe()
    def mark_confusion(self, text: str = "", task: Optional[str] = None) -> Optional[Event]:
        return self._add(Event(CONFUSION, self.clock(), task=self._task(task), text=str(text)[:300]))

    @_safe()
    def log_viz(
        self,
        step: Optional[int] = None,
        line: Optional[int] = None,
        event: Optional[str] = None,
        note: Optional[str] = None,
        task: Optional[str] = None,
        **data: Any,
    ) -> Optional[Event]:
        payload = {k: v for k, v in dict(step=step, line=line, event=event, **data).items() if v is not None}
        return self._add(Event(VIZ, self.clock(), task=self._task(task), text=note, data=payload))

    @_safe()
    def note(self, text: str, task: Optional[str] = None) -> Optional[Event]:
        return self._add(Event(NOTE, self.clock(), task=self._task(task), text=str(text)[:500]))

    def track(self, task: Optional[str] = None, code: Optional[str] = None) -> "track":
        return track(task=task, code=code, session=self)

    # ------------------------------------------------------------ persistence
    def to_dicts(self) -> List[Dict[str, Any]]:
        return [e.to_dict() for e in self.events]

    def header(self) -> Dict[str, Any]:
        return {"session_id": self.session_id, "started_at": self.started_at, "metadata": self.metadata}

    @_safe(default=False)
    def save(self, path: str) -> bool:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"_session": self.header()}, ensure_ascii=False) + "\n")
            for e in self.events:
                fh.write(json.dumps(e.to_dict(), ensure_ascii=False) + "\n")
        return True

    @classmethod
    def load(cls, path: str) -> "Session":
        s = cls()
        first_ts: Optional[float] = None
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                d = json.loads(line)
                if "_session" in d:
                    h = d["_session"]
                    s.session_id = h.get("session_id", s.session_id)
                    s.started_at = h.get("started_at", s.started_at)
                    s.metadata = h.get("metadata", {})
                    first_ts = s.started_at
                    continue
                ev = Event.from_dict(d)
                if first_ts is None:
                    first_ts = ev.ts
                    s.started_at = ev.ts
                s.events.append(ev)
        return s

    # -------------------------------------------------------------- shortcuts
    def compress(self, **kwargs: Any):
        from .compressor import compress
        return compress(self, **kwargs)

    def report(self, mode: str = "student", **kwargs: Any):
        from .reporter import generate_report
        return generate_report(mode=mode, session=self, **kwargs)

    def __repr__(self) -> str:
        runs = sum(1 for e in self.events if e.kind == RUN)
        return f"<stucktrace.Session {self.session_id} events={len(self.events)} runs={runs}>"


class track:
    """Decorator *and* context manager that records one run.

    >>> @track("fizzbuzz")
    ... def solve(): ...

    >>> with track("loops", code=src):
    ...     exec(src)
    """

    def __init__(self, task: Optional[str] = None, code: Optional[str] = None,
                 session: Optional[Session] = None) -> None:
        fn = None
        if callable(task) and not isinstance(task, str):  # used as bare @track
            fn, task = task, None
        self.task = task
        self.code = code
        self.session = session
        self._t0 = 0.0
        self._wrapped = self._wrap(fn) if fn is not None else None
        if fn is not None:
            functools.update_wrapper(self, fn)

    def _session(self) -> Session:
        return self.session or get_session()

    def __enter__(self) -> "track":
        self._t0 = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        try:
            self._session().record_run(code=self.code, ok=exc is None, exc=exc,
                                       duration=time.perf_counter() - self._t0, task=self.task)
        except Exception as e:  # pragma: no cover
            log.debug("track.__exit__ failed: %r", e)
        return False  # never swallow the student's exception

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        if self._wrapped is not None:  # bare @track usage: forward the call
            return self._wrapped(*args, **kwargs)
        return self._wrap(args[0])

    def _wrap(self, fn: Callable) -> Callable:
        task = self.task or getattr(fn, "__name__", "default")
        try:
            src: Optional[str] = inspect.getsource(fn)
        except (OSError, TypeError):
            src = None
        owner = self

        @functools.wraps(fn)
        def wrapper(*a: Any, **kw: Any) -> Any:
            with track(task=task, code=src, session=owner.session):
                return fn(*a, **kw)
        return wrapper


# ---------------------------------------------------------------- global API
_current: Optional[Session] = None


def get_session() -> Session:
    """Return the active session, creating a default one if needed."""
    global _current
    if _current is None:
        _current = Session()
    return _current


def start_session(student_id: Optional[str] = None, course: Optional[str] = None,
                  task: Optional[str] = None, jupyter: bool = True, **kwargs: Any) -> Session:
    """Start a new tracking session. In IPython/Jupyter, cell hooks are installed automatically."""
    global _current
    _current = Session(student_id=student_id, course=course, **kwargs)
    if task:
        _current.set_task(task)
    if jupyter:
        ip = _get_ipython()
        if ip is not None:
            install_jupyter_hooks(ip)
    return _current


def end_session(path: Optional[str] = None) -> Optional[Session]:
    global _current
    s = _current
    if s is not None and path:
        s.save(path)
    ip = _get_ipython()
    if ip is not None:
        uninstall_jupyter_hooks(ip)
    _current = None
    return s


def set_task(name: str) -> None:
    get_session().set_task(name)


def mark_confusion(text: str = "", task: Optional[str] = None) -> None:
    get_session().mark_confusion(text, task=task)


def log_viz(step: Optional[int] = None, line: Optional[int] = None, event: Optional[str] = None,
            note: Optional[str] = None, task: Optional[str] = None, **data: Any) -> None:
    get_session().log_viz(step=step, line=line, event=event, note=note, task=task, **data)


# ------------------------------------------------------------ Jupyter hooks
_INTERNAL = re.compile(
    r"^\s*(%|!|#|import\s+stucktrace|from\s+stucktrace|"
    r"(\w+\.)?(generate_report|mark_confusion|log_viz|set_task|start_session|end_session|init_session|run_demo)\()"
)


def _is_internal(code: Optional[str]) -> bool:
    """Cells that only drive stucktrace itself (magics, API calls) are not student attempts."""
    if not code or not code.strip():
        return True
    lines = [ln for ln in code.splitlines() if ln.strip()]
    return all(_INTERNAL.match(ln) for ln in lines)


def _get_ipython():
    try:
        from IPython import get_ipython  # type: ignore
        return get_ipython()
    except Exception:
        return None


class _JupyterHooks:
    def __init__(self) -> None:
        self._t0 = 0.0
        self._code: Optional[str] = None

    def pre_run_cell(self, info: Any = None) -> None:
        try:
            self._t0 = time.perf_counter()
            self._code = getattr(info, "raw_cell", None)
        except Exception:  # pragma: no cover
            pass

    def post_run_cell(self, result: Any) -> None:
        try:
            code = self._code
            if code is None:
                code = getattr(getattr(result, "info", None), "raw_cell", None)
            if _is_internal(code):
                return
            exc = getattr(result, "error_before_exec", None) or getattr(result, "error_in_exec", None)
            ok = bool(getattr(result, "success", exc is None)) and exc is None
            get_session().record_run(code=code, ok=ok, exc=exc,
                                     duration=time.perf_counter() - self._t0)
        except Exception as e:  # pragma: no cover
            log.debug("post_run_cell failed: %r", e)


def install_jupyter_hooks(ip: Any) -> bool:
    if getattr(ip, "_stucktrace_hooks", None) is not None:
        return True
    try:
        h = _JupyterHooks()
        ip.events.register("pre_run_cell", h.pre_run_cell)
        ip.events.register("post_run_cell", h.post_run_cell)
        ip._stucktrace_hooks = h
        _register_magics(ip)
        return True
    except Exception as e:
        log.debug("could not install Jupyter hooks: %r", e)
        return False


def uninstall_jupyter_hooks(ip: Any) -> None:
    h = getattr(ip, "_stucktrace_hooks", None)
    if h is None:
        return
    for name, cb in (("pre_run_cell", h.pre_run_cell), ("post_run_cell", h.post_run_cell)):
        try:
            ip.events.unregister(name, cb)
        except Exception:
            pass
    ip._stucktrace_hooks = None


_MAGIC_HELP = """%stucktrace usage:
  %stucktrace task <name>                    switch current task
  %stucktrace status                         short session summary
  %stucktrace report [student|instructor] [--offline] [--lang ru]
  %stucktrace save <path.jsonl>              save raw trajectory
  %confused <text>                           mark a confusion / panic point"""


def _register_magics(ip: Any) -> None:
    def stucktrace_magic(line: str = "") -> Any:
        parts = line.split()
        if not parts:
            print(_MAGIC_HELP)
            return None
        cmd, args = parts[0], parts[1:]
        if cmd == "task" and args:
            set_task(" ".join(args))
            print(f"stucktrace: task -> {' '.join(args)}")
        elif cmd == "status":
            s = get_session()
            runs = [e for e in s.events if e.kind == RUN]
            fails = sum(1 for e in runs if not e.ok)
            print(f"stucktrace: session {s.session_id}, task={s.current_task}, runs={len(runs)}, failed={fails}")
        elif cmd == "report":
            from .reporter import generate_report
            mode = next((a for a in args if a in ("student", "instructor")), "student")
            lang = args[args.index("--lang") + 1] if "--lang" in args and args.index("--lang") + 1 < len(args) else "en"
            backend = "offline" if "--offline" in args else "auto"
            return generate_report(mode=mode, backend=backend, lang=lang)
        elif cmd == "save" and args:
            ok = get_session().save(args[0])
            print(f"stucktrace: saved to {args[0]}" if ok else "stucktrace: save failed")
        else:
            print(_MAGIC_HELP)
        return None

    def confused_magic(line: str = "") -> None:
        mark_confusion(line.strip())
        print("stucktrace: confusion marked — noted, keep going.")

    try:
        ip.register_magic_function(stucktrace_magic, "line", "stucktrace")
        ip.register_magic_function(confused_magic, "line", "confused")
    except Exception as e:
        log.debug("magic registration failed: %r", e)
