import pytest

import stucktrace as st
from stucktrace import Session, track
from stucktrace.events import CONFUSION, RUN, VIZ


def test_record_run_failure_and_edit_ratio(session):
    session.record_run("x = 1\n", ok=True, task="t")
    ev = session.record_run("x = 1\ny = z\n", exc=NameError("name 'z' is not defined"), task="t")
    assert ev.ok is False and ev.error_type == "NameError"
    assert 0 < ev.edit_ratio < 1
    assert ev.code_hash and ev.code_preview.startswith("x = 1")


def test_store_code_false_keeps_no_preview(clock):
    s = Session(store_code=False, clock=clock)
    ev = s.record_run("secret = 42")
    assert ev.code_preview is None and ev.code_len == 11


def test_context_manager_records_and_reraises(session):
    with pytest.raises(ZeroDivisionError):
        with track("div", code="1/0", session=session):
            1 / 0
    ev = session.events[-1]
    assert ev.kind == RUN and not ev.ok and ev.error_type == "ZeroDivisionError" and ev.task == "div"


def test_decorator_with_and_without_args(session):
    st.start_session(jupyter=False)

    @track
    def bare():
        return 1

    @track("named")
    def named(x):
        if x < 0:
            raise ValueError("neg")
        return x

    assert bare() == 1 and named(2) == 2
    with pytest.raises(ValueError):
        named(-1)
    runs = [e for e in st.get_session().events if e.kind == RUN]
    assert [r.task for r in runs] == ["bare", "named", "named"]
    assert [r.ok for r in runs] == [True, True, False]
    assert bare.__name__ == "bare"


def test_markers(session):
    session.set_task("loops")
    session.mark_confusion("не понимаю цикл")
    session.log_viz(step=3, line=5, note="i=2")
    kinds = [e.kind for e in session.events]
    assert CONFUSION in kinds and VIZ in kinds
    assert session.events[-2].text == "не понимаю цикл" and session.events[-2].task == "loops"
    assert session.events[-1].data == {"step": 3, "line": 5}


def test_global_api():
    s = st.start_session(student_id="abc", task="t1", jupyter=False)
    st.mark_confusion("help")
    st.log_viz(step=1)
    assert s is st.get_session() and len(s.events) == 3 and s.metadata["student_id"] == "abc"


def test_save_and_load_roundtrip(session, tmp_path, clock):
    session.record_run("a", exc=TypeError("bad"), task="t")
    clock.now += 5
    session.mark_confusion("??", task="t")
    p = tmp_path / "s.jsonl"
    assert session.save(str(p))
    s2 = Session.load(str(p))
    assert s2.session_id == session.session_id and s2.metadata == session.metadata
    assert [e.to_dict() for e in s2.events] == session.to_dicts()


def test_telemetry_never_breaks(session, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("internal")
    monkeypatch.setattr(session, "_add", boom)
    assert session.record_run("x") is None          # swallowed
    assert session.mark_confusion("x") is None
    assert session.save("/nonexistent/dir/file.jsonl") is False
    with track("t", session=session):                # student code still runs
        y = 2
    assert y == 2


def test_autosave(tmp_path, clock):
    p = tmp_path / "live.jsonl"
    s = Session(autosave=str(p), clock=clock)
    s.record_run("x")
    s.mark_confusion("hm")
    assert len(p.read_text().strip().splitlines()) == 2


def test_max_events_ring_buffer(clock):
    s = Session(max_events=3, clock=clock)
    for i in range(5):
        s.record_run(str(i))
    assert len(s.events) == 3 and s.events[0].code_preview == "2"


def test_confusion_attaches_to_last_tracked_task(session):
    with pytest.raises(ZeroDivisionError):
        with track("avg", session=session):
            1 / 0
    session.mark_confusion("why?")
    assert session.events[-1].task == "avg"
