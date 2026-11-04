"""The canvas and its deterministic validator (field ids match frontend/src/canvas/fields.ts).

The validator, not the model, decides what is complete. Required for review: every text field
except Business Impact and Expected Value/KPIs, plus Priority (the user's own choice) and at
least one opportunity type.
"""

import re
from enum import StrEnum
from typing import Any

PRIORITIES = ("Low", "Medium", "High", "Strategic")
OPPORTUNITY_TYPES = (
    "Find Information",
    "Generate Reports",
    "Predict Issues",
    "Recommend Actions",
    "Analyse Data",
    "Automate Workflows",
)


class CanvasField(StrEnum):
    solutionName = "solutionName"  # noqa: N815
    team = "team"
    currentProcess = "currentProcess"  # noqa: N815
    painPoints = "painPoints"  # noqa: N815
    businessImpact = "businessImpact"  # noqa: N815
    futureState = "futureState"  # noqa: N815
    achieve = "achieve"
    success = "success"
    kpis = "kpis"
    users = "users"
    decision = "decision"
    data = "data"
    systems = "systems"
    risks = "risks"
    priority = "priority"
    opportunityTypes = "opportunityTypes"  # noqa: N815


LABELS = {
    "solutionName": "Solution Name",
    "team": "Team",
    "currentProcess": "Current Process",
    "painPoints": "Pain Points",
    "businessImpact": "Business Impact",
    "futureState": "Future State Vision",
    "achieve": "What do we want to achieve through AI?",
    "success": "What does success look like?",
    "kpis": "Expected Value/KPIs",
    "users": "Who will use this solution?",
    "decision": "What decision will it improve?",
    "data": "What data is needed?",
    "systems": "What systems must connect?",
    "risks": "Key risks and dependencies?",
    "priority": "Priority",
    "opportunityTypes": "Opportunity type",
}

OPTIONAL_FIELDS = ("businessImpact", "kpis")  # prompted, but never an export blocker
# The order questions are asked in. Priority is last and is always the user's choice.
REQUIRED_ORDER = (
    "solutionName",
    "team",
    "currentProcess",
    "painPoints",
    "futureState",
    "achieve",
    "opportunityTypes",
    "success",
    "users",
    "decision",
    "data",
    "systems",
    "risks",
    "priority",
)
ALL_FIELDS = frozenset(REQUIRED_ORDER) | frozenset(OPTIONAL_FIELDS)

MAX_TEXT = 1000

Canvas = dict[str, Any]  # field id -> str, or list[str] for opportunityTypes


def clean_value(field: str, raw: Any) -> str | list[str] | None:
    """A validated value for the field, or None. Nothing is coerced into a fact."""
    if field not in ALL_FIELDS:
        return None
    if field == "opportunityTypes":
        items = [raw] if isinstance(raw, str) else raw
        if not isinstance(items, list) or not items:
            return None
        canonical = {t.casefold(): t for t in OPPORTUNITY_TYPES}
        chosen: list[str] = []
        for item in items:
            match = canonical.get(item.casefold().strip()) if isinstance(item, str) else None
            if match is None:
                return None  # one unsupported value rejects the whole update
            if match not in chosen:
                chosen.append(match)
        return chosen
    if not isinstance(raw, str):
        return None
    text = re.sub(r"\s+", " ", raw).strip()
    if field == "priority":
        return next((p for p in PRIORITIES if p.casefold() == text.casefold()), None)
    return text if 0 < len(text) <= MAX_TEXT else None


def missing_required(canvas: Canvas) -> list[str]:
    return [f for f in REQUIRED_ORDER if not canvas.get(f)]
