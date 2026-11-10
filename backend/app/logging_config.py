"""JSON logs that carry metadata only (no request or response content)."""

import json
import logging

_METADATA_FIELDS = (
    "correlation_id",
    "status",
    "exception_type",
    "model_id",
    "stop_reason",
    "failure_code",
    "input_tokens",
    "output_tokens",
    "latency_ms",
    "http_status",
    "request_id",
    "request_shape",
    "error_text",
    "reason",
    "detail",
)

# At DEBUG these libraries log request and response parameters, i.e. prompts and
# completions. They are held at WARNING regardless of LOG_LEVEL.
_SDK_LOGGERS = ("boto3", "botocore", "urllib3", "s3transfer")


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
    for name in _SDK_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
