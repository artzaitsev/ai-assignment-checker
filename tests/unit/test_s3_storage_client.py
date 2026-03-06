from __future__ import annotations

import pytest

from app.clients.s3 import S3StorageClient


class _Body:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return self._payload


class _FakeClientError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


class _FakeS3Client:
    def __init__(self) -> None:
        self.put_calls: list[tuple[str, str, bytes]] = []
        self.get_calls: list[tuple[str, str]] = []
        self.objects: dict[str, bytes] = {}

    def put_object(self, *, Bucket: str, Key: str, Body: bytes) -> object:
        self.put_calls.append((Bucket, Key, Body))
        self.objects[Key] = Body
        return {"ETag": "stub"}

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, object]:
        self.get_calls.append((Bucket, Key))
        if Key not in self.objects:
            raise _FakeClientError("NoSuchKey")
        return {"Body": _Body(self.objects[Key])}


@pytest.mark.unit
def test_put_and_get_bytes_with_valid_prefix_and_canonical_uri() -> None:
    sdk = _FakeS3Client()
    client = S3StorageClient(bucket="artifacts", s3_client=sdk)

    artifact_ref = client.put_bytes(key="raw/sub-1.txt", payload=b"hello")
    payload = client.get_bytes(key="raw/sub-1.txt")

    assert artifact_ref == "s3://artifacts/raw/sub-1.txt"
    assert payload == b"hello"
    assert sdk.put_calls == [("artifacts", "raw/sub-1.txt", b"hello")]
    assert sdk.get_calls == [("artifacts", "raw/sub-1.txt")]


@pytest.mark.unit
def test_invalid_prefix_is_rejected_without_sdk_calls() -> None:
    sdk = _FakeS3Client()
    client = S3StorageClient(bucket="artifacts", s3_client=sdk)

    with pytest.raises(ValueError, match="allowed stage prefix"):
        client.put_bytes(key="tmp/sub-1.txt", payload=b"hello")

    with pytest.raises(ValueError, match="allowed stage prefix"):
        client.get_bytes(key="tmp/sub-1.txt")

    assert sdk.put_calls == []
    assert sdk.get_calls == []


@pytest.mark.unit
def test_missing_object_maps_to_key_error() -> None:
    sdk = _FakeS3Client()
    client = S3StorageClient(bucket="artifacts", s3_client=sdk)

    with pytest.raises(KeyError, match="storage key not found: raw/missing.txt"):
        client.get_bytes(key="raw/missing.txt")

    assert sdk.get_calls == [("artifacts", "raw/missing.txt")]
