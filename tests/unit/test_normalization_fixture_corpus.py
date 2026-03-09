from __future__ import annotations

import json
from pathlib import Path
from zipfile import ZipFile

import pytest


NORMALIZATION_DIR = Path("tests/data/normalization")
CASES_DIR = NORMALIZATION_DIR / "cases"
EXPECTED_CASES = {
    "case_001_plain_text_ordered",
    "case_002_plain_text_repeated_prompts",
    "case_003_plain_text_answer_only",
    "case_004_plain_text_mixed_sql_commentary",
    "case_005_suffixless_plain_text",
    "case_006_docx_text_only",
    "case_007_docx_embedded_image_needs_ocr",
    "case_008_odt_text_only",
    "case_009_pdf_native_text",
    "case_010_pdf_mixed_native_and_scanned",
    "case_011_ocr_heavy_submission",
    "case_012_corrupt_supported_file",
    "case_013_unsupported_format",
    "case_014_misnamed_docx_signature",
    "case_015_corrupt_docx_supported",
}
NORMALIZED_V2_REQUIRED_FIELDS = {
    "submission_public_id",
    "assignment_public_id",
    "source_type",
    "submission_text",
    "task_solutions",
    "unmapped_text",
    "schema_version",
}


@pytest.mark.unit
def test_normalization_fixture_layout_and_parser_files_exist() -> None:
    assert (NORMALIZATION_DIR / "README.md").exists()
    assert (NORMALIZATION_DIR / "assignments" / "assignment_db_review_v1.json").exists()
    assert (NORMALIZATION_DIR / "assignments" / "assignment_sql_debug_v1.json").exists()

    observed_cases = {
        entry.name
        for entry in CASES_DIR.iterdir()
        if entry.is_dir()
    }
    assert observed_cases == EXPECTED_CASES

    parser_dir = NORMALIZATION_DIR / "parser_io"
    assert (parser_dir / "parser_input_repeated_prompts.json").exists()
    assert (parser_dir / "parser_output_repeated_prompts.json").exists()
    assert (parser_dir / "parser_input_answer_only.json").exists()
    assert (parser_dir / "parser_output_answer_only.json").exists()
    assert (parser_dir / "parser_output_malformed_missing_field.json").exists()
    assert (parser_dir / "parser_output_malformed_extra_text.txt").exists()


@pytest.mark.unit
def test_case_metadata_and_expected_outputs_follow_contract() -> None:
    for case_name in EXPECTED_CASES:
        case_dir = CASES_DIR / case_name
        meta = json.loads((case_dir / "meta.json").read_text(encoding="utf-8"))

        assert meta["case_id"] == case_name
        assert "data_hidden" not in json.dumps(meta)
        assert (NORMALIZATION_DIR / meta["assignment_fixture"]).exists()
        assert (case_dir / meta["input_file"]).exists()

        if meta["expects_error"]:
            expected_error = json.loads((case_dir / meta["expected_error"]).read_text(encoding="utf-8"))
            assert set(expected_error.keys()) == {"code", "message"}
        else:
            expected_output = json.loads((case_dir / meta["expected_output"]).read_text(encoding="utf-8"))
            assert set(expected_output.keys()) == NORMALIZED_V2_REQUIRED_FIELDS
            assert expected_output["schema_version"] == "normalized:v2"

        if meta["ocr_mode"] != "off":
            ocr_stub_path = case_dir / meta["ocr_stub"]
            assert ocr_stub_path.exists()
            ocr_stub = json.loads(ocr_stub_path.read_text(encoding="utf-8"))
            assert isinstance(ocr_stub.get("text"), str)


@pytest.mark.unit
def test_binary_fixtures_are_real_documents_and_ocr_cases_embed_images() -> None:
    docx_text = CASES_DIR / "case_006_docx_text_only" / "input.docx"
    docx_image = CASES_DIR / "case_007_docx_embedded_image_needs_ocr" / "input.docx"
    odt_text = CASES_DIR / "case_008_odt_text_only" / "input.odt"
    docx_misnamed = CASES_DIR / "case_014_misnamed_docx_signature" / "submission.bin"
    docx_corrupt = CASES_DIR / "case_015_corrupt_docx_supported" / "input.docx"
    pdf_native = CASES_DIR / "case_009_pdf_native_text" / "input.pdf"
    pdf_mixed = CASES_DIR / "case_010_pdf_mixed_native_and_scanned" / "input.pdf"
    pdf_ocr = CASES_DIR / "case_011_ocr_heavy_submission" / "input.pdf"

    assert docx_text.read_bytes().startswith(b"PK")
    assert docx_image.read_bytes().startswith(b"PK")
    assert odt_text.read_bytes().startswith(b"PK")
    assert docx_misnamed.read_bytes().startswith(b"PK")
    assert docx_corrupt.read_bytes().startswith(b"PK")

    assert pdf_native.read_bytes().startswith(b"%PDF")
    assert pdf_mixed.read_bytes().startswith(b"%PDF")
    assert pdf_ocr.read_bytes().startswith(b"%PDF")

    with ZipFile(docx_image) as archive:
        names = archive.namelist()
    assert any(name.startswith("word/media/") for name in names)

    mixed_pdf_bytes = pdf_mixed.read_bytes()
    ocr_pdf_bytes = pdf_ocr.read_bytes()
    assert b"/Image" in mixed_pdf_bytes
    assert b"/Image" in ocr_pdf_bytes
