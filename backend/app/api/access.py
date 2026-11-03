"""Entry and session endpoints (JSON over same-origin fetch; see docs/access-control.md).

The originally described HTMX redirects become JSON responses plus Set-Cookie, in line
with the React/JSON foundation contract.
"""

import hmac
import logging
import secrets
from typing import Annotated
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Form, Request, Response

from app.access.errors import AccessRejected, DependencyUnavailable
from app.access.service import MAX_CODE_LENGTH, AccessService
from app.access.store import Session
from app.errors import ApiProblem, correlation_id_of, error_response

logger = logging.getLogger("app.access")

# __Host- cookies must be Secure, Path=/ and have no Domain: the browser enforces it.
GRANT_COOKIE = "__Host-grant"
SESSION_COOKIE = "__Host-session"
CSRF_COOKIE = "__Host-csrf"
CSRF_HEADER = "X-CSRF-Token"
_COOKIE = {"secure": True, "httponly": True, "samesite": "lax", "path": "/"}

# Navigation contract with the conversation feature: where the SPA goes after a session is
# created. The server decides this, so the client never composes a destination itself.
CONVERSATION_ROUTE = "/conversation"

router = APIRouter()


def get_service(request: Request) -> AccessService:
    return request.app.state.access


def _log(request: Request, event: str, reason: str | None = None) -> None:
    # Event category and a safe reason only: never the code, cookies, form bodies or secrets.
    logger.info(event, extra={"correlation_id": correlation_id_of(request), "reason": reason})


def _unauthenticated() -> ApiProblem:
    # One response for every credential failure, so nothing reveals whether a session exists.
    return ApiProblem(401, "unauthenticated", "Authentication is required.")


def _csrf_rejected(request: Request, reason: str) -> ApiProblem:
    _log(request, "csrf_rejected", reason)
    return ApiProblem(
        403,
        "request_not_verified",
        "The request could not be verified. Please reload and try again.",
    )


def require_csrf(request: Request) -> None:
    """Same-origin check, then a double-submit token. Applied to every state-changing route.

    Fails closed: a request that proves neither a same-origin Origin nor Sec-Fetch-Site is rejected.
    """
    origin = request.headers.get("origin")
    if origin is not None:
        # TLS ends at the load balancer, so compare host[:port] only, never the scheme.
        if origin == "null" or urlparse(origin).netloc != request.headers.get("host"):
            raise _csrf_rejected(request, "cross_origin")
    elif request.headers.get("sec-fetch-site") != "same-origin":
        raise _csrf_rejected(request, "origin_unproven")

    cookie, header = request.cookies.get(CSRF_COOKIE), request.headers.get(CSRF_HEADER)
    if not cookie or not header or not hmac.compare_digest(cookie, header):
        raise _csrf_rejected(request, "token_mismatch")


def current_session(
    request: Request, service: Annotated[AccessService, Depends(get_service)]
) -> Session:
    try:
        return service.load_session(request.cookies.get(SESSION_COOKIE))
    except AccessRejected as exc:
        _log(request, "session_rejected", exc.reason)
        raise _unauthenticated() from None


@router.get("/access-state")
def access_state(
    request: Request, service: Annotated[AccessService, Depends(get_service)]
) -> dict[str, bool]:
    """Whether this browser may start a canvas. A boolean only: no secrets, no content."""
    return {
        "start_permitted": service.start_permitted(
            request.cookies.get(GRANT_COOKIE), request.cookies.get(SESSION_COOKIE)
        )
    }


@router.get("/csrf")
def issue_csrf_token(response: Response) -> dict[str, str]:
    token = secrets.token_urlsafe(32)
    response.set_cookie(CSRF_COOKIE, token, max_age=24 * 3600, **_COOKIE)
    return {"csrf_token": token}


@router.post("/access", dependencies=[Depends(require_csrf)])
def enter_access_code(
    request: Request,
    response: Response,
    service: Annotated[AccessService, Depends(get_service)],
    access_code: Annotated[str, Form(min_length=1, max_length=MAX_CODE_LENGTH)],
) -> dict[str, str]:
    # Brute-force protection is the WAF rate-based rule on this path (docs/access-control.md).
    if not service.verify_access_code(access_code):
        _log(request, "access_denied", "invalid_code")
        raise ApiProblem(401, "invalid_access_code", "That access code was not recognised.")
    value, max_age = service.issue_grant()
    response.set_cookie(GRANT_COOKIE, value, max_age=max_age, **_COOKIE)
    _log(request, "access_granted")
    return {"status": "ok"}


@router.post("/sessions", status_code=201, dependencies=[Depends(require_csrf)])
def create_session(
    request: Request,
    response: Response,
    service: Annotated[AccessService, Depends(get_service)],
) -> dict[str, str]:
    try:
        value, session = service.start_session(
            request.cookies.get(GRANT_COOKIE), request.cookies.get(SESSION_COOKIE)
        )
    except AccessRejected as exc:
        _log(request, "session_start_rejected", exc.reason)
        raise _unauthenticated() from None
    max_age = service.seconds_remaining(session, service.now())
    response.set_cookie(SESSION_COOKIE, value, max_age=max_age, **_COOKIE)
    response.delete_cookie(GRANT_COOKIE, **_COOKIE)  # the grant is replaced by the session
    _log(request, "session_created")
    return {
        "created_at": session.created_at.isoformat(),
        "expires_at": session.expires_at.isoformat(),
        "next": CONVERSATION_ROUTE,
    }


@router.get("/session")
def read_own_session(session: Annotated[Session, Depends(current_session)]) -> dict[str, str]:
    # Always the caller's own record: nothing in the request can select another session.
    return {
        "created_at": session.created_at.isoformat(),
        "expires_at": session.expires_at.isoformat(),
    }


@router.delete("/session", status_code=204, dependencies=[Depends(require_csrf)])
def end_own_session(
    request: Request,
    session: Annotated[Session, Depends(current_session)],
    service: Annotated[AccessService, Depends(get_service)],
) -> Response:
    service.end_session(session.session_id)
    _log(request, "session_ended")
    response = Response(status_code=204)
    response.delete_cookie(SESSION_COOKIE, **_COOKIE)
    return response


async def dependency_unavailable(request: Request, exc: DependencyUnavailable) -> Response:
    # Fail closed with retry guidance; the category is logged, never provider detail.
    _log(request, "dependency_unavailable", exc.category)
    return error_response(
        503,
        "service_unavailable",
        "The service is temporarily unavailable. Please try again shortly.",
        correlation_id_of(request),
        {"Retry-After": "5"},
    )
