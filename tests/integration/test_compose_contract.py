from pathlib import Path

import pytest
import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _load_compose(filename: str) -> dict:
    compose_path = PROJECT_ROOT / filename
    return yaml.safe_load(compose_path.read_text(encoding="utf-8"))


@pytest.mark.integration
def test_compose_has_postgres_migrator_app_dependency_chain() -> None:
    compose = _load_compose("docker-compose.yml")
    services = compose["services"]

    assert "postgres" in services
    assert "migrator" in services

    app_services = [
        "api",
        "worker-ingest-telegram",
        "worker-normalize",
        "worker-llm",
        "worker-deliver",
    ]

    for service_name in app_services:
        service = services[service_name]
        depends_on = service["depends_on"]
        assert depends_on["postgres"]["condition"] == "service_healthy"
        assert depends_on["migrator"]["condition"] == "service_completed_successfully"


@pytest.mark.integration
def test_app_services_share_single_image_build_context() -> None:
    compose = _load_compose("docker-compose.yml")
    services = compose["services"]

    api_build = services["api"]["build"]
    for service_name in (
        "worker-ingest-telegram",
        "worker-normalize",
        "worker-llm",
        "worker-deliver",
    ):
        assert services[service_name]["build"] == api_build


@pytest.mark.integration
def test_prod_compose_has_no_local_source_mounts_for_app_services() -> None:
    compose = _load_compose("docker-compose.yml")
    services = compose["services"]

    for service_name in (
        "api",
        "worker-ingest-telegram",
        "worker-normalize",
        "worker-llm",
        "worker-deliver",
    ):
        volumes = services[service_name].get("volumes", [])
        assert "./:/app" not in volumes


@pytest.mark.integration
def test_compose_commands_use_uv_runtime() -> None:
    prod = _load_compose("docker-compose.yml")
    override = _load_compose("docker-compose.override.yml")

    for service_name in (
        "api",
        "worker-ingest-telegram",
        "worker-normalize",
        "worker-llm",
        "worker-deliver",
    ):
        assert prod["services"][service_name]["command"][0] == "uv"
        assert "uv run" in override["services"][service_name]["command"][2]


@pytest.mark.integration
def test_override_defines_fast_mode_and_full_worker_profile() -> None:
    override = _load_compose("docker-compose.override.yml")
    services = override["services"]

    assert "api" in services
    assert "./:/app" in services["api"].get("volumes", [])

    api_profiles = services["api"].get("profiles", [])
    assert "full" not in api_profiles

    for service_name in (
        "worker-ingest-telegram",
        "worker-normalize",
        "worker-llm",
        "worker-deliver",
    ):
        profiles = services[service_name].get("profiles", [])
        assert "full" in profiles
