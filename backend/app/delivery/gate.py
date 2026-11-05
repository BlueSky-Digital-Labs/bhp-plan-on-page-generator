"""The one rule for "may this session be deleted by the user?".

Used both to show the download status and, again, immediately before a deletion is accepted. It
reads only server-held facts: what the client says about itself is never an input.
"""

from dataclasses import dataclass

from app.delivery.store import Delivery, DocumentKind

# Why deletion is locked. Machine-readable: the screen owns the wording.
LOCK_REASONS = (
    "not_ready",
    "turn_in_progress",
    "not_confirmed",
    "canvas_not_downloaded",
    "appendix_not_downloaded",
    "both_not_downloaded",
)


@dataclass(frozen=True)
class PurgeGate:
    revision: int
    canvas_downloaded: bool
    appendix_downloaded: bool
    purge_eligible: bool
    lock_reason: str | None

    def view(self) -> dict:
        return {
            "revision": self.revision,
            "canvas_downloaded": self.canvas_downloaded,
            "appendix_downloaded": self.appendix_downloaded,
            "purge_eligible": self.purge_eligible,
            "lock_reason": self.lock_reason,
        }


def evaluate_gate(
    revision: int,
    *,
    ready: bool,
    reply_finished: bool,
    confirmed_revision: int | None,
    deliveries: dict[DocumentKind, Delivery],
) -> PurgeGate:
    """Both documents count only if they were sent for the *current* revision.

    So an amendment (a new revision) resets both at once, and a late record for an older revision
    can never unlock anything: its revision no longer matches.
    """
    canvas = (held := deliveries.get("canvas")) is not None and held.revision == revision
    appendix = (held := deliveries.get("appendix")) is not None and held.revision == revision
    reason: str | None = None
    if not ready:
        reason = "not_ready"
    elif not reply_finished:
        reason = "turn_in_progress"
    elif confirmed_revision != revision:
        reason = "not_confirmed"
    elif not canvas and not appendix:
        reason = "both_not_downloaded"
    elif not canvas:
        reason = "canvas_not_downloaded"
    elif not appendix:
        reason = "appendix_not_downloaded"
    return PurgeGate(revision, canvas, appendix, reason is None, reason)
