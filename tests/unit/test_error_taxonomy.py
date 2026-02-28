import pytest

from app.domain.error_taxonomy import (
    classify_error,
    is_canonical_error_code,
    resolve_stage_error,
)


@pytest.mark.unit
def test_canonical_error_codes_are_enforced() -> None:
    assert is_canonical_error_code("schema_validation_failed") is True
    assert is_canonical_error_code("unknown_error") is False


@pytest.mark.unit
def test_stage_error_mapping_restricts_invalid_codes() -> None:
    assert resolve_stage_error(stage="normalized", code="schema_validation_failed") == "schema_validation_failed"
    assert resolve_stage_error(stage="normalized", code="delivery_transport_failed") == "internal_error"


@pytest.mark.unit
def test_retry_classification_distinguishes_terminal_and_recoverable() -> None:
    assert classify_error("llm_provider_unavailable") == "recoverable"
    assert classify_error("validation_error") == "terminal"
