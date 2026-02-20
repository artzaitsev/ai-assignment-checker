import pytest

from app.roles import SUPPORTED_ROLES, validate_role


@pytest.mark.unit
@pytest.mark.parametrize("role", SUPPORTED_ROLES)
def test_supported_role_is_accepted(role: str) -> None:
    validated = validate_role(role)
    assert validated.name == role


@pytest.mark.unit
def test_invalid_role_rejected_with_actionable_message() -> None:
    with pytest.raises(ValueError) as exc_info:
        validate_role("worker-unknown")

    message = str(exc_info.value)
    assert "Unsupported role 'worker-unknown'" in message
    assert "Supported roles:" in message
    assert "migrator is external" in message
