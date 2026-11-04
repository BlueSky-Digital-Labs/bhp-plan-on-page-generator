"""How an attached file's text sits inside an answer, and how to tell it from what was typed."""

import re

MAX_ATTACHMENT_CHARS = 15_000  # of extracted text sent with one answer

_START = "[[Attached file: {name} | {kind}{note}]]"
_END = "[[End of attached file]]"
_BLOCK = re.compile(r"\[\[Attached file: [^\n\]]*\]\].*?\[\[End of attached file\]\]", re.DOTALL)


def block(name: str, kind: str, text: str, *, truncated: bool) -> str:
    """The attachment as it is appended to an answer. Marker lookalikes inside the text are broken
    up so a file cannot forge the end of its own block."""
    safe = text.replace("[[", "[ [")
    note = " | shortened" if truncated else ""
    return f"{_START.format(name=name, kind=kind, note=note)}\n{safe}\n{_END}"


def typed_part(answer: str) -> str:
    """What the user actually typed: the answer without any attached-file blocks. Their own words
    alone decide things only they may decide (such as the priority, or saying "skip")."""
    return _BLOCK.sub("", answer).strip()
