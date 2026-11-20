"""Event model: the raw, append-only trajectory record."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from typing import Any, Dict, Optional

RUN = "run"              # one execution attempt (cell / script / decorated function)
CONFUSION = "confusion"  # explicit "I don't understand" marker from the student
VIZ = "viz"              # a visualization / tracing step (OPT_Mentor, Python Tutor, ...)
TASK = "task"            # the student switched to another task
NOTE = "note"            # free-form note (instructor / tool)

KINDS = frozenset({RUN, CONFUSION, VIZ, TASK, NOTE})


@dataclass
class Event:
    kind: str
    ts: float
    task: Optional[str] = None
    ok: Optional[bool] = None
    error_type: Optional[str] = None
    error_msg: Optional[str] = None
    duration: Optional[float] = None
    code_hash: Optional[str] = None
    code_len: Optional[int] = None
    edit_ratio: Optional[float] = None   # 0.0 = identical to previous attempt, 1.0 = full rewrite
    code_preview: Optional[str] = None
    text: Optional[str] = None
    data: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None and v != {} and v != ""}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Event":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})
