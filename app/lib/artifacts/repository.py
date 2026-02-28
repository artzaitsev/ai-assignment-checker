from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.domain.contracts import ArtifactRepository, StorageClient
from app.lib.artifacts.codecs import (
    decode_normalized,
    encode_export_rows,
    encode_normalized,
)
from app.lib.artifacts.types import ExportRowArtifact, NormalizedArtifact

CompatPolicy = Literal["strict", "compatible"]

SCHEMA_VERSION_BY_CONTRACT: dict[str, dict[str, str]] = {
    "v1": {
        "normalized": "normalized:v1",
        "exports": "exports:v1",
    }
}


@dataclass(frozen=True)
class VersionedArtifactRepository(ArtifactRepository):
    storage: StorageClient
    active_contract_version: str = "v1"
    compat_policy: CompatPolicy = "strict"

    def __post_init__(self) -> None:
        if self.active_contract_version not in SCHEMA_VERSION_BY_CONTRACT:
            raise ValueError(f"unsupported artifact contract version: {self.active_contract_version}")
        if self.compat_policy not in ("strict", "compatible"):
            raise ValueError(f"unsupported artifact compat policy: {self.compat_policy}")

    def load_normalized(self, *, artifact_ref: str) -> NormalizedArtifact:
        payload = self.storage.get_bytes(key=_storage_key_from_ref(artifact_ref))
        artifact = decode_normalized(payload)
        self._validate_schema("normalized", artifact.schema_version)
        return artifact

    def save_normalized(self, *, submission_id: str, artifact: NormalizedArtifact) -> str:
        self._validate_schema("normalized", artifact.schema_version)
        key = f"normalized/{submission_id}.json"
        self.storage.put_bytes(key=key, payload=encode_normalized(artifact))
        # For normalized artifacts we keep key-like refs to preserve existing API expectations.
        return key

    def save_export_rows(self, *, export_id: str, rows: list[ExportRowArtifact]) -> str:
        for row in rows:
            self._validate_schema("exports", row.schema_version)
        key = f"exports/{export_id}.csv"
        return self.storage.put_bytes(key=key, payload=encode_export_rows(rows))

    def _validate_schema(self, artifact_kind: str, actual_schema_version: str) -> None:
        expected_schema_version = SCHEMA_VERSION_BY_CONTRACT[self.active_contract_version][artifact_kind]
        if actual_schema_version == expected_schema_version:
            return

        if self.compat_policy == "compatible":
            expected_family = expected_schema_version.split(":", maxsplit=1)[0]
            actual_family = actual_schema_version.split(":", maxsplit=1)[0]
            if expected_family == actual_family:
                return

        raise ValueError(
            f"artifact schema mismatch for {artifact_kind}: expected {expected_schema_version}, got {actual_schema_version}"
        )


def _storage_key_from_ref(ref: str) -> str:
    if "://" in ref:
        return ref.split("://", maxsplit=1)[1]
    return ref
