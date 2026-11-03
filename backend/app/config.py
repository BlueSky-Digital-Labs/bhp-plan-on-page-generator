"""Deployment configuration, kept separate from secrets.

`Settings` holds non-secret configuration and is safe to log. Secrets live in
`Secrets`, which wraps values in `SecretStr` so they never appear in a repr or
log line. In AWS, secret values come from Secrets Manager via the task role, not
from the image or from any Vite build-time variable.
"""

from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr, ValidationError, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    @property
    def is_deployed(self) -> bool:
        return self.app_env is AppEnv.DEPLOYED

    @field_validator("static_dir")
    @classmethod
    def _deployed_requires_frontend_build(cls, value: Path, info: ValidationInfo) -> Path:
        if info.data.get("app_env") is AppEnv.DEPLOYED and not (value / "index.html").is_file():
            raise ValueError("must contain the compiled frontend when deployed")
        return value


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
