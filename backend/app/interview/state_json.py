"""An interview state as JSON, for stores that keep it as text (DynamoDB).

Written from the dataclass's own fields, so a new field is stored without touching this file. A
record that does not parse is an error the caller treats as a storage fault, never a blank state.
"""

import json
from dataclasses import fields
from typing import Any

from app.interview.engine import InterviewState, Target


def dump_state(state: InterviewState) -> str:
    data: dict[str, Any] = {}
    for f in fields(state):
        value = getattr(state, f.name)
        if isinstance(value, set):
            value = sorted(value)
        elif isinstance(value, Target):
            value = {"kind": value.kind, "field": value.field}
        data[f.name] = value
    return json.dumps(data, ensure_ascii=False)


def load_state(text: str) -> InterviewState:
    """The state, or ValueError if the text is not a state this code wrote."""
    raw = json.loads(text)
    if not isinstance(raw, dict):
        raise ValueError("not an object")
    values: dict[str, Any] = {}
    for f in fields(InterviewState):
        if f.name not in raw:
            continue
        value = raw[f.name]
        if f.name == "target":
            value = Target(value["kind"], value["field"]) if value else None
        elif f.default_factory is set:  # type: ignore[misc]
            value = set(value)
        values[f.name] = value
    return InterviewState(**values)
