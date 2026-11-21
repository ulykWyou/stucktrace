import pytest

import stucktrace as st
from stucktrace.cli import main
from stucktrace.tracker import _is_internal

@pytest.fixture
def ip():
    pytest.importorskip("IPython")
    from IPython.testing.globalipapp import get_ipython
    shell = get_ipython()
    yield shell
    st.unload_ipython_extension(shell)


def test_is_internal():
    assert _is_internal("%stucktrace report")
    assert _is_internal("st.generate_report('student')\nst.mark_confusion('x')")
    assert _is_internal("   ")
    assert not _is_internal("x = 1\nst.mark_confusion('x')")


def test_load_ext_tracks_cells(ip):
    ip.run_cell("import stucktrace\n%load_ext stucktrace", store_history=True)
    st.load_ipython_extension(ip)               # idempotent
    ip.run_line_magic("stucktrace", "task loops")
    ip.run_cell("for i in range(3):\n    total += i")     # NameError
    ip.run_cell("for i in range(3)\n    pass")             # SyntaxError
    ip.run_cell("total = 0\nfor i in range(3):\n    total += i")
    ip.run_line_magic("confused", "не понимаю цикл")
    runs = [e for e in st.get_session().events if e.kind == "run"]
    assert [r.ok for r in runs] == [False, False, True]
    assert runs[0].error_type == "NameError" and runs[1].error_type == "SyntaxError"
    assert all(r.task == "loops" for r in runs)
    assert st.get_session().events[-1].text == "не понимаю цикл"
    rep = ip.run_line_magic("stucktrace", "report instructor --offline")
    assert "loops" in rep.markdown


def test_start_session_installs_hooks_in_ipython(ip):
    st.start_session(student_id="x")
    assert getattr(ip, "_stucktrace_hooks", None) is not None
    st.end_session()
    assert getattr(ip, "_stucktrace_hooks", None) is None


def test_cli(tmp_path, capsys):
    from stucktrace.demo import build_demo_session
    p = tmp_path / "s.jsonl"
    build_demo_session(verbose=False).save(str(p))
    assert main(["report", str(p), "--mode", "instructor", "--offline", "-o", str(tmp_path / "r.md")]) == 0
    assert "Instructor Diagnostic" in capsys.readouterr().out
    assert main(["compress", str(p), "--format", "json"]) == 0
    assert '"session_id"' in capsys.readouterr().out
    assert main(["moodle", str(p)]) == 0
    assert "sum_evens" in capsys.readouterr().out

    
def test_reset_magic_starts_fresh_session(ip, capsys):
    st.load_ipython_extension(ip)
    ip.run_cell("x = 1 / 0")
    old = st.get_session()
    assert any(e.kind == "run" for e in old.events)
    ip.run_line_magic("stucktrace", "reset")
    new = st.get_session()
    assert new is not old and new.events == []
    assert old.session_id in capsys.readouterr().out
    ip.run_cell("y = 2")
    assert [e.ok for e in new.events if e.kind == "run"] == [True]
