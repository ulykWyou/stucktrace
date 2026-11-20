"""Command line: ``stucktrace report trace.jsonl --mode instructor``."""
from __future__ import annotations

import argparse
import sys
from typing import List, Optional

from . import __version__
from .adapters import MoodleAdapter
from .reporter import generate_report
from .tracker import Session


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="stucktrace", description="Learning-trajectory reports from a saved log.")
    p.add_argument("--version", action="version", version=f"stucktrace {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("report", help="generate a report from a .jsonl trajectory")
    r.add_argument("path")
    r.add_argument("--mode", choices=["student", "instructor"], default="student")
    r.add_argument("--lang", choices=["en", "ru"], default="en")
    r.add_argument("--offline", action="store_true", help="never call Hermes")
    r.add_argument("-o", "--out", help="write Markdown to this file")

    c = sub.add_parser("compress", help="print the compressed context")
    c.add_argument("path")
    c.add_argument("--format", choices=["json", "md", "prompt"], default="md")

    m = sub.add_parser("moodle", help="export Moodle CSV/JSON")
    m.add_argument("path")
    m.add_argument("--format", choices=["csv", "json"], default="csv")

    d = sub.add_parser("demo", help="run the 45-second end-to-end demo")
    d.add_argument("--lang", choices=["en", "ru"], default="en")
    d.add_argument("--hermes", action="store_true", help="try the Hermes API (falls back offline)")
    d.add_argument("--pause", type=float, default=0.0, help="seconds to pause between demo steps")

    a = p.parse_args(argv)
    if a.cmd == "demo":
        from .demo import run_demo
        run_demo(lang=a.lang, backend="hermes" if a.hermes else "offline", pause=a.pause)
        return 0
    session = Session.load(a.path)
    if a.cmd == "report":
        rep = generate_report(a.mode, session=session, backend="offline" if a.offline else "auto", lang=a.lang)
        if a.out:
            rep.save(a.out)
        print(rep._repr_markdown_())
        if rep.warning:
            print(f"\n[warning] {rep.warning}", file=sys.stderr)
    elif a.cmd == "compress":
        ctx = session.compress()
        print({"json": ctx.to_json, "md": ctx.to_markdown, "prompt": ctx.to_prompt}[a.format]())
    elif a.cmd == "moodle":
        import json
        ad = MoodleAdapter(user_id=session.metadata.get("student_id"))
        ctx = session.compress()
        print(ad.export_csv(ctx) if a.format == "csv" else json.dumps(ad.export_attempt_json(ctx), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
