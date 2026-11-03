"""JSON logs that carry metadata only (no request or response content)."""

import json
import logging

_METADATA_FIELDS = ("correlation_id", "status", "exception_type")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {"level": record.levelname, "logger": record.name, "event": record.getMessage()}
        for field in _METADATA_FIELDS:
            if hasattr(record, field):
                payload[field] = getattr(record, field)
        # Tracebacks are deliberately omitted: exception text can embed user content.
        return json.dumps(payload)


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
