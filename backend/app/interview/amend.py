"""Changing an answer after "That's everything I need": which part does the user mean?

Deterministic and content-free: it only looks for the names of canvas fields in what the user typed
(the model is not asked, so a weak model cannot pick the wrong part). A message that names exactly
one field goes straight to asking for the new answer; anything else asks which part, with the list.
"""

import re

from app.interview.canvas import LABELS, OPPORTUNITY_TYPES, PRIORITIES

# What the "which part?" list shows, in canvas order. Plain words, no question marks.
SHORT_NAMES: dict[str, str] = {
    "solutionName": "Solution name",
    "team": "Team",
    "currentProcess": "Current process",
    "painPoints": "Pain points",
    "businessImpact": "Business impact",
    "futureState": "Future state",
    "achieve": "What we want to achieve",
    "success": "Success measures",
    "kpis": "Expected value / KPIs",
    "users": "Users",
    "decision": "Decision it improves",
    "data": "Data",
    "systems": "Systems",
    "risks": "Risks",
    "priority": "Priority",
    "opportunityTypes": "Opportunity type",
}
PICK_OPTIONS = tuple(SHORT_NAMES.values())

_WORDS: dict[str, tuple[str, ...]] = {
    "solutionName": (
        "solution name",
        "idea name",
        "name of the solution",
        "name of the idea",
        "name of my idea",
        "idea title",
        "title",
        "name",
    ),
    "team": ("team name", "team", "department", "business unit"),
    "currentProcess": ("current process", "process", "how it works today", "how it is done today"),
    "painPoints": ("pain points", "pain point", "pain", "problems", "problem"),
    "businessImpact": ("business impact", "impact"),
    "futureState": ("future state vision", "future state", "future", "vision"),
    "achieve": ("what we want to achieve", "achieve", "goal", "outcome", "objective"),
    "success": ("success measures", "success measure", "what success looks like", "success"),
    "kpis": (
        "expected value / kpis",
        "expected value/kpis",
        "expected value",
        "kpis",
        "kpi",
        "metrics",
        "metric",
    ),
    "users": ("who will use this solution", "who will use", "users", "user", "audience"),
    "decision": ("decision it improves", "decisions", "decision"),
    "data": ("data needed", "data"),
    "systems": ("systems", "system", "integrations", "integration"),
    "risks": ("risks", "risk", "dependencies", "dependency"),
    "priority": ("priority", "priorities"),
    "opportunityTypes": (
        "opportunity types",
        "opportunity type",
        "opportunities",
        "opportunity",
        "type of help",
        "kind of help",
    ),
}


def _aliases() -> list[tuple[str, str]]:
    pairs: dict[str, str] = {}
    for field, words in _WORDS.items():
        for word in words:
            pairs[word] = field
        pairs[SHORT_NAMES[field].casefold()] = field
        pairs[LABELS[field].casefold().rstrip("?")] = field
    # Longest first, so "team name" is read as the team and not also as a name.
    return sorted(pairs.items(), key=lambda pair: -len(pair[0]))


_ALIASES = _aliases()
_VALUES = tuple(sorted((*OPPORTUNITY_TYPES, *PRIORITIES), key=len, reverse=True))


def mentioned_fields(text: str) -> list[str]:
    """Canvas fields named in the text, in the order they first appear, each once.

    The words of the answers themselves ("Analyse Data", "Must-have") are set aside first, so
    "change the opportunity to Analyse Data" names one field, not two.
    """
    low = text.casefold()
    for value in _VALUES:
        low = re.sub(rf"\b{re.escape(value.casefold())}\b", " ", low)
    found: list[tuple[int, str]] = []
    for word, field in _ALIASES:
        pattern = re.compile(rf"(?<![a-z]){re.escape(word)}(?![a-z])")
        match = pattern.search(low)
        if match is None:
            continue
        found.append((match.start(), field))
        low = low[: match.start()] + " " * len(word) + low[match.end() :]  # used up
    seen: list[str] = []
    for _, field in sorted(found):
        if field not in seen:
            seen.append(field)
    return seen


_THANKS = (
    r"(?:thanks|thank you|thx|ok|okay|great|perfect|good|fine|looks good|all good|that'?s all|"
    r"that'?s fine|that'?s it|nothing else|nothing|no thanks|no thank you)"
)
_DONE = re.compile(rf"^(?:{_THANKS}[\s.,!]*)+$")  # nothing but thanks, however many
_NEGATIVE = re.compile(
    r"^(no\b|nope|never ?mind|cancel|forget it|leave it|keep it|not that|wrong one|actually no|"
    r"stop)"
)


def is_done(text: str) -> bool:
    """A short "thanks" / "okay" / "that's all": nothing to change."""
    t = text.strip().casefold()
    return len(t) <= 40 and bool(_DONE.match(t))


def is_negative(text: str) -> bool:
    """ "No", "never mind", "cancel": not that one (or nothing at all)."""
    t = text.strip().casefold()
    return len(t) <= 60 and bool(_NEGATIVE.match(t) or _DONE.match(t))
