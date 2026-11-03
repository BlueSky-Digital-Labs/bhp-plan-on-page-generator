import json

from app.ai import smoke
from tests.ai.conftest import (
    SECRET,
    FakeBedrockRuntime,
    client_error,
    message_stop,
    metadata,
    text,
    tool_events,
    validated,
)


class Scripted:
    """Serves one scripted stream per call, in order."""

    def __init__(self, *scripts):
        self.clients = [
            FakeBedrockRuntime(s)
            if not isinstance(s, Exception)
            else FakeBedrockRuntime(raise_on_call=s)
            for s in scripts
        ]
        self.calls = []

    def converse_stream(self, **kwargs):
        client = self.clients[len(self.calls)]
        self.calls.append(kwargs)
        return client.converse_stream(**kwargs)


def test_smoke_passes_for_streaming_and_structured_tool(config_b):
    client = Scripted(
        [text("Hi"), message_stop(), metadata()],
        tool_events("tu-1", "report_status", {"status": "ok"}),
    )
    summary = smoke.run_smoke(validated(config_b), client)
    assert summary["ok"] and summary["streaming"]["ok"] and summary["tool"]["ok"]
    assert summary["model_id"] == "au.test.model-b:1"
    # Model B supports forced tool choice, so the smoke test forces the tool.
    assert client.calls[1]["toolConfig"]["toolChoice"] == {"tool": {"name": "report_status"}}


def test_smoke_fails_on_malformed_tool_output_and_prints_no_content(config_b):
    client = Scripted(
        [text(SECRET), message_stop()],
        tool_events("tu-1", "report_status", {"status": SECRET}),
    )
    summary = smoke.run_smoke(validated(config_b), client)
    assert not summary["ok"] and summary["streaming"]["ok"] and not summary["tool"]["ok"]
    assert summary["tool"]["failure_code"] == "invalid_tool_input"
    assert SECRET not in json.dumps(summary)


def test_smoke_reports_provider_failure_with_request_id_only(config_b):
    client = Scripted(
        client_error("AccessDeniedException", 403), client_error("AccessDeniedException", 403)
    )
    summary = smoke.run_smoke(validated(config_b), client)
    assert summary["streaming"]["failure_code"] == "access_denied"
    assert summary["streaming"]["request_id"] == "req-123"
    assert SECRET not in json.dumps(summary)


def test_smoke_main_is_blocked_when_nothing_is_approved(capsys):
    assert smoke.main([]) == 2
    out = json.loads(capsys.readouterr().out)
    assert out == {"ok": False, "blocked": ["no_selection"], "settings": ["model_id"]}
