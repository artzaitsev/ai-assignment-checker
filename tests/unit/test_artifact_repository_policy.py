import json

import pytest

from app.clients.stub import StubStorageClient
from app.lib.artifacts.factory import build_artifact_repository
from app.lib.artifacts.repository import VersionedArtifactRepository
from app.lib.artifacts.types import NormalizedArtifact


@pytest.mark.unit
def test_factory_defaults_to_strict_v1() -> None:
    repository = build_artifact_repository(storage=StubStorageClient())
    assert isinstance(repository, VersionedArtifactRepository)
    assert repository.active_contract_version == "v1"
    assert repository.compat_policy == "strict"


@pytest.mark.unit
def test_strict_policy_rejects_unknown_schema_version_on_load() -> None:
    storage = StubStorageClient()
    storage.put_bytes(
        key="normalized/sub-1.json",
        payload=json.dumps(
            {
                "submission_public_id": "sub-1",
                "assignment_public_id": "asg-1",
                "source_type": "api_upload",
                "content_markdown": "# content",
                "normalization_metadata": {},
                "schema_version": "normalized:v999",
            }
        ).encode("utf-8"),
    )
    repository = build_artifact_repository(storage=storage)

    with pytest.raises(ValueError, match="artifact schema mismatch"):
        repository.load_normalized(artifact_ref="normalized/sub-1.json")


@pytest.mark.unit
def test_strict_policy_rejects_unknown_schema_version_on_save() -> None:
    artifact = NormalizedArtifact(
        submission_public_id="sub-1",
        assignment_public_id="asg-1",
        source_type="api_upload",
        content_markdown="# content",
        normalization_metadata={},
        schema_version="normalized:v2",
    )
    repository = build_artifact_repository(storage=StubStorageClient())

    with pytest.raises(ValueError, match="artifact schema mismatch"):
        repository.save_normalized(submission_id="sub-1", artifact=artifact)
