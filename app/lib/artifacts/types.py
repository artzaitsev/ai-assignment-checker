from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# v1 contract models shared across stage producers/consumers.
# These schemas define payload shape and required trace fields; they do not
# implement business logic or data fetching.


class NormalizedArtifact(BaseModel):
    # Produced by normalize stage, consumed by evaluate stage.
    # Stable IDs to join artifact back to submission/assignment.
    submission_public_id: str
    assignment_public_id: str
    # Which ingress path created the source payload.
    source_type: Literal["api_upload", "telegram_webhook"]
    # Canonical text used as LLM input after format-specific extraction.
    content_markdown: str
    # Free-form trace data from normalization (parser, mime, warnings, etc.).
    normalization_metadata: dict[str, object]
    # Contract version for readers to branch parsing/migrations when needed.
    schema_version: str = Field(default="normalized:v1")


class ExportRowArtifact(BaseModel):
    # Stable tabular row contract for CSV/Sheets export.
    # Human-facing identifiers for organizer reporting.
    candidate_identifier: str
    assignment_identifier: str
    score_1_10: int = Field(ge=1, le=10)
    # Flattened text fields for spreadsheet compatibility.
    criteria_summary: str
    strengths: str
    issues: str
    recommendations: str
    # Chain metadata columns for reproducibility and reporting.
    chain_version: str
    model: str
    spec_version: str
    response_language: str
    schema_version: str = Field(default="exports:v1")
