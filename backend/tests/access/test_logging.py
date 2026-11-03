import logging

from app.logging_config import JsonFormatter
from tests.access.conftest import CODE, NEW_CODE, SESSION_COOKIE, csrf, enter, signed_in, start


def test_logs_carry_event_categories_but_no_codes_cookies_or_secrets(world):
    lines: list[str] = []

    class Capture(logging.Handler):
        def emit(self, record):
            lines.append(JsonFormatter().format(record))

    logger = logging.getLogger("app.access")
    handler = Capture()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        client = signed_in(world)
        session_cookie = client.cookies.get(SESSION_COOKIE)
        enter(world.client(), "wrong-guess-SECRETISH")
        world.secrets.fail = True
        enter(world.client(), CODE)
        world.secrets.fail = False
        client.cookies.clear()  # replace (not duplicate) the cookie so only the bad one is sent
        client.cookies.set(SESSION_COOKIE, "tampered.value")
        client.get("/api/session")
        client.post("/api/sessions", headers={**csrf(client), "Origin": "https://evil.example"})
        start(world.client())
    finally:
        logger.removeHandler(handler)

    text = "\n".join(lines)
    for forbidden in (
        CODE,
        NEW_CODE,
        "wrong-guess-SECRETISH",
        session_cookie,
        world.secrets.hash,
        world.secrets.key.decode(),
    ):
        assert forbidden not in text
    for event in (
        "access_granted",
        "access_denied",
        "session_created",
        "session_rejected",
        "csrf_rejected",
        "dependency_unavailable",
    ):
        assert event in text
