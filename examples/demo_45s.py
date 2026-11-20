"""45-second defense demo. Run:  python examples/demo_45s.py [--lang ru] [--pause 2]

Same as `stucktrace demo` (works right after `pip install stucktrace`, no repo needed).
"""
import argparse

from stucktrace.demo import run_demo

p = argparse.ArgumentParser()
p.add_argument("--lang", default="en", choices=["en", "ru"])
p.add_argument("--pause", type=float, default=0.0, help="pause between steps, for live narration")
p.add_argument("--hermes", action="store_true", help="try Hermes API (falls back to offline)")
a = p.parse_args()
run_demo(lang=a.lang, backend="hermes" if a.hermes else "offline", pause=a.pause)
