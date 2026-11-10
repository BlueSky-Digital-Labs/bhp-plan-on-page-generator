"""Scheduled expiry: delete every session past its end (15 idle minutes or 24 hours), whether or
not the user came back.

A command, not a web route: `python -m app.expiry` runs one sweep and exits (0 if every expired
session was removed, 1 otherwise, so a scheduled task that failed shows up as a failed task). Run
it every 5 minutes. A session becomes deletable the moment it is strictly past its idle deadline or
its 24 hour end, and is physically removed by the next sweep. The DynamoDB TTL is only a backstop.
See docs/expiry.md.

It bypasses the download gate on purpose (the user may never have returned), uses the same
deletion as sign-out and purge, finds sessions from the content store's own id-and-expiry list
(so it does not depend on a session record that TTL may already have removed), and logs counts
only.
"""

import logging
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.access.errors import DependencyUnavailable

logger = logging.getLogger("app.expiry")

PAGE_SIZE = 100
MAX_PAGES = 1000  # a sweep is bounded; whatever is left is picked up by the next one


@dataclass(frozen=True)
class ExpiryResult:
    found: int
    deleted: int
    failed: int
    complete: bool  # every page was read; False if the page limit stopped it early

    @property
    def ok(self) -> bool:
        return self.failed == 0


def run_expiry(
    conversation: Any,
    access: Any,
    *,
    now: datetime,
    page_size: int = PAGE_SIZE,
    max_pages: int = MAX_PAGES,
) -> ExpiryResult:
    """One sweep. Safe to repeat or to overlap with another sweep: every step is idempotent.

    For each session the sign-in record is ended first, so no new content can be written under it,
    then the content is deleted. One session failing never stops the others; it stays listed and
    the next sweep tries it again.
    """
    found = deleted = failed = 0
    after: str | None = None
    complete = False
    for _ in range(max_pages):
        ids, after = conversation.expired_session_ids(now, page_size, after)
        for session_id in ids:
            found += 1
            try:
                access.end_session(session_id)
                conversation.delete_session(session_id)
                deleted += 1
            except Exception as exc:  # noqa: BLE001 - recorded by type only, then retried next run
                failed += 1
                logger.error("expiry_delete_failed", extra={"exception_type": type(exc).__name__})
        if after is None:
            complete = True
            break
    return ExpiryResult(found, deleted, failed, complete)


def main() -> int:
    from app.access.wiring import build_access_service
    from app.config import get_settings
    from app.logging_config import configure_logging
    from app.main import build_conversation

    settings = get_settings()
    configure_logging(settings.log_level)
    try:
        result = run_expiry(
            build_conversation(settings),
            build_access_service(settings),
            now=datetime.now(UTC),
        )
    except DependencyUnavailable as exc:
        # E.g. deployed without a durable store: nothing can be swept, and that must be loud.
        logger.error("expiry_unavailable", extra={"reason": exc.category})
        return 1
    logger.info(
        "expiry_run",
        extra={
            "detail": f"found={result.found} deleted={result.deleted} failed={result.failed}"
            f" complete={result.complete}"
        },
    )
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
