import pytest

from app.domain.artifacts import (
    ALLOWED_ARTIFACT_KEYS,
    STAGE_ARTIFACT_KEYS,
    artifact_keys_for_stage,
    put_artifact_ref,
)
from app.domain.lifecycle import STAGE_LIFECYCLES


@pytest.mark.unit
def test_stage_artifact_keys_cover_all_stage_lifecycles() -> None:
    assert set(STAGE_ARTIFACT_KEYS.keys()) == set(STAGE_LIFECYCLES.keys())
    for keys in STAGE_ARTIFACT_KEYS.values():
        for key in keys:
            assert key in ALLOWED_ARTIFACT_KEYS


@pytest.mark.unit
def test_llm_output_stage_has_no_storage_artifacts() -> None:
    assert artifact_keys_for_stage(stage="llm-output") == tuple()


@pytest.mark.unit
def test_put_artifact_ref_rejects_unknown_key() -> None:
    with pytest.raises(ValueError, match="unsupported artifact key"):
        put_artifact_ref(artifacts={}, key="unknown", artifact_ref="s3://unknown/ref")
