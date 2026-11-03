import logging

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.main import create_app

SECRET_TEXT = "my-confidential-plan-text"


def _envelope_is_safe(response, status, code):
    body = response.json()
    assert response.status_code == status
    assert set(body) == {"error"}
    error = body["error"]
    assert set(error) == {"code", "message", "correlation_id"}
    assert error["code"] == code
    assert error["correlation_id"] == response.headers["x-correlation-id"]
    assert SECRET_TEXT not in response.text
    assert "Traceback" not in response.text
    return error


def _app_with_failing_routes(settings) -> FastAPI:
    # No frontend mount, so routes added here are not shadowed by the catch-all static mount.
    app = create_app(settings.model_copy(update={"static_dir": settings.static_dir / "absent"}))

    class Payload(BaseModel):
        count: int

    @app.get("/api/boom")
    def boom():
        raise RuntimeError(f"failed while handling {SECRET_TEXT}")

    @app.get("/api/teapot")
    def teapot():
        raise HTTPException(status_code=418, detail=SECRET_TEXT)

    @app.post("/api/echo")
    def echo(payload: Payload):
        return payload

    return app


def test_unexpected_exception_returns_generic_500(settings):
    client = TestClient(_app_with_failing_routes(settings), raise_server_exceptions=False)
    response = client.get("/api/boom")
    error = _envelope_is_safe(response, 500, "internal_error")
    assert error["message"] == "Something went wrong. Please try again."
    assert response.headers["x-content-type-options"] == "nosniff"


def test_http_exception_detail_is_not_leaked(settings):
    client = TestClient(_app_with_failing_routes(settings), raise_server_exceptions=False)
    _envelope_is_safe(client.get("/api/teapot"), 418, "request_failed")


def test_validation_error_does_not_echo_submitted_input(settings):
    client = TestClient(_app_with_failing_routes(settings), raise_server_exceptions=False)
    response = client.post("/api/echo", json={"count": SECRET_TEXT})
    _envelope_is_safe(response, 422, "invalid_request")


def test_unknown_api_path_is_json_envelope(client):
    _envelope_is_safe(client.get("/api/nope"), 404, "not_found")


def test_wrong_method_is_json_envelope(client):
    _envelope_is_safe(client.post("/api/health"), 405, "method_not_allowed")


def test_logs_carry_correlation_id_but_no_exception_text(settings):
    records: list[logging.LogRecord] = []

    class Collector(logging.Handler):
        def emit(self, record):
            records.append(record)

    # Attached directly: create_app() reconfigures root handlers, which hides pytest's caplog.
    client = TestClient(_app_with_failing_routes(settings), raise_server_exceptions=False)
    logger = logging.getLogger("app.errors")
    handler = Collector()
    logger.addHandler(handler)
    try:
        response = client.get("/api/boom")
    finally:
        logger.removeHandler(handler)

    record = next(r for r in records if r.getMessage() == "unhandled_exception")
    assert record.correlation_id == response.headers["x-correlation-id"]
    assert record.exception_type == "RuntimeError"
    assert SECRET_TEXT not in "".join(r.getMessage() for r in records)
    assert not any(r.exc_info for r in records)
