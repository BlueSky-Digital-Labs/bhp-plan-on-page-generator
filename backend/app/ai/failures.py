"""Translate SDK exceptions into content-free `Failure` values.

Only the exception class, the AWS error *code*, the HTTP status and the request id are
read. The provider's message text is never touched, because it can echo request content.

One opt-in exception, for finding out why a model refuses a request in a test deployment:
with BEDROCK_LOG_ERROR_TEXT=true the message of a ValidationException or
ResourceNotFoundException is kept (control characters removed, cut at 300 characters).
Those messages describe the request's structure or the model id and can occasionally quote
part of it, so this is off by default and must not be left on once real data is used.
"""

import os
import re

from botocore.exceptions import (
    ClientError,
    ConnectionClosedError,
    ConnectTimeoutError,
    EndpointConnectionError,
    NoCredentialsError,
    ReadTimeoutError,
)

from app.ai.types import Failure, FailureCode

_BY_AWS_CODE = {
    "ThrottlingException": FailureCode.THROTTLED,
    "TooManyRequestsException": FailureCode.THROTTLED,
    "ModelTimeoutException": FailureCode.TIMEOUT,
    "ServiceUnavailableException": FailureCode.SERVICE_UNAVAILABLE,
    "InternalServerException": FailureCode.INTERNAL_ERROR,
    "ValidationException": FailureCode.INVALID_REQUEST,
    "AccessDeniedException": FailureCode.ACCESS_DENIED,
    "ResourceNotFoundException": FailureCode.MODEL_NOT_FOUND,
    "ModelNotReadyException": FailureCode.SERVICE_UNAVAILABLE,
    "ModelErrorException": FailureCode.STREAM_ERROR,
    "ModelStreamErrorException": FailureCode.STREAM_ERROR,
}

# Error keys that can arrive as events inside the stream itself.
STREAM_ERROR_EVENTS = {
    "internalServerException": "InternalServerException",
    "modelStreamErrorException": "ModelStreamErrorException",
    "validationException": "ValidationException",
    "throttlingException": "ThrottlingException",
    "serviceUnavailableException": "ServiceUnavailableException",
}


_TEXT_CODES = {"ValidationException", "ResourceNotFoundException"}
_MAX_ERROR_TEXT = 300


def _error_text(aws_code: str, message: object) -> str | None:
    if os.environ.get("BEDROCK_LOG_ERROR_TEXT", "").strip().lower() != "true":
        return None
    if aws_code not in _TEXT_CODES or not isinstance(message, str):
        return None
    text = re.sub(r"[\x00-\x1f\x7f-\x9f\s]+", " ", message).strip()
    return text[:_MAX_ERROR_TEXT] or None


def failure_from_aws_code(aws_code: str, *, partial_output: bool, **extra: object) -> Failure:
    code = _BY_AWS_CODE.get(aws_code, FailureCode.UNEXPECTED_ERROR)
    if not re.fullmatch(r"[A-Za-z0-9]{1,64}", aws_code):
        aws_code = "unknown"
    return Failure(code=code, partial_output=partial_output, exception_type=aws_code, **extra)  # type: ignore[arg-type]


def failure_from_exception(exc: Exception, *, partial_output: bool) -> Failure:
    if isinstance(exc, ClientError):  # includes botocore EventStreamError
        response = exc.response or {}
        meta = response.get("ResponseMetadata", {})
        error = response.get("Error", {})
        aws_code = str(error.get("Code", ""))
        return failure_from_aws_code(
            aws_code,
            partial_output=partial_output,
            http_status=meta.get("HTTPStatusCode"),
            request_id=meta.get("RequestId"),
            error_text=_error_text(aws_code, error.get("Message")),
        )
    if isinstance(exc, ReadTimeoutError | ConnectTimeoutError):
        code = FailureCode.TIMEOUT
    elif isinstance(exc, EndpointConnectionError | ConnectionClosedError):
        code = FailureCode.CONNECTION_ERROR
    elif isinstance(exc, NoCredentialsError):
        code = FailureCode.NO_CREDENTIALS
    else:
        code = FailureCode.UNEXPECTED_ERROR
    return Failure(code=code, partial_output=partial_output, exception_type=type(exc).__name__)
