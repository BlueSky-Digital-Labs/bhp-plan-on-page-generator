"""Deployment configuration, kept separate from secrets.

`Settings` holds non-secret configuration and is safe to log. Secrets live in
`Secrets`, which wraps values in `SecretStr` so they never appear in a repr or
log line. In AWS, secret values come from Secrets Manager via the task role, not
from the image or from any Vite build-time variable.
"""

import re
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, ValidationError, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.mailer.message import is_address

MAX_BRANDING_CHARS = 80
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f\u2028\u2029]")


class AppEnv(StrEnum):
    LOCAL = "local"
    TEST = "test"
    DEPLOYED = "deployed"


class ConfigError(RuntimeError):
    """Startup configuration is missing or invalid.

    The message names the offending settings only. It never includes values, so
    it is safe to print and to ship to logs.
    """


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    # Required, with no default: a deployment that forgets it must not start.
    app_env: AppEnv
    aws_region: str = "ap-southeast-2"
    log_level: str = "INFO"
    # Compiled Vite assets. Required to exist when deployed.
    static_dir: Path = Path("static")

    # Access control. These are identifiers (names/ARNs), not secret values. All three are
    # required when deployed so a misconfigured deployment fails at startup, not at first use.
    # Where the two access secrets live: "secretsmanager" (the design) or "parameterstore", for an
    # account whose policies deny Secrets Manager. With parameterstore the two *_secret_id settings
    # are parameter names and start with "/".
    secrets_backend: Literal["secretsmanager", "parameterstore"] = "secretsmanager"
    sessions_table_name: str | None = Field(default=None, validate_default=True)
    access_code_hash_secret_id: str | None = Field(default=None, validate_default=True)
    cookie_signing_key_secret_id: str | None = Field(default=None, validate_default=True)

    # Where assistant replies come from. "unavailable" never fabricates a reply. "test" is a
    # scripted producer for presentation tests (never when deployed). "interviewer_bedrock" is the
    # guided interview using the verified, approved Bedrock configuration; it refuses to start
    # without one.
    conversation_producer: Literal["unavailable", "test", "interviewer_bedrock"] = "unavailable"

    # What the app calls itself on screen, in the browser tab, and in the appendix and the email.
    # Read at startup and served to the page, so changing them needs a restart, not a rebuild.
    app_name: str = Field(default="Plan on a Page", validate_default=True)
    header_text: str = Field(default="Plan on a Page", validate_default=True)  # beside the logo
    footer_text: str = Field(default="BHP", validate_default=True)

    # Emailing the canvas and appendix. "unavailable" sends nothing; "ses" uses Amazon SES from the
    # one verified sender address below; "test" accepts and discards (presentation tests only,
    # never when deployed). With "ses" an allow-list of recipient domains is required, so the app
    # cannot be used to email arbitrary outsiders.
    email_delivery: Literal["unavailable", "ses", "test"] = "unavailable"
    email_sender_address: str | None = Field(default=None, validate_default=True)
    email_allowed_domains: str = Field(default="", validate_default=True)  # comma-separated
    email_configuration_set: str | None = None
    ses_region: str | None = None  # the SES region, when it is not the app's own

    @property
    def allowed_email_domains(self) -> tuple[str, ...]:
        parts = (d.strip().lstrip("@").casefold() for d in self.email_allowed_domains.split(","))
        return tuple(dict.fromkeys(d for d in parts if d))

    @property
    def is_deployed(self) -> bool:
        return self.app_env is AppEnv.DEPLOYED

    @field_validator("static_dir")
    @classmethod
    def _deployed_requires_frontend_build(cls, value: Path, info: ValidationInfo) -> Path:
        if info.data.get("app_env") is AppEnv.DEPLOYED and not (value / "index.html").is_file():
            raise ValueError("must contain the compiled frontend when deployed")
        return value

    @field_validator("conversation_producer")
    @classmethod
    def _no_test_producer_when_deployed(cls, value: str, info: ValidationInfo) -> str:
        if value == "test" and info.data.get("app_env") is AppEnv.DEPLOYED:
            raise ValueError("not allowed when deployed")
        return value

    @field_validator("access_code_hash_secret_id", "cookie_signing_key_secret_id")
    @classmethod
    def _parameter_names_start_with_a_slash(cls, value: str | None, info: ValidationInfo):
        if (
            value
            and info.data.get("secrets_backend") == "parameterstore"
            and not value.startswith("/")
        ):
            raise ValueError("must be a parameter name starting with / for parameterstore")
        return value

    @field_validator("app_name", "header_text", "footer_text")
    @classmethod
    def _plain_short_text(cls, value: str) -> str:
        text = re.sub(r"\s+", " ", value).strip()
        if not text or len(text) > MAX_BRANDING_CHARS or _CONTROL.search(text):
            raise ValueError(f"must be 1 to {MAX_BRANDING_CHARS} plain characters")
        return text

    @field_validator("email_delivery")
    @classmethod
    def _no_test_mailer_when_deployed(cls, value: str, info: ValidationInfo) -> str:
        if value == "test" and info.data.get("app_env") is AppEnv.DEPLOYED:
            raise ValueError("not allowed when deployed")
        return value

    @field_validator("email_sender_address")
    @classmethod
    def _ses_needs_a_sender(cls, value: str | None, info: ValidationInfo) -> str | None:
        if info.data.get("email_delivery") == "ses" and not (value and is_address(value.strip())):
            raise ValueError("a plain email address is required for ses")
        return value.strip() if value else None

    @field_validator("email_allowed_domains")
    @classmethod
    def _ses_needs_allowed_domains(cls, value: str, info: ValidationInfo) -> str:
        if info.data.get("email_delivery") == "ses" and not value.replace(",", "").strip():
            raise ValueError("required for ses")
        return value

    @field_validator(
        "sessions_table_name", "access_code_hash_secret_id", "cookie_signing_key_secret_id"
    )
    @classmethod
    def _required_when_deployed(cls, value: str | None, info: ValidationInfo) -> str | None:
        if info.data.get("app_env") is AppEnv.DEPLOYED and not value:
            raise ValueError("required when deployed")
        return value or None


class Secrets(BaseSettings):
    """Secret material. Optional until the features that use it land."""

    model_config = SettingsConfigDict(extra="ignore")

    access_code_hash: SecretStr | None = None
    cookie_signing_key: SecretStr | None = None


def failed_fields(exc: ValidationError) -> str:
    # Pydantic's own message echoes input values; report field names only.
    names = sorted({".".join(str(p) for p in err["loc"]) or "settings" for err in exc.errors()})
    return ", ".join(names)


@lru_cache
def get_settings() -> Settings:
    try:
        return Settings()  # type: ignore[call-arg]  # populated from the environment
    except ValidationError as exc:
        raise ConfigError(f"Invalid or missing configuration: {failed_fields(exc)}") from None


@lru_cache
def get_secrets() -> Secrets:
    try:
        return Secrets()
    except ValidationError as exc:
        raise ConfigError(f"Invalid secrets configuration: {failed_fields(exc)}") from None
