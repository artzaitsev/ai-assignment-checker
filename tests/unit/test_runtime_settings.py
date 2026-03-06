from __future__ import annotations

from pathlib import Path

import pytest

from app.main import run
from app.services.runtime_settings import (
    integration_mode_from_env,
    runtime_validation_mode_from_env,
    validate_runtime_configuration_for_role,
)


RUNTIME_ENV_KEYS = (
    "INTEGRATION_MODE",
    "RUNTIME_VALIDATION_MODE",
    "DATABASE_URL",
    "DB_URL",
    "S3_ENDPOINT_URL",
    "S3_URL",
    "S3_BUCKET",
    "S3_ACCESS_KEY_ID",
    "AWS_ACCESS_KEY_ID",
    "S3_SECRET_ACCESS_KEY",
    "AWS_SECRET_ACCESS_KEY",
    "S3_REGION",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_TOKEN",
    "LLM_API_KEY",
    "LLM_BASE_URL",
    "LLM_MODEL",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_MODEL",
)


def _clear_runtime_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in RUNTIME_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


@pytest.mark.unit
def test_runtime_validation_mode_defaults_to_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_runtime_env(monkeypatch)

    assert runtime_validation_mode_from_env() == "dev"


@pytest.mark.unit
def test_runtime_validation_mode_rejects_invalid_value(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_runtime_env(monkeypatch)
    monkeypatch.setenv("RUNTIME_VALIDATION_MODE", "staging")

    with pytest.raises(ValueError, match="RUNTIME_VALIDATION_MODE"):
        runtime_validation_mode_from_env()


@pytest.mark.unit
def test_integration_mode_defaults_to_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_runtime_env(monkeypatch)

    assert integration_mode_from_env() == "stub"


@pytest.mark.unit
def test_integration_mode_rejects_invalid_value(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_runtime_env(monkeypatch)
    monkeypatch.setenv("INTEGRATION_MODE", "hybrid")

    with pytest.raises(ValueError, match="INTEGRATION_MODE"):
        integration_mode_from_env()


@pytest.mark.unit
def test_strict_mode_aggregates_missing_required_values(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_runtime_env(monkeypatch)
    monkeypatch.setenv("INTEGRATION_MODE", "real")
    monkeypatch.setenv("RUNTIME_VALIDATION_MODE", "strict")

    with pytest.raises(ValueError) as exc_info:
        validate_runtime_configuration_for_role(role_name="worker-evaluate")

    message = str(exc_info.value)
    assert "database" in message
    assert "DATABASE_URL" in message
    assert "llm" in message
    assert "LLM_API_KEY" in message
    assert "LLM_BASE_URL" in message
    assert "LLM_MODEL" in message


@pytest.mark.unit
def test_strict_mode_uses_defaults_for_optional_values(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_runtime_env(monkeypatch)
    monkeypatch.setenv("INTEGRATION_MODE", "real")
    monkeypatch.setenv("RUNTIME_VALIDATION_MODE", "strict")
    monkeypatch.setenv("DATABASE_URL", "postgres://app:app@localhost:5432/app")
    monkeypatch.setenv("S3_ENDPOINT_URL", "http://localhost:9000")
    monkeypatch.setenv("S3_BUCKET", "artifacts")
    monkeypatch.setenv("S3_ACCESS_KEY_ID", "test-key")
    monkeypatch.setenv("S3_SECRET_ACCESS_KEY", "test-secret")

    validate_runtime_configuration_for_role(role_name="worker-normalize")


@pytest.mark.unit
def test_strict_stub_mode_does_not_require_real_integrations(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_runtime_env(monkeypatch)
    monkeypatch.setenv("INTEGRATION_MODE", "stub")
    monkeypatch.setenv("RUNTIME_VALIDATION_MODE", "strict")

    validate_runtime_configuration_for_role(role_name="worker-evaluate")


@pytest.mark.unit
def test_alias_keys_fail_fast_in_dev_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_runtime_env(monkeypatch)
    monkeypatch.setenv("RUNTIME_VALIDATION_MODE", "dev")
    monkeypatch.setenv("OPENAI_API_KEY", "legacy-key")

    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        validate_runtime_configuration_for_role(role_name="worker-evaluate")


@pytest.mark.unit
def test_alias_keys_fail_fast_in_strict_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_runtime_env(monkeypatch)
    monkeypatch.setenv("INTEGRATION_MODE", "real")
    monkeypatch.setenv("RUNTIME_VALIDATION_MODE", "strict")
    monkeypatch.setenv("TELEGRAM_TOKEN", "legacy-token")

    with pytest.raises(ValueError, match="TELEGRAM_TOKEN"):
        validate_runtime_configuration_for_role(role_name="worker-ingest-telegram")


@pytest.mark.unit
def test_dry_run_loads_required_values_from_dotenv(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _clear_runtime_env(monkeypatch)
    workdir = tmp_path / "dotenv-startup"
    workdir.mkdir()
    (workdir / ".env").write_text(
        "INTEGRATION_MODE=real\nRUNTIME_VALIDATION_MODE=strict\nDATABASE_URL=postgres://app:app@localhost:5432/app\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(workdir)

    exit_code = run(["--role", "api", "--dry-run-startup"])

    assert exit_code == 0


@pytest.mark.unit
def test_process_env_overrides_dotenv_values(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _clear_runtime_env(monkeypatch)
    workdir = tmp_path / "dotenv-precedence"
    workdir.mkdir()
    (workdir / ".env").write_text(
        "INTEGRATION_MODE=real\nRUNTIME_VALIDATION_MODE=strict\nDATABASE_URL=not-a-url\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(workdir)
    monkeypatch.setenv("DATABASE_URL", "postgres://app:app@localhost:5432/app")

    exit_code = run(["--role", "api", "--dry-run-startup"])

    assert exit_code == 0
