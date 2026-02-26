from __future__ import annotations

from io import BytesIO
import json

import pytest

from app.clients.stub import StubStorageClient
from app.domain.dto import NormalizePayloadCommand
from app.domain.errors import UnsupportedFormatError
from app.domain.use_cases.normalize import NORMALIZED_SCHEMA_VERSION, normalize_payload


def _normalized_doc(storage: StubStorageClient, ref: str) -> dict[str, object]:
    return json.loads(storage.get_bytes(ref=ref).decode("utf-8"))


@pytest.mark.unit
def test_normalize_txt_to_unified_v1() -> None:
    storage = StubStorageClient()
    raw_ref = storage.put_bytes(key="raw/sub-1/answer.txt", payload=b"hello\n\nworld")

    result = normalize_payload(
        NormalizePayloadCommand(submission_id="sub-1", artifact_ref=raw_ref),
        storage=storage,
    )

    assert result.schema_version == NORMALIZED_SCHEMA_VERSION
    assert result.normalized_ref.startswith("stub://normalized/sub-1.json")
    doc = _normalized_doc(storage, result.normalized_ref)
    assert doc["source"]["format"] == "txt"
    assert doc["content_markdown"] == "hello\n\nworld"


@pytest.mark.unit
def test_normalize_md_to_unified_v1() -> None:
    storage = StubStorageClient()
    raw_ref = storage.put_bytes(
        key="raw/sub-2/answer.md",
        payload=b"# Header\n\nText",
    )

    result = normalize_payload(
        NormalizePayloadCommand(submission_id="sub-2", artifact_ref=raw_ref),
        storage=storage,
    )

    doc = _normalized_doc(storage, result.normalized_ref)
    assert doc["source"]["format"] == "md"
    assert doc["content_markdown"] == "# Header\n\nText"


@pytest.mark.unit
def test_normalize_docx_to_unified_v1() -> None:
    docx = pytest.importorskip("docx")

    storage = StubStorageClient()
    document = docx.Document()
    document.add_paragraph("Docx line")
    buffer = BytesIO()
    document.save(buffer)
    raw_ref = storage.put_bytes(key="raw/sub-3/answer.docx", payload=buffer.getvalue())

    result = normalize_payload(
        NormalizePayloadCommand(submission_id="sub-3", artifact_ref=raw_ref),
        storage=storage,
    )

    doc = _normalized_doc(storage, result.normalized_ref)
    assert doc["source"]["format"] == "docx"
    assert "Docx line" in str(doc["content_markdown"])


@pytest.mark.unit
def test_normalize_pdf_to_unified_v1(monkeypatch: pytest.MonkeyPatch) -> None:
    storage = StubStorageClient()
    raw_ref = storage.put_bytes(key="raw/sub-4/answer.pdf", payload=b"%PDF-fake")

    monkeypatch.setattr("app.domain.normalization._parse_pdf", lambda payload: "PDF text")

    result = normalize_payload(
        NormalizePayloadCommand(submission_id="sub-4", artifact_ref=raw_ref),
        storage=storage,
    )

    doc = _normalized_doc(storage, result.normalized_ref)
    assert doc["source"]["format"] == "pdf"
    assert doc["content_markdown"] == "PDF text"


@pytest.mark.unit
def test_normalize_unsupported_extension_raises() -> None:
    storage = StubStorageClient()
    raw_ref = storage.put_bytes(key="raw/sub-5/answer.png", payload=b"png")

    with pytest.raises(UnsupportedFormatError):
        normalize_payload(
            NormalizePayloadCommand(submission_id="sub-5", artifact_ref=raw_ref),
            storage=storage,
        )

