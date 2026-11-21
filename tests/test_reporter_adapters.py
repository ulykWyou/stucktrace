import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from stucktrace import (HermesClient, MoodleAdapter, OPTMentorAdapter, generate_report, spoiler_guard)
from stucktrace.demo import build_demo_session


@pytest.fixture
def demo():
    return build_demo_session(verbose=False)


def test_offline_student_and_instructor(demo):
    s = generate_report("student", session=demo, backend="offline")
    i = generate_report("instructor", session=demo, backend="offline")
    assert s.source == i.source == "offline"
    assert "Self-Reflection" in s.markdown and "sum_evens" in s.markdown
    assert "Instructor Diagnostic" in i.markdown and "| sum_evens |" in i.markdown
    assert "why does % not work" in i.markdown
    assert "offline engine" in s._repr_markdown_()


def test_russian_report(demo):
    r = generate_report("instructor", session=demo, backend="offline", lang="ru")
    assert "Диагностический" in r.markdown and "подряд" in r.markdown


def test_invalid_mode(demo):
    with pytest.raises(ValueError):
        generate_report("teacher", session=demo)


def test_unreachable_hermes_falls_back(demo):
    client = HermesClient(api_url="http://127.0.0.1:9/v1/chat/completions", timeout=1)
    r = generate_report("student", session=demo, backend="auto", hermes=client)
    assert r.source == "offline" and r.warning and "Hermes" in r.warning


def test_hermes_not_configured_is_silent(demo):
    r = generate_report("student", session=demo, backend="auto")
    assert r.source == "offline" and r.warning is None


class _Handler(BaseHTTPRequestHandler):
    received = {}

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _Handler.received = {"body": body, "auth": self.headers.get("Authorization")}
        answer = "## Report\nYou got stuck.\n```python\nline1\nline2\nline3\nline4\n```\nkeep going"
        data = json.dumps({"choices": [{"message": {"content": answer}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


def test_hermes_success_and_spoiler_guard(demo):
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        client = HermesClient(api_url=f"http://127.0.0.1:{srv.server_port}/v1/chat/completions",
                              api_key="k", model="hermes-3")
        r = generate_report("student", session=demo, hermes=client)
    finally:
        srv.shutdown()
    assert r.source == "hermes"
    assert "line3" not in r.markdown and "code hidden" in r.markdown   # spoiler removed
    assert _Handler.received["auth"] == "Bearer k"
    msgs = _Handler.received["body"]["messages"]
    assert msgs[0]["role"] == "system" and "never give the solution" in msgs[0]["content"]
    assert "TASK sum_evens" in msgs[1]["content"]


def test_spoiler_guard_keeps_short_snippets():
    md = "use `x`\n```\nprint(type(n))\n```\n"
    assert spoiler_guard(md) == md


def test_moodle_export(demo, tmp_path):
    ctx = demo.compress()
    ad = MoodleAdapter.from_attempt({"courseid": 5, "quizid": 9, "userid": "u1", "attempt": 2},
                                    question_map={"fizzbuzz": 10})
    data = ad.export_attempt_json(ctx)
    assert data["moodle"] == {"courseid": 5, "quizid": 9, "userid": "u1", "attempt": 2}
    slots = {q["task"]: q["slot"] for q in data["questions"]}
    assert slots["fizzbuzz"] == 10 and slots["sum_evens"] == 1
    p = tmp_path / "m.csv"
    text = ad.export_csv(ctx, str(p))
    assert text.splitlines()[0].startswith("userid,courseid") and p.exists()
    assert len(text.strip().splitlines()) == 1 + len(ctx.tasks)


def test_opt_mentor_import(session):
    trace = {"trace": [{"event": "step_line", "line": 1, "stack_to_render": []},
                       {"event": "call", "line": 3, "stack_to_render": [{}, {}]},
                       {"event": "exception", "line": 3, "exception_msg": "boom"}]}
    summary = OPTMentorAdapter().import_trace(json.dumps(trace), session=session, task="rec")
    assert summary["steps"] == 3 and summary["unique_lines"] == 2 and summary["max_stack_depth"] == 2
    assert session.events[-1].kind == "viz" and session.events[-1].task == "rec"
    assert OPTMentorAdapter().import_trace("not json", session=session) is None
    assert len(OPTMentorAdapter.export_steps(session)) == 1


def test_report_save(demo, tmp_path):
    p = tmp_path / "r.md"
    generate_report("instructor", session=demo, backend="offline").save(str(p))
    assert "stucktrace" in p.read_text(encoding="utf-8")


NEW_HINT_TYPES = ["FileNotFoundError", "StopIteration", "OverflowError", "NotImplementedError", "TabError"]


@pytest.mark.parametrize("etype", NEW_HINT_TYPES)
def test_new_hints_exist_in_both_languages(etype):
    from stucktrace.adapters import _HINTS
    assert _HINTS["en"][etype] and _HINTS["ru"][etype]


def test_hint_appears_in_student_report(session, clock):
    for t in (0, 5):
        clock.now = 1000 + t
        session.record_run("open('data.csv')", error_type="FileNotFoundError",
                           error_msg="[Errno 2] No such file or directory: 'data.csv'", task="io")
    md = generate_report("student", session=session, backend="offline").markdown
    assert "os.getcwd()" in md
