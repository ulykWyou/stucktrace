from stucktrace import CompressorConfig, compress, error_signature


def fail(session, clock, t, etype="TypeError", msg="unsupported operand type(s) for +: 'int' and 'str'",
         code=None, task="t"):
    clock.now = 1000 + t
    session.record_run(code, error_type=etype, error_msg=msg, task=task)


def ok(session, clock, t, code=None, task="t"):
    clock.now = 1000 + t
    session.record_run(code, ok=True, task=task)


def test_error_signature_normalises_names_and_numbers():
    a = error_signature("NameError", "name 'x' is not defined")
    b = error_signature("NameError", "name 'total' is not defined")
    assert a == b == "NameError: name '…' is not defined"
    assert error_signature("IndexError", "line 12") == error_signature("IndexError", "line 7")


def test_rapid_burst_and_repeated_error(session, clock):
    for t in (0, 5, 9, 14):
        fail(session, clock, t)
    ok(session, clock, 30)
    ctx = compress(session)
    task = ctx.tasks[0]
    kinds = [s.kind for s in task.stuck_points]
    assert "rapid_fail_burst" in kinds and "repeated_error" in kinds
    assert task.error_clusters[0].count == 4 and task.error_clusters[0].max_streak == 4
    assert task.error_clusters[0].resolved
    assert task.solved and task.first_success_attempt == 5
    assert task.panic_level in ("medium", "high")
    assert task.transitions[0].after_attempts == 4


def test_slow_failures_are_not_a_burst(session, clock):
    for t in (0, 60, 120):
        fail(session, clock, t, etype=["A", "B", "C"][t // 60], msg="")
    kinds = {s.kind for s in compress(session).tasks[0].stuck_points}
    assert "rapid_fail_burst" not in kinds and "repeated_error" not in kinds


def test_long_pause_and_confusion(session, clock):
    fail(session, clock, 0)
    clock.now = 1000 + 300
    session.mark_confusion("не понимаю цикл", task="t")
    task = compress(session).tasks[0]
    kinds = [s.kind for s in task.stuck_points]
    assert kinds == ["long_pause", "confusion"]
    assert task.confusions == ["не понимаю цикл"]
    assert not task.solved


def test_rewrite_detection(session, clock):
    fail(session, clock, 0, code="for i in range(10):\n    print(i)\n")
    ok(session, clock, 40, code="total = sum(x * x for x in data)\nresult = total / len(data)\n")
    kinds = [s.kind for s in compress(session).tasks[0].stuck_points]
    assert "rewrite" in kinds


def test_viz_helped(session, clock):
    fail(session, clock, 0)
    clock.now = 1010
    session.log_viz(step=1, task="t")
    ok(session, clock, 20)
    task = compress(session).tasks[0]
    assert task.viz_helped and task.transitions[0].viz_before


def test_digest_is_run_length_encoded(session, clock):
    for t in (0, 3, 6):
        fail(session, clock, t)
    ok(session, clock, 9)
    assert compress(session).tasks[0].digest == "F:TypeError x3 | S"


def test_tasks_are_separated_and_totals(session, clock):
    fail(session, clock, 0, task="a")
    ok(session, clock, 5, task="a")
    ok(session, clock, 10, task="b")
    ctx = compress(session)
    assert [t.task for t in ctx.tasks] == ["a", "b"]
    assert ctx.totals["runs"] == 3 and ctx.totals["failures"] == 1 and ctx.totals["solved"] == 2


def test_clean_session_has_low_panic(session, clock):
    ok(session, clock, 0)
    task = compress(session).tasks[0]
    assert task.panic_score == 0 and task.panic_level == "low" and not task.stuck_points


def test_views_and_config(session, clock):
    for t in (0, 40, 80):
        fail(session, clock, t)
    assert not any(s.kind == "rapid_fail_burst" for s in compress(session).tasks[0].stuck_points)
    ctx = compress(session, CompressorConfig(panic_gap=60))
    assert any(s.kind == "rapid_fail_burst" for s in ctx.tasks[0].stuck_points)
    assert '"tasks"' in ctx.to_json() and "| t |" in ctx.to_markdown()
    assert "TASK t" in ctx.to_prompt() and len(ctx.to_prompt(max_chars=50)) <= 50
    assert ctx.stats["raw_events"] == 3


def test_compress_accepts_dicts():
    evs = [{"kind": "run", "ts": 1.0, "task": "x", "ok": False, "error_type": "E"},
           {"kind": "run", "ts": 2.0, "task": "x", "ok": True}]
    ctx = compress(evs)
    assert ctx.tasks[0].solved and ctx.session_id == "adhoc"


def test_empty_session(session):
    ctx = compress(session)
    assert ctx.tasks == [] and ctx.totals["runs"] == 0
