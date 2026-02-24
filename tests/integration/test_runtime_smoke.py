import shutil
import subprocess
import sys

import pytest


ROLES = [
    "api",
    "worker-ingest-telegram",
    "worker-normalize",
    "worker-evaluate",
    "worker-deliver",
]


@pytest.mark.integration
@pytest.mark.parametrize("role", ROLES)
def test_role_starts_in_empty_mode_via_dry_run(role: str) -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "app.main", "--role", role, "--dry-run-startup"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr


@pytest.mark.integration
def test_uv_smoke_script_exists_and_uses_venv_contract() -> None:
    script = "scripts/smoke_local_uv.sh"
    contents = open(script, encoding="utf-8").read()
    assert "uv run python -m app.main --role" in contents


@pytest.mark.integration
def test_uv_smoke_script_runs_when_uv_is_available() -> None:
    if shutil.which("uv") is None:
        pytest.skip("uv not installed in environment")

    proc = subprocess.run(
        ["bash", "scripts/smoke_local_uv.sh"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
