import pytest

import stucktrace.tracker as tracker
from stucktrace import Session


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def session(clock):
    return Session(student_id="s1", clock=clock)


@pytest.fixture(autouse=True)
def _reset_global():
    tracker._current = None
    yield
    tracker._current = None


@pytest.fixture(autouse=True)
def _no_hermes_env(monkeypatch):
    for k in ("HERMES_API_URL", "HERMES_API_KEY", "HERMES_MODEL"):
        monkeypatch.delenv(k, raising=False)
