from __future__ import annotations

from io import BytesIO
from pathlib import PurePosixPath
from typing import Final

from pydantic import ValidationError

from app.domain.dto import NormalizePayloadCommand, NormalizePayloadResult
from app.lib.artifacts.types import NormalizedArtifact

COMPONENT_ID: Final[str] = "domain.normalize.payload"


def _infer_ext(artifact_ref: str) -> str:
    key = artifact_ref.split("://", maxsplit=1)[1] if "://" in artifact_ref else artifact_ref
    name = PurePosixPath(key).name
    return PurePosixPath(name).suffix.lower()


def _extract_text(payload: bytes, ext: str) -> str:
    if ext in (".txt", ".md"):
        return payload.decode("utf-8", errors="replace").strip()

    if ext == ".docx":
        from docx import Document  # type: ignore

        doc = Document(BytesIO(payload))
        parts: list[str] = []
        for p in doc.paragraphs:
            t = (p.text or "").strip()
            if t:
                parts.append(t)
        return "\n\n".join(parts).strip()

    if ext == ".pdf":
        from pypdf import PdfReader  # type: ignore

        reader = PdfReader(BytesIO(payload))
        parts: list[str] = []
        for page in reader.pages:
            t = (page.extract_text() or "").strip()
            if t:
                parts.append(t)
        return "\n\n".join(parts).strip()

    raise ValueError(f"unsupported format for normalization: {ext}")


def normalize_payload(cmd: NormalizePayloadCommand, *, payload: bytes) -> NormalizePayloadResult:
    ext = _infer_ext(cmd.artifact_ref)
    text = _extract_text(payload, ext)

    try:
        normalized_artifact = NormalizedArtifact(
            submission_public_id=cmd.submission_id,
            assignment_public_id="asg_00000000000000000000000000",
            source_type="api_upload",
            content_markdown=text if text else "",
            normalization_metadata={"producer": COMPONENT_ID, "ext": ext},
            schema_version="normalized:v1",
        )
    except ValidationError as exc:
        raise ValueError("normalized artifact schema validation failed") from exc

    return NormalizePayloadResult(
        normalized_artifact=normalized_artifact,
        schema_version="normalized:v1",
    )