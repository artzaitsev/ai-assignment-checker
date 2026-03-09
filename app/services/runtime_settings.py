from __future__ import annotations

from dataclasses import dataclass
from os import environ as os_environ
from typing import Callable
from urllib.parse import urlparse

from app.domain.models import ApplySessionSettings, TelegramLinkSettings

INTEGRATION_MODE_STUB = "stub"
INTEGRATION_MODE_REAL = "real"
SUPPORTED_INTEGRATION_MODES = (
    INTEGRATION_MODE_STUB,
    INTEGRATION_MODE_REAL,
)

RUNTIME_VALIDATION_MODE_DEV = "dev"
RUNTIME_VALIDATION_MODE_STRICT = "strict"
SUPPORTED_RUNTIME_VALIDATION_MODES = (
    RUNTIME_VALIDATION_MODE_DEV,
    RUNTIME_VALIDATION_MODE_STRICT,
)

CANONICAL_ENV_ALIASES: dict[str, tuple[str, ...]] = {
    "DATABASE_URL": ("DB_URL",),
    "S3_ENDPOINT_URL": ("S3_URL",),
    "S3_ACCESS_KEY_ID": ("AWS_ACCESS_KEY_ID",),
    "S3_SECRET_ACCESS_KEY": ("AWS_SECRET_ACCESS_KEY",),
    "TELEGRAM_BOT_TOKEN": ("TELEGRAM_TOKEN",),
    "LLM_API_KEY": ("OPENAI_API_KEY",),
    "LLM_BASE_URL": ("OPENAI_BASE_URL",),
    "LLM_MODEL": ("OPENAI_MODEL",),
}


@dataclass(frozen=True)
class DatabaseRuntimeSettings:
    database_url: str


@dataclass(frozen=True)
class S3RuntimeSettings:
    endpoint_url: str
    bucket: str
    access_key_id: str
    secret_access_key: str
    region: str


@dataclass(frozen=True)
class TelegramBotRuntimeSettings:
    bot_token: str
    api_base_url: str


@dataclass(frozen=True)
class LLMRuntimeSettings:
    api_key: str
    base_url: str
    model: str


def integration_mode_from_env() -> str:
    mode = os_environ.get("INTEGRATION_MODE", INTEGRATION_MODE_STUB).strip().lower()
    if mode in SUPPORTED_INTEGRATION_MODES:
        return mode
    supported = ", ".join(SUPPORTED_INTEGRATION_MODES)
    raise ValueError(f"INTEGRATION_MODE must be one of: {supported}")


def runtime_validation_mode_from_env() -> str:
    mode = os_environ.get("RUNTIME_VALIDATION_MODE", RUNTIME_VALIDATION_MODE_DEV).strip().lower()
    if mode in SUPPORTED_RUNTIME_VALIDATION_MODES:
        return mode
    supported = ", ".join(SUPPORTED_RUNTIME_VALIDATION_MODES)
    raise ValueError(f"RUNTIME_VALIDATION_MODE must be one of: {supported}")


def validate_runtime_configuration_for_role(*, role_name: str) -> None:
    _validate_unsupported_alias_env_keys()
    integration_mode = integration_mode_from_env()
    if runtime_validation_mode_from_env() != RUNTIME_VALIDATION_MODE_STRICT:
        return

    if integration_mode != INTEGRATION_MODE_REAL:
        return

    validators: tuple[tuple[str, Callable[[], object]], ...] = _strict_role_validators(role_name)
    errors: list[str] = []
    for integration_name, validator in validators:
        try:
            validator()
        except ValueError as exc:
            errors.append(f"{integration_name}: {exc}")

    if errors:
        joined = "; ".join(errors)
        raise ValueError(f"Runtime configuration validation failed for role '{role_name}': {joined}")


def _strict_role_validators(role_name: str) -> tuple[tuple[str, Callable[[], object]], ...]:
    role_to_validators: dict[str, tuple[tuple[str, Callable[[], object]], ...]] = {
        "api": (("database", database_settings_from_env),),
        "worker-ingest-telegram": (
            ("database", database_settings_from_env),
            ("telegram", telegram_bot_settings_from_env),
        ),
        "worker-normalize": (
            ("database", database_settings_from_env),
            ("s3", s3_settings_from_env),
            ("llm", llm_settings_from_env),
        ),
        "worker-evaluate": (
            ("database", database_settings_from_env),
            ("llm", llm_settings_from_env),
        ),
        "worker-deliver": (
            ("database", database_settings_from_env),
            ("telegram", telegram_bot_settings_from_env),
        ),
    }
    return role_to_validators.get(role_name, ())


def _validate_unsupported_alias_env_keys() -> None:
    errors: list[str] = []
    for canonical_key, aliases in CANONICAL_ENV_ALIASES.items():
        for alias in aliases:
            alias_value = os_environ.get(alias, "").strip()
            if alias_value:
                errors.append(
                    f"{alias} is not supported; use canonical key {canonical_key}"
                )
    if errors:
        raise ValueError("; ".join(errors))


def database_settings_from_env() -> DatabaseRuntimeSettings:
    database_url = _read_required_value("DATABASE_URL")
    parsed = urlparse(database_url)
    if parsed.scheme not in {"postgres", "postgresql"} or not parsed.netloc:
        raise ValueError("DATABASE_URL must be an absolute postgres:// or postgresql:// URL")
    return DatabaseRuntimeSettings(database_url=database_url)


def s3_settings_from_env() -> S3RuntimeSettings:
    required_values, errors = _read_required_values(
        "S3_ENDPOINT_URL",
        "S3_BUCKET",
        "S3_ACCESS_KEY_ID",
        "S3_SECRET_ACCESS_KEY",
    )

    endpoint_url = required_values.get("S3_ENDPOINT_URL", "")
    if endpoint_url:
        try:
            _validate_http_url(value=endpoint_url, env_name="S3_ENDPOINT_URL")
        except ValueError as exc:
            errors.append(str(exc))

    if errors:
        raise ValueError("; ".join(errors))

    region = os_environ.get("S3_REGION", "us-east-1").strip()
    if not region:
        raise ValueError("S3_REGION must be a non-empty string")

    return S3RuntimeSettings(
        endpoint_url=endpoint_url,
        bucket=required_values["S3_BUCKET"],
        access_key_id=required_values["S3_ACCESS_KEY_ID"],
        secret_access_key=required_values["S3_SECRET_ACCESS_KEY"],
        region=region,
    )


def telegram_bot_settings_from_env() -> TelegramBotRuntimeSettings:
    bot_token = _read_required_value("TELEGRAM_BOT_TOKEN")
    api_base_url = os_environ.get("TELEGRAM_BOT_API_BASE_URL", "https://api.telegram.org").strip().rstrip("/")
    _validate_http_url(value=api_base_url, env_name="TELEGRAM_BOT_API_BASE_URL")
    return TelegramBotRuntimeSettings(bot_token=bot_token, api_base_url=api_base_url)


def llm_settings_from_env() -> LLMRuntimeSettings:
    required_values, errors = _read_required_values(
        "LLM_API_KEY",
        "LLM_BASE_URL",
        "LLM_MODEL",
    )
    base_url = required_values.get("LLM_BASE_URL", "")
    if base_url:
        try:
            _validate_http_url(value=base_url, env_name="LLM_BASE_URL")
        except ValueError as exc:
            errors.append(str(exc))

    if errors:
        raise ValueError("; ".join(errors))

    return LLMRuntimeSettings(
        api_key=required_values["LLM_API_KEY"],
        base_url=base_url,
        model=required_values["LLM_MODEL"],
    )


def _read_required_value(name: str) -> str:
    value = os_environ.get(name, "").strip()
    if not value:
        raise ValueError(f"{name} is required and must be a non-empty string")
    return value


def _read_required_values(*names: str) -> tuple[dict[str, str], list[str]]:
    values: dict[str, str] = {}
    errors: list[str] = []
    for name in names:
        value = os_environ.get(name, "").strip()
        if not value:
            errors.append(f"{name} is required and must be a non-empty string")
            continue
        values[name] = value
    return values, errors


def _validate_http_url(*, value: str, env_name: str) -> None:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{env_name} must be an absolute http(s) URL")

def telegram_link_settings_from_env() -> TelegramLinkSettings:
    base_url = os_environ.get("PUBLIC_WEB_BASE_URL", "http://localhost:8000").strip().rstrip("/")
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("PUBLIC_WEB_BASE_URL must be an absolute http(s) URL")

    signing_secret = os_environ.get("TELEGRAM_LINK_SIGNING_SECRET", "dev-telegram-link-secret").strip()
    if len(signing_secret) < 12:
        raise ValueError("TELEGRAM_LINK_SIGNING_SECRET must be at least 12 characters")

    ttl_raw = os_environ.get("TELEGRAM_LINK_TTL_SECONDS", "900").strip()
    try:
        ttl_seconds = int(ttl_raw)
    except ValueError as exc:
        raise ValueError("TELEGRAM_LINK_TTL_SECONDS must be an integer") from exc
    if ttl_seconds <= 0:
        raise ValueError("TELEGRAM_LINK_TTL_SECONDS must be > 0")

    return TelegramLinkSettings(
        public_web_base_url=base_url,
        signing_secret=signing_secret,
        ttl_seconds=ttl_seconds,
    )


def apply_session_settings_from_env() -> ApplySessionSettings:
    signing_secret = os_environ.get("APPLY_SESSION_SIGNING_SECRET", "dev-apply-session-secret").strip()
    if len(signing_secret) < 12:
        raise ValueError("APPLY_SESSION_SIGNING_SECRET must be at least 12 characters")

    ttl_raw = os_environ.get("APPLY_SESSION_TTL_SECONDS", "900").strip()
    try:
        ttl_seconds = int(ttl_raw)
    except ValueError as exc:
        raise ValueError("APPLY_SESSION_TTL_SECONDS must be an integer") from exc
    if ttl_seconds <= 0:
        raise ValueError("APPLY_SESSION_TTL_SECONDS must be > 0")

    return ApplySessionSettings(
        signing_secret=signing_secret,
        ttl_seconds=ttl_seconds,
    )
