from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, cast

from app.domain.contracts import STORAGE_PREFIXES


class S3BodyReader(Protocol):
    def read(self) -> bytes: ...


class S3ObjectClient(Protocol):
    def put_object(self, *, Bucket: str, Key: str, Body: bytes) -> object: ...

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, object]: ...


@dataclass(frozen=True)
class S3StorageClient:
    bucket: str
    s3_client: S3ObjectClient

    def put_bytes(self, *, key: str, payload: bytes) -> str:
        _validate_storage_key(key)
        try:
            self.s3_client.put_object(Bucket=self.bucket, Key=key, Body=payload)
        except Exception as exc:
            raise ValueError(f"storage put failed for key '{key}': {exc}") from exc
        return f"s3://{self.bucket}/{key}"

    def get_bytes(self, *, key: str) -> bytes:
        _validate_storage_key(key)
        try:
            response = self.s3_client.get_object(Bucket=self.bucket, Key=key)
        except Exception as exc:
            if _is_not_found(exc):
                raise KeyError(f"storage key not found: {key}") from exc
            raise ValueError(f"storage get failed for key '{key}': {exc}") from exc

        body = response.get("Body")
        if body is None or not hasattr(body, "read"):
            raise ValueError(f"storage get failed for key '{key}': missing response body")

        payload = cast(S3BodyReader, body).read()
        if not isinstance(payload, (bytes, bytearray)):
            raise ValueError(f"storage get failed for key '{key}': response body must be bytes")
        return bytes(payload)


def build_s3_storage_client(
    *,
    endpoint_url: str,
    bucket: str,
    access_key_id: str,
    secret_access_key: str,
    region: str,
) -> S3StorageClient:
    try:
        import boto3
    except ImportError as exc:
        raise ValueError("boto3 must be installed to use real S3 storage client") from exc

    client_kwargs: dict[str, object] = {
        "region_name": region,
        "aws_access_key_id": access_key_id,
        "aws_secret_access_key": secret_access_key,
    }
    if endpoint_url:
        client_kwargs["endpoint_url"] = endpoint_url

    sdk_client = boto3.client("s3", **client_kwargs)
    return S3StorageClient(bucket=bucket, s3_client=cast(S3ObjectClient, sdk_client))


def _validate_storage_key(key: str) -> None:
    if not any(key.startswith(prefix) for prefix in STORAGE_PREFIXES):
        raise ValueError("storage key must start with an allowed stage prefix")


def _is_not_found(exc: Exception) -> bool:
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return False

    error = response.get("Error")
    if not isinstance(error, dict):
        return False

    code = error.get("Code")
    if not isinstance(code, str):
        return False

    normalized_code = code.strip()
    return normalized_code in {"NoSuchKey", "NotFound", "404"}
