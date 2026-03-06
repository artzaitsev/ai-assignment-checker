from __future__ import annotations

from dataclasses import dataclass

from app.domain.contracts import STORAGE_PREFIXES


@dataclass(frozen=True)
class StorageRefParts:
    bucket: str | None
    object_key: str


def parse_storage_ref(ref: str) -> StorageRefParts:
    raw_ref = ref.strip()
    if not raw_ref:
        raise ValueError("artifact ref must be a non-empty string")

    if "://" not in raw_ref:
        return StorageRefParts(bucket=None, object_key=_validated_storage_key(raw_ref))

    scheme, remainder = raw_ref.split("://", maxsplit=1)
    if scheme != "s3":
        raise ValueError(f"unsupported artifact ref scheme: {scheme}")

    if any(remainder.startswith(prefix) for prefix in STORAGE_PREFIXES):
        return StorageRefParts(bucket=None, object_key=_validated_storage_key(remainder))

    if "/" not in remainder:
        raise ValueError("s3 artifact ref must include bucket and key")

    bucket, object_key = remainder.split("/", maxsplit=1)
    if not bucket:
        raise ValueError("s3 artifact ref bucket must be non-empty")
    return StorageRefParts(bucket=bucket, object_key=_validated_storage_key(object_key))


def storage_key_from_ref(ref: str) -> str:
    return parse_storage_ref(ref).object_key


def canonical_ref_from_parts(*, bucket: str | None, object_key: str) -> str:
    key = _validated_storage_key(object_key)
    normalized_bucket = (bucket or "").strip()
    if not normalized_bucket:
        return key
    return f"s3://{normalized_bucket}/{key}"


def _validated_storage_key(key: str) -> str:
    if not any(key.startswith(prefix) for prefix in STORAGE_PREFIXES):
        prefixes = ", ".join(STORAGE_PREFIXES)
        raise ValueError(f"artifact ref must contain key with allowed prefix: {prefixes}")
    return key
