from __future__ import annotations

from io import BytesIO

import pytest

from app.domain.dto import NormalizePayloadCommand
from app.domain.errors import UnsupportedFormatError
from app.domain.use_cases.normalize import NORMALIZED_SCHEMA_VERSION, normalize_payload


@pytest.mark.unit
def test_normalize_txt_to_unified_v1() -> None:
    result = normalize_payload(
        NormalizePayloadCommand(
            submission_id="sub_00000000000000000000000001",
            artifact_ref="raw/sub_00000000000000000000000001/answer.txt",
            assignment_public_id="asg_00000000000000000000000001",
            source_type="api_upload",
        ),
        raw_payload=b"hello\n\nworld",
    )

    artifact = result.normalized_artifact
    assert result.schema_version == NORMALIZED_SCHEMA_VERSION
    assert artifact.source_type == "api_upload"
    assert artifact.assignment_public_id == "asg_00000000000000000000000001"
    assert artifact.content_markdown == "hello\n\nworld"
    assert artifact.normalization_metadata["source_format"] == "txt"


@pytest.mark.unit
def test_normalize_md_to_unified_v1() -> None:
    result = normalize_payload(
        NormalizePayloadCommand(
            submission_id="sub_00000000000000000000000002",
            artifact_ref="raw/sub_00000000000000000000000002/answer.md",
            assignment_public_id="asg_00000000000000000000000002",
            source_type="api_upload",
        ),
        raw_payload=b"# Header\n\nText",
    )

    assert result.normalized_artifact.content_markdown == "# Header\n\nText"
    assert result.normalized_artifact.normalization_metadata["source_format"] == "md"


@pytest.mark.unit
def test_normalize_docx_to_unified_v1() -> None:
    docx = pytest.importorskip("docx")

    document = docx.Document()
    document.add_paragraph("Docx line")
    buffer = BytesIO()
    document.save(buffer)

    result = normalize_payload(
        NormalizePayloadCommand(
            submission_id="sub_00000000000000000000000003",
            artifact_ref="raw/sub_00000000000000000000000003/answer.docx",
            assignment_public_id="asg_00000000000000000000000003",
            source_type="api_upload",
        ),
        raw_payload=buffer.getvalue(),
    )

    assert "Docx line" in result.normalized_artifact.content_markdown
    assert result.normalized_artifact.normalization_metadata["source_format"] == "docx"


@pytest.mark.unit
def test_normalize_pdf_to_unified_v1(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.domain.normalization._parse_pdf", lambda payload: "PDF text")

    result = normalize_payload(
        NormalizePayloadCommand(
            submission_id="sub_00000000000000000000000004",
            artifact_ref="raw/sub_00000000000000000000000004/answer.pdf",
            assignment_public_id="asg_00000000000000000000000004",
            source_type="api_upload",
        ),
        raw_payload=b"%PDF-fake",
    )

    assert result.normalized_artifact.content_markdown == "PDF text"
    assert result.normalized_artifact.normalization_metadata["source_format"] == "pdf"


@pytest.mark.unit
def test_normalize_unsupported_extension_raises() -> None:
    with pytest.raises(UnsupportedFormatError):
        normalize_payload(
            NormalizePayloadCommand(
                submission_id="sub_00000000000000000000000005",
                artifact_ref="raw/sub_00000000000000000000000005/answer.png",
                assignment_public_id="asg_00000000000000000000000005",
                source_type="api_upload",
            ),
            raw_payload=b"png",
        )
