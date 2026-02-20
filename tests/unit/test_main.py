import pytest

from app.main import run


@pytest.mark.unit
def test_cli_returns_non_zero_for_invalid_role(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = run(["--role", "bad-role", "--dry-run-startup"])
    captured = capsys.readouterr()

    assert exit_code == 2
    assert "ERROR:" in captured.err
    assert "Supported roles" in captured.err


@pytest.mark.unit
def test_cli_dry_run_succeeds_for_valid_role() -> None:
    exit_code = run(["--role", "api", "--dry-run-startup"])
    assert exit_code == 0
