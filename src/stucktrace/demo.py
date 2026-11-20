"""45-second end-to-end demo: a scripted student struggles, stucktrace explains it.

Run with ``python -m stucktrace demo`` (or ``stucktrace demo --lang ru``) or,
in a notebook, ``from stucktrace.demo import run_demo; run_demo()``.

The student's code attempts are REALLY executed (real exceptions are
captured); only the clock is simulated so that pauses of several minutes
fit into a short live demo.
"""
from __future__ import annotations

import time
from typing import Any, List, Optional, Tuple

from .adapters import MoodleAdapter, OPTMentorAdapter
from .reporter import generate_report
from .tracker import Session


class SimClock:
    def __init__(self, start: float = 1_700_000_000.0) -> None:
        self.t0 = start
        self.now = start

    def __call__(self) -> float:
        return self.now

    def at(self, seconds: float) -> None:
        self.now = self.t0 + seconds


DATA = 'data = "2 4 5 8 11 6".split()\n'

# (t_seconds, action, task, payload)
SCRIPT: List[Tuple[float, str, str, Any]] = [
    (0, "task", "sum_evens", None),
    (5, "run", "sum_evens", DATA + "total = 0\nfor n in dat:\n    if n % 2 == 0:\n        total += n\n"),
    (12, "run", "sum_evens", DATA + "total = 0\nfor n in data:\n    if n % 2 == 0:\n        total += n\n"),
    (17, "run", "sum_evens", DATA + "total = 0\nfor n in data:\n    if (n % 2) == 0:\n        total += n\n"),
    (21, "run", "sum_evens", DATA + "total = 0\nfor n in data:\n    if n % 2 == 0 :\n        total = total + n\n"),
    (170, "confusion", "sum_evens", "why does % not work on my numbers?"),
    (185, "viz", "sum_evens", {"trace": [
        {"event": "step_line", "line": 1, "func_name": "<module>", "stack_to_render": []},
        {"event": "step_line", "line": 3, "func_name": "<module>", "stack_to_render": []},
        {"event": "exception", "line": 4, "func_name": "<module>", "stack_to_render": [],
         "exception_msg": "TypeError: not all arguments converted during string formatting"}]}),
    (205, "run", "sum_evens", DATA + "total = 0\nfor n in data:\n    if int(n) % 2 == 0:\n        total += int(n)\n"),
    (240, "task", "count_vowels", None),
    (250, "run", "count_vowels", 's = "recursion"\ncount = 0\nfor i in range(len(s) + 1):\n    if s[i] in "aeiou":\n        count += 1\n'),
    (262, "run", "count_vowels", 's = "recursion"\ncount = sum(\n    1 for ch in s if ch in "aeiou"\n'),
    (281, "run", "count_vowels", 's = "recursion"\ncount = sum(1 for ch in s if ch in "aeiou")\n'),
    (300, "task", "fizzbuzz", None),
    (330, "run", "fizzbuzz", 'out = ["FizzBuzz" if i % 15 == 0 else "Fizz" if i % 3 == 0 else '
                             '"Buzz" if i % 5 == 0 else str(i) for i in range(1, 16)]\n'),
]


def build_demo_session(verbose: bool = True) -> Session:
    clock = SimClock()
    s = Session(student_id="s1024", course="CS1302 Intro to Programming", clock=clock)
    MoodleAdapter(course_id=1302, quiz_id=7, user_id="s1024", attempt=1).apply_to(s)
    opt = OPTMentorAdapter()
    for t, action, task, payload in SCRIPT:
        clock.at(t)
        if action == "task":
            s.set_task(task)
            if verbose:
                print(f"\n▶ task: {task}")
        elif action == "run":
            ns: dict = {}
            try:
                with s.track(task, code=payload):
                    exec(compile(payload, f"<{task}>", "exec"), ns)  # noqa: S102 - demo of student code
            except Exception:
                pass  # the student's error is recorded by track(); the demo goes on
            ev = s.events[-1]
            if verbose:
                mark = "✓ ok" if ev.ok else f"✗ {ev.error_type}: {ev.error_msg}"
                print(f"  +{int(t):>3}s run  {mark}")
        elif action == "confusion":
            s.mark_confusion(payload, task=task)
            if verbose:
                print(f"  +{int(t):>3}s 🆘 mark_confusion({payload!r})")
        elif action == "viz":
            opt.import_trace(payload, session=s, task=task)
            if verbose:
                print(f"  +{int(t):>3}s 👁 OPT_Mentor trace imported")
    return s


def run_demo(lang: str = "en", backend: str = "offline", save: Optional[str] = "demo_session.jsonl",
             pause: float = 0.0) -> Session:
    """Full cycle: errors -> compressed context -> student & instructor reports -> Moodle export."""
    try:
        from IPython import get_ipython  # type: ignore
        in_nb = get_ipython() is not None and "IPKernelApp" in get_ipython().config
    except Exception:
        in_nb = False

    def show(md: str) -> None:
        if in_nb:
            from IPython.display import Markdown, display  # type: ignore
            display(Markdown(md))
        else:
            print(md)

    def step(title: str) -> None:
        print("\n" + "=" * 72 + f"\n{title}\n" + "=" * 72)
        if pause:
            time.sleep(pause)

    step("1/4  Student works (real code, real exceptions, simulated clock)")
    s = build_demo_session(verbose=True)
    if save:
        s.save(save)

    step("2/4  Trajectory Compressor")
    ctx = s.compress()
    show(ctx.to_markdown())

    step("3/4  Reports (Hermes if configured, otherwise offline engine)")
    for mode in ("student", "instructor"):
        rep = generate_report(mode, session=s, backend=backend, lang=lang)
        show(rep._repr_markdown_() + "\n")

    step("4/4  Moodle export (CSV rows per question)")
    print(MoodleAdapter(course_id=1302, quiz_id=7, user_id="s1024", attempt=1).export_csv(ctx))
    if save:
        print(f"raw trajectory saved to {save} — try: stucktrace report {save} --mode instructor")
    return s
