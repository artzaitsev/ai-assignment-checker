from __future__ import annotations

import json
from pathlib import Path

import pytest


NORMALIZATION_DIR = Path("tests/data/normalization")
CASES_DIR = NORMALIZATION_DIR / "cases"


def _case_meta(case_dir: Path) -> dict[str, object]:
    return json.loads((case_dir / "meta.json").read_text(encoding="utf-8"))


@pytest.mark.integration
def test_normalization_entrypoints_use_committed_synthetic_fixtures_only() -> None:
    for case_dir in CASES_DIR.iterdir():
        if not case_dir.is_dir():
            continue
        meta = _case_meta(case_dir)
        assert "data_hidden" not in json.dumps(meta)
        assignment_fixture = NORMALIZATION_DIR / str(meta["assignment_fixture"])
        input_file = case_dir / str(meta["input_file"])
        assert assignment_fixture.exists()
        assert input_file.exists()


@pytest.mark.integration
def test_case_families_cover_plaintext_office_pdf_ocr_parser_and_errors() -> None:
    case_names = {entry.name for entry in CASES_DIR.iterdir() if entry.is_dir()}

    assert {"case_001_plain_text_ordered", "case_005_suffixless_plain_text"}.issubset(case_names)
    assert {"case_006_docx_text_only", "case_008_odt_text_only", "case_014_misnamed_docx_signature"}.issubset(case_names)
    assert {"case_009_pdf_native_text", "case_010_pdf_mixed_native_and_scanned"}.issubset(case_names)
    assert {"case_007_docx_embedded_image_needs_ocr", "case_011_ocr_heavy_submission"}.issubset(case_names)
    assert {"case_012_corrupt_supported_file", "case_013_unsupported_format", "case_015_corrupt_docx_supported"}.issubset(case_names)

    parser_dir = NORMALIZATION_DIR / "parser_io"
    assert any(parser_dir.glob("parser_input_*.json"))
    assert any(parser_dir.glob("parser_output_*.json"))
