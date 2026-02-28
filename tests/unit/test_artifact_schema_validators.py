import pytest
from pydantic import ValidationError

from app.lib.artifacts.types import (
    ExportRowArtifact,
    NormalizedArtifact,
)


@pytest.mark.unit
def test_normalized_artifact_requires_schema_fields() -> None:
    artifact = NormalizedArtifact(
        submission_public_id="sub_00000000000000000000000000",
        assignment_public_id="asg_00000000000000000000000000",
        source_type="api_upload",
        content_markdown="# normalized",
        normalization_metadata={"producer": "test"},
    )
    assert artifact.schema_version == "normalized:v1"


@pytest.mark.unit
def test_export_contract_includes_version_fields() -> None:
    export = ExportRowArtifact(
        candidate_identifier="cand_x",
        assignment_identifier="asg_x",
        score_1_10=8,
        criteria_summary="correctness:8",
        strengths="s",
        issues="i",
        recommendations="r",
        chain_version="chain:v1",
        model="model:v1",
        spec_version="chain-spec:v1",
        response_language="ru",
    )
    assert export.schema_version == "exports:v1"


@pytest.mark.unit
def test_export_contract_rejects_out_of_range_scores() -> None:
    with pytest.raises(ValidationError):
        ExportRowArtifact(
            candidate_identifier="cand_x",
            assignment_identifier="asg_x",
            score_1_10=11,
            criteria_summary="correctness:8",
            strengths="s",
            issues="i",
            recommendations="r",
            chain_version="chain:v1",
            model="model:v1",
            spec_version="chain-spec:v1",
            response_language="ru",
        )
