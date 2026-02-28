from __future__ import annotations

import csv
import io
import json

from app.lib.artifacts.types import (
    ExportRowArtifact,
    NormalizedArtifact,
)


def encode_normalized(artifact: NormalizedArtifact) -> bytes:
    return json.dumps(artifact.model_dump(mode="json"), sort_keys=True).encode("utf-8")


def decode_normalized(payload: bytes) -> NormalizedArtifact:
    return NormalizedArtifact.model_validate_json(payload)


def encode_export_rows(rows: list[ExportRowArtifact]) -> bytes:
    if not rows:
        return b""

    fieldnames = list(rows[0].model_dump(mode="json").keys())
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fieldnames)
    writer.writeheader()
    for row in rows:
        writer.writerow(row.model_dump(mode="json"))
    return buffer.getvalue().encode("utf-8")
