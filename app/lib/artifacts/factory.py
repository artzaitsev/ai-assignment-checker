from __future__ import annotations

import os
from typing import cast

from app.domain.contracts import ArtifactRepository, StorageClient
from app.lib.artifacts.repository import CompatPolicy, VersionedArtifactRepository

DEFAULT_ARTIFACT_CONTRACT_VERSION = "v1"
DEFAULT_ARTIFACT_COMPAT_POLICY = "strict"


def build_artifact_repository(
    *,
    storage: StorageClient,
    active_contract_version: str | None = None,
    compat_policy: str | None = None,
) -> ArtifactRepository:
    version = active_contract_version or os.getenv(
        "ARTIFACT_CONTRACT_VERSION", DEFAULT_ARTIFACT_CONTRACT_VERSION
    )
    policy = compat_policy or os.getenv("ARTIFACT_COMPAT_POLICY", DEFAULT_ARTIFACT_COMPAT_POLICY)
    if policy not in ("strict", "compatible"):
        raise ValueError(f"unsupported artifact compat policy: {policy}")

    return VersionedArtifactRepository(
        storage=storage,
        active_contract_version=version,
        compat_policy=cast(CompatPolicy, policy),
    )
