from __future__ import annotations

import pytest

from app.lib.artifacts.refs import canonical_ref_from_parts, parse_storage_ref, storage_key_from_ref


@pytest.mark.unit
def test_parse_storage_ref_for_canonical_s3_uri() -> None:
    parsed = parse_storage_ref("s3://real-bucket/raw/sub-1/input.txt")

    assert parsed.bucket == "real-bucket"
    assert parsed.object_key == "raw/sub-1/input.txt"
    assert storage_key_from_ref("s3://real-bucket/raw/sub-1/input.txt") == "raw/sub-1/input.txt"


@pytest.mark.unit
def test_parse_storage_ref_for_stage_prefixed_shorthand() -> None:
    parsed = parse_storage_ref("s3://normalized/sub-1.json")

    assert parsed.bucket is None
    assert parsed.object_key == "normalized/sub-1.json"
    assert storage_key_from_ref("normalized/sub-1.json") == "normalized/sub-1.json"


@pytest.mark.unit
def test_parse_storage_ref_rejects_malformed_or_unsupported_refs() -> None:
    with pytest.raises(ValueError, match="allowed prefix"):
        parse_storage_ref("s3://real-bucket/tmp/sub-1.txt")

    with pytest.raises(ValueError, match="must include bucket and key"):
        parse_storage_ref("s3://bucket-only")

    with pytest.raises(ValueError, match="unsupported artifact ref scheme"):
        parse_storage_ref("https://example.invalid/raw/sub-1.txt")


@pytest.mark.unit
def test_canonical_ref_from_parts_preserves_or_rebuilds_ref() -> None:
    assert canonical_ref_from_parts(bucket="real-bucket", object_key="exports/run-1.csv") == "s3://real-bucket/exports/run-1.csv"
    assert canonical_ref_from_parts(bucket="", object_key="eval/sub-1.json") == "eval/sub-1.json"
