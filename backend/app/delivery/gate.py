"""The one rule for "may this session be deleted by the user?".

A session may be deleted once the one-page canvas, in any format, has been sent for the *current*
revision, whether the canvas is finished or an unfinished draft the user saves to carry on later.
(The appendix is still reported, but it never holds deletion back.)
Used both to show the download status and, again, immediately before a deletion is accepted. It
reads only server-held facts: what the client says about itself is never an input.
"""

from dataclasses import dataclass

from app.delivery.store import Delivery, DocumentKind

# Why deletion is locked. Machine-readable: the screen owns the wording.
LOCK_REASONS = (
    "not_ready",  # nothing answered yet, so there is nothing to save
    "turn_in_progress",
    "canvas_not_downloaded",
)


@dataclass(frozen=True)
class PurgeGate:
    revision: int
    canvas_downloaded: bool
    appendix_downloaded: bool
    purge_eligible: bool
    lock_reason: str | None
    complete: bool = True  # False: an unfinished draft; the screen then says "stop", not "delete"

    def view(self) -> dict:
        return {
            "revision": self.revision,
            "canvas_downloaded": self.canvas_downloaded,
            "appendix_downloaded": self.appendix_downloaded,
            "purge_eligible": self.purge_eligible,
            "lock_reason": self.lock_reason,
            "complete": self.complete,
        }


def evaluate_gate(
    revision: int,
    *,
    started: bool,
    complete: bool,
    reply_finished: bool,
    deliveries: dict[DocumentKind, Delivery],
) -> PurgeGate:
    """A document counts only if it was sent for the *current* revision.

    So an amendment (a new revision) resets both at once, and a late record for an older revision
    can never unlock anything: its revision no longer matches. `started` means at least one
    answer is in, so there is something to save; `complete` is only for the wording on screen.
    """
    canvas = (held := deliveries.get("canvas")) is not None and held.revision == revision
    appendix = (held := deliveries.get("appendix")) is not None and held.revision == revision
    reason: str | None = None
    if not started:
        reason = "not_ready"
    elif not reply_finished:
        reason = "turn_in_progress"
    elif not canvas:
        reason = "canvas_not_downloaded"
    return PurgeGate(revision, canvas, appendix, reason is None, reason, complete)
