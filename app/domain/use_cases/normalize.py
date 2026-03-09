from __future__ import annotations

import io
import json
import logging
import re
import xml.etree.ElementTree as ET
from typing import Literal
from zipfile import BadZipFile, ZipFile

from pydantic import ValidationError

from app.domain.contracts import LLMClient
from app.domain.dto import (
    LLMClientRequest,
    NormalizePayloadCommand,
    NormalizePayloadResult,
    NormalizationParserInput,
    NormalizationParserOutput,
    NormalizationTaskSolution,
    OfficeExtractionResult,
)
from app.lib.artifacts.types import NormalizedArtifact

COMPONENT_ID = "domain.normalize.payload"
logger = logging.getLogger("runtime")
_TEXT_MIME_HINTS = ("text/", "application/json", "application/xml")
_DOCX_MIME_HINTS = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/msword",
}
_ODT_MIME_HINTS = {
    "application/vnd.oasis.opendocument.text",
    "application/x-vnd.oasis.opendocument.text",
}
_DOCX_NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
}
_ODT_NS = {
    "draw": "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0",
    "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
    "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
}
_DOCX_DRAWING_TAG = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}drawing"
_DOCX_OBJECT_TAG = "{urn:schemas-microsoft-com:office:office}OLEObject"
_DOCX_PICT_TAG = "{urn:schemas-microsoft-com:vml}shape"
_ODT_IMAGE_TAG = "{urn:oasis:names:tc:opendocument:xmlns:drawing:1.0}image"
_SUPPORTED_OFFICE_FORMATS = {"docx", "odt"}
_PARSE_FAILED_MESSAGE = "Supported file format could not be parsed."
_NORMALIZATION_PARSER_SYSTEM_PROMPT = "NORMALIZATION_PARSER: return strict JSON with task_solutions[] and unmapped_text"
_NORMALIZATION_REPAIR_SYSTEM_PROMPT = (
    "NORMALIZATION_PARSER_REPAIR: return strict JSON object with task_solutions[] only; "
    "include only missing_task_ids, one entry per task_id, each answer must be string"
)


def normalize_payload(cmd: NormalizePayloadCommand, *, llm: LLMClient) -> NormalizePayloadResult:
    """Build normalized:v2 artifact for supported plain-text and office submissions."""
    source_kind = _detect_submission_kind(
        filename=cmd.filename,
        persisted_mime=cmd.persisted_mime,
        payload=cmd.raw_payload,
    )

    office_extraction: OfficeExtractionResult | None = None
    if source_kind == "plain_text":
        submission_text = _normalize_submission_text(_decode_plain_text(cmd.raw_payload))
    else:
        office_extraction = _extract_office_document(payload=cmd.raw_payload, file_format=source_kind)
        submission_text = office_extraction.submission_text

    parser_input = NormalizationParserInput(
        assignment_public_id=cmd.assignment_public_id,
        language=cmd.assignment_language,
        tasks=cmd.assignment_tasks,
        submission_text=submission_text,
    )
    parser_output = _invoke_normalization_parser(parser_input=parser_input, llm=llm)

    try:
        normalized_artifact = NormalizedArtifact(
            submission_public_id=cmd.submission_id,
            assignment_public_id=cmd.assignment_public_id,
            source_type=cmd.source_type,
            submission_text=submission_text,
            task_solutions=[{"task_id": item.task_id, "answer": item.answer} for item in parser_output.task_solutions],
            unmapped_text=parser_output.unmapped_text,
            schema_version="normalized:v2",
        )
    except ValidationError as exc:
        raise ValueError("normalized artifact schema validation failed") from exc

    return NormalizePayloadResult(
        normalized_artifact=normalized_artifact,
        schema_version="normalized:v2",
        office_extraction=office_extraction,
    )


def _detect_submission_kind(*, filename: str, persisted_mime: str | None, payload: bytes) -> Literal["plain_text", "docx", "odt"]:
    signature_kind = _sniff_office_package_format(payload)
    if signature_kind is not None:
        return signature_kind

    office_hint = _office_format_from_extension(filename) or _office_format_from_mime(persisted_mime)
    if office_hint is not None and payload.startswith(b"PK"):
        return office_hint

    if _is_supported_plain_text(filename=filename, persisted_mime=persisted_mime, payload=payload):
        return "plain_text"
    raise ValueError("unsupported submission format")


def _is_supported_plain_text(*, filename: str, persisted_mime: str | None, payload: bytes) -> bool:
    normalized_name = filename.strip().lower()
    extension = normalized_name.rsplit(".", maxsplit=1)[1] if "." in normalized_name else ""
    suffixless = "." not in normalized_name
    extension_allowed = extension in {"txt", "md"}
    mime_hint_allowed = isinstance(persisted_mime, str) and persisted_mime.strip().lower().startswith(_TEXT_MIME_HINTS)
    candidate = extension_allowed or suffixless or bool(mime_hint_allowed)
    return candidate and _sniff_plain_text_bytes(payload)


def _sniff_plain_text_bytes(payload: bytes) -> bool:
    if not payload:
        return True
    if b"\x00" in payload:
        return False
    control_bytes = 0
    for value in payload:
        if value in (9, 10, 13):
            continue
        if value < 32:
            control_bytes += 1
    if control_bytes / max(1, len(payload)) > 0.02:
        return False
    try:
        _decode_plain_text(payload)
    except ValueError:
        return False
    return True


def _decode_plain_text(payload: bytes) -> str:
    if payload.startswith(b"\xef\xbb\xbf"):
        return payload.decode("utf-8-sig")
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return payload.decode("cp1251")
        except UnicodeDecodeError as exc:
            raise ValueError("unsupported plain-text encoding") from exc


def _office_format_from_extension(filename: str) -> Literal["docx", "odt"] | None:
    normalized_name = filename.strip().lower()
    if normalized_name.endswith(".docx"):
        return "docx"
    if normalized_name.endswith(".odt"):
        return "odt"
    return None


def _office_format_from_mime(persisted_mime: str | None) -> Literal["docx", "odt"] | None:
    if not isinstance(persisted_mime, str):
        return None
    normalized_mime = persisted_mime.strip().lower()
    if normalized_mime in _DOCX_MIME_HINTS:
        return "docx"
    if normalized_mime in _ODT_MIME_HINTS:
        return "odt"
    return None


def _sniff_office_package_format(payload: bytes) -> Literal["docx", "odt"] | None:
    if not payload.startswith(b"PK"):
        return None
    try:
        with ZipFile(io.BytesIO(payload)) as archive:
            names = set(archive.namelist())
            if "word/document.xml" in names and "[Content_Types].xml" in names:
                return "docx"
            if "mimetype" in names:
                mimetype = archive.read("mimetype").decode("utf-8", errors="ignore").strip()
                if mimetype == "application/vnd.oasis.opendocument.text":
                    return "odt"
            if "content.xml" in names and "META-INF/manifest.xml" in names:
                return "odt"
    except (BadZipFile, KeyError):
        return None
    return None


def _normalize_submission_text(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    normalized = normalized.lstrip("\ufeff")
    return normalized.strip(" \t\n")


def _extract_office_document(*, payload: bytes, file_format: Literal["docx", "odt"]) -> OfficeExtractionResult:
    try:
        with ZipFile(io.BytesIO(payload)) as archive:
            if file_format == "docx":
                return _extract_docx_submission(archive)
            return _extract_odt_submission(archive)
    except (BadZipFile, ET.ParseError, KeyError, UnicodeDecodeError, ValueError) as exc:
        raise ValueError(_PARSE_FAILED_MESSAGE) from exc


def _extract_docx_submission(archive: ZipFile) -> OfficeExtractionResult:
    root = ET.fromstring(archive.read("word/document.xml"))
    body = root.find("w:body", _DOCX_NS)
    if body is None:
        raise ValueError("missing document body")

    blocks: list[str] = []
    embedded_image_count = 0
    for child in body:
        if child.tag == _docx_tag("p"):
            paragraph_text, paragraph_images = _extract_docx_paragraph(child)
            embedded_image_count += paragraph_images
            if paragraph_text:
                blocks.append(paragraph_text)
            if child.find("./w:pPr/w:sectPr", _DOCX_NS) is not None:
                blocks.append("")
        elif child.tag == _docx_tag("tbl"):
            table_rows, table_images = _extract_docx_table(child)
            embedded_image_count += table_images
            blocks.extend(table_rows)
        elif child.tag == _docx_tag("sectPr"):
            blocks.append("")

    submission_text = _finalize_extracted_blocks(blocks)
    if not submission_text:
        raise ValueError("empty docx body")
    return OfficeExtractionResult(
        detected_format="docx",
        submission_text=submission_text,
        embedded_image_count=embedded_image_count,
    )


def _extract_docx_paragraph(paragraph: ET.Element) -> tuple[str, int]:
    parts: list[str] = []
    embedded_image_count = 0
    for run in paragraph.findall(".//w:r", _DOCX_NS):
        embedded_image_count += _count_docx_images(run)
        run_text = _extract_docx_run_text(run)
        if not run_text:
            continue
        parts.append(_apply_docx_inline_formatting(run, run_text))

    paragraph_text = _normalize_inline_text("".join(parts))
    if paragraph_text and paragraph.find("./w:pPr/w:numPr", _DOCX_NS) is not None:
        paragraph_text = f"- {paragraph_text}"
    return paragraph_text, embedded_image_count


def _extract_docx_run_text(run: ET.Element) -> str:
    parts: list[str] = []
    for node in run.iter():
        if node.tag == _docx_tag("t"):
            parts.append(node.text or "")
        elif node.tag == _docx_tag("tab"):
            parts.append("\t")
        elif node.tag in {_docx_tag("br"), _docx_tag("cr")}:
            parts.append("\n")
    return "".join(parts)


def _apply_docx_inline_formatting(run: ET.Element, text: str) -> str:
    if not text.strip():
        return text
    is_bold = _docx_run_property_enabled(run, "b")
    is_italic = _docx_run_property_enabled(run, "i")
    if is_bold and is_italic:
        return f"***{text}***"
    if is_bold:
        return f"**{text}**"
    if is_italic:
        return f"_{text}_"
    return text


def _docx_run_property_enabled(run: ET.Element, tag_name: str) -> bool:
    prop = run.find(f"./w:rPr/w:{tag_name}", _DOCX_NS)
    if prop is None:
        return False
    value = prop.attrib.get(_docx_attr("val"), "true").strip().lower()
    return value not in {"0", "false", "off"}


def _extract_docx_table(table: ET.Element) -> tuple[list[str], int]:
    rows: list[str] = []
    embedded_image_count = 0
    for row in table.findall("w:tr", _DOCX_NS):
        cells: list[str] = []
        for cell in row.findall("w:tc", _DOCX_NS):
            cell_parts: list[str] = []
            for paragraph in cell.findall("w:p", _DOCX_NS):
                paragraph_text, paragraph_images = _extract_docx_paragraph(paragraph)
                embedded_image_count += paragraph_images
                if paragraph_text:
                    cell_parts.append(paragraph_text)
            cell_text = _normalize_inline_text(" ".join(cell_parts))
            if cell_text:
                cells.append(cell_text)
        if cells:
            rows.append(" | ".join(cells))
    return rows, embedded_image_count


def _count_docx_images(element: ET.Element) -> int:
    return sum(1 for node in element.iter() if node.tag in {_DOCX_DRAWING_TAG, _DOCX_OBJECT_TAG, _DOCX_PICT_TAG})


def _extract_odt_submission(archive: ZipFile) -> OfficeExtractionResult:
    root = ET.fromstring(archive.read("content.xml"))
    office_text = root.find(".//office:body/office:text", _ODT_NS)
    if office_text is None:
        raise ValueError("missing office body")

    blocks: list[str] = []
    embedded_image_count = 0
    for child in office_text:
        child_blocks, child_images = _extract_odt_block(child)
        embedded_image_count += child_images
        blocks.extend(child_blocks)

    submission_text = _finalize_extracted_blocks(blocks)
    if not submission_text:
        raise ValueError("empty odt body")
    return OfficeExtractionResult(
        detected_format="odt",
        submission_text=submission_text,
        embedded_image_count=embedded_image_count,
    )


def _extract_odt_block(element: ET.Element) -> tuple[list[str], int]:
    if element.tag in {_odt_tag("text", "p"), _odt_tag("text", "h")}:
        text = _normalize_inline_text(_extract_odt_inline_text(element))
        return ([text] if text else []), _count_odt_images(element)

    if element.tag == _odt_tag("text", "list"):
        blocks: list[str] = []
        embedded_image_count = 0
        for item in element.findall("text:list-item", _ODT_NS):
            item_parts: list[str] = []
            for child in item:
                child_blocks, child_images = _extract_odt_block(child)
                embedded_image_count += child_images
                item_parts.extend(child_blocks)
            item_text = _normalize_inline_text(" ".join(item_parts))
            if item_text:
                blocks.append(f"- {item_text}")
        return blocks, embedded_image_count

    if element.tag == _odt_tag("table", "table"):
        rows: list[str] = []
        embedded_image_count = 0
        for row in element.findall("table:table-row", _ODT_NS):
            cells: list[str] = []
            for cell in row.findall("table:table-cell", _ODT_NS):
                cell_blocks: list[str] = []
                for child in cell:
                    child_blocks, child_images = _extract_odt_block(child)
                    embedded_image_count += child_images
                    cell_blocks.extend(child_blocks)
                cell_text = _normalize_inline_text(" ".join(cell_blocks))
                if cell_text:
                    cells.append(cell_text)
            if cells:
                rows.append(" | ".join(cells))
        return rows, embedded_image_count

    return [], _count_odt_images(element)


def _extract_odt_inline_text(element: ET.Element) -> str:
    parts: list[str] = []
    if element.text:
        parts.append(element.text)
    for child in element:
        if child.tag == _odt_tag("text", "s"):
            parts.append(" " * int(child.attrib.get(_odt_attr("text", "c"), "1")))
        elif child.tag == _odt_tag("text", "tab"):
            parts.append("\t")
        elif child.tag == _odt_tag("text", "line-break"):
            parts.append("\n")
        else:
            parts.append(_extract_odt_inline_text(child))
        if child.tail:
            parts.append(child.tail)
    return "".join(parts)


def _count_odt_images(element: ET.Element) -> int:
    return sum(1 for node in element.iter() if node.tag == _ODT_IMAGE_TAG)


def _finalize_extracted_blocks(blocks: list[str]) -> str:
    normalized_blocks: list[str] = []
    previous_blank = False
    for block in blocks:
        normalized_block = _normalize_block_text(block)
        if not normalized_block:
            if normalized_blocks and not previous_blank:
                normalized_blocks.append("")
            previous_blank = True
            continue
        normalized_blocks.append(normalized_block)
        previous_blank = False
    while normalized_blocks and normalized_blocks[-1] == "":
        normalized_blocks.pop()
    return _normalize_submission_text("\n".join(normalized_blocks))


def _normalize_block_text(text: str) -> str:
    lines = [_normalize_inline_text(line) for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    return "\n".join(line for line in lines if line)


def _normalize_inline_text(text: str) -> str:
    return re.sub(r"[ \t]+", " ", text).strip()


def _docx_tag(local_name: str) -> str:
    return f"{{{_DOCX_NS['w']}}}{local_name}"


def _docx_attr(local_name: str) -> str:
    return f"{{{_DOCX_NS['w']}}}{local_name}"


def _odt_tag(prefix: str, local_name: str) -> str:
    return f"{{{_ODT_NS[prefix]}}}{local_name}"


def _odt_attr(prefix: str, local_name: str) -> str:
    return f"{{{_ODT_NS[prefix]}}}{local_name}"


def _invoke_normalization_parser(*, parser_input: NormalizationParserInput, llm: LLMClient) -> NormalizationParserOutput:
    payload = {
        "assignment_public_id": parser_input.assignment_public_id,
        "language": parser_input.language,
        "assignment_tasks": [
            {
                "task_id": task.task_id,
                "task_index": task.task_index,
                "task_text": task.task_text,
            }
            for task in parser_input.tasks
        ],
        "submission_text": parser_input.submission_text,
    }
    result = llm.evaluate(
        LLMClientRequest(
            system_prompt=_NORMALIZATION_PARSER_SYSTEM_PROMPT,
            user_prompt=json.dumps(payload, ensure_ascii=False, sort_keys=True),
            model="normalization-parser:v1",
            temperature=0.0,
            seed=42,
            response_language=parser_input.language,
        )
    )

    raw_output: object = result.raw_json
    if raw_output is None:
        try:
            raw_output = json.loads(result.raw_text)
        except json.JSONDecodeError as exc:
            raise ValueError("normalization parser output is not valid JSON") from exc
    expected_task_ids = tuple(task.task_id for task in parser_input.tasks)
    parser_output = _decode_parser_output(raw_output, expected_task_ids=expected_task_ids)
    return _repair_missing_task_answers(parser_input=parser_input, parser_output=parser_output, llm=llm)


def _repair_missing_task_answers(
    *,
    parser_input: NormalizationParserInput,
    parser_output: NormalizationParserOutput,
    llm: LLMClient,
) -> NormalizationParserOutput:
    expected_task_ids = tuple(task.task_id for task in parser_input.tasks)
    current_by_task_id: dict[str, str] = {solution.task_id: solution.answer for solution in parser_output.task_solutions}
    missing_task_ids = tuple(
        task_id for task_id in expected_task_ids if task_id not in current_by_task_id or not current_by_task_id[task_id].strip()
    )
    if not missing_task_ids:
        return parser_output

    repair_payload = {
        "assignment_public_id": parser_input.assignment_public_id,
        "language": parser_input.language,
        "assignment_tasks": [
            {
                "task_id": task.task_id,
                "task_index": task.task_index,
                "task_text": task.task_text,
            }
            for task in parser_input.tasks
        ],
        "submission_text": parser_input.submission_text,
        "missing_task_ids": list(missing_task_ids),
    }
    repair_result = llm.evaluate(
        LLMClientRequest(
            system_prompt=_NORMALIZATION_REPAIR_SYSTEM_PROMPT,
            user_prompt=json.dumps(repair_payload, ensure_ascii=False, sort_keys=True),
            model="normalization-parser:v1",
            temperature=0.0,
            seed=42,
            response_language=parser_input.language,
        )
    )

    repair_raw_output: object = repair_result.raw_json
    if repair_raw_output is None:
        try:
            repair_raw_output = json.loads(repair_result.raw_text)
        except json.JSONDecodeError:
            logger.warning(
                "normalization parser repair output is not valid JSON; keeping original parser output",
                extra={"component": COMPONENT_ID},
            )
            return parser_output

    repaired_answers = _decode_repair_task_answers(raw_output=repair_raw_output, missing_task_ids=missing_task_ids)
    if not repaired_answers:
        return parser_output

    merged_solutions: list[NormalizationTaskSolution] = []
    for solution in parser_output.task_solutions:
        if solution.task_id in repaired_answers and (not solution.answer.strip() or repaired_answers[solution.task_id].strip()):
            merged_solutions.append(NormalizationTaskSolution(task_id=solution.task_id, answer=repaired_answers[solution.task_id]))
        else:
            merged_solutions.append(solution)

    present_ids = {solution.task_id for solution in merged_solutions}
    for task_id in missing_task_ids:
        if task_id in repaired_answers and task_id not in present_ids:
            merged_solutions.append(NormalizationTaskSolution(task_id=task_id, answer=repaired_answers[task_id]))

    logger.info(
        "normalization parser repair pass completed",
        extra={
            "component": COMPONENT_ID,
            "missing_task_ids": list(missing_task_ids),
            "repaired_task_ids": sorted(repaired_answers.keys()),
        },
    )
    return NormalizationParserOutput(task_solutions=tuple(merged_solutions), unmapped_text=parser_output.unmapped_text)


def _decode_repair_task_answers(*, raw_output: object, missing_task_ids: tuple[str, ...]) -> dict[str, str]:
    if not isinstance(raw_output, dict):
        logger.warning(
            "normalization parser repair output must be object; keeping original parser output",
            extra={"component": COMPONENT_ID},
        )
        return {}
    raw_task_solutions = raw_output.get("task_solutions")
    if not isinstance(raw_task_solutions, list):
        logger.warning(
            "normalization parser repair output.task_solutions must be array; keeping original parser output",
            extra={"component": COMPONENT_ID},
        )
        return {}

    allowed_task_ids = set(missing_task_ids)
    repaired_answers: dict[str, str] = {}
    for item in raw_task_solutions:
        if not isinstance(item, dict):
            continue
        raw_task_id = item.get("task_id")
        if not isinstance(raw_task_id, str) or raw_task_id not in allowed_task_ids or raw_task_id in repaired_answers:
            continue
        answer = item.get("answer", item.get("solution", ""))
        repaired_answers[raw_task_id] = _coerce_answer_text(answer=answer, task_id=raw_task_id)
    return repaired_answers


def _decode_parser_output(raw_output: object, *, expected_task_ids: tuple[str, ...]) -> NormalizationParserOutput:
    if not isinstance(raw_output, dict):
        raise ValueError("normalization parser output must be a JSON object")
    task_solutions_raw = raw_output.get("task_solutions")
    unmapped_text = raw_output.get("unmapped_text")
    if not isinstance(task_solutions_raw, list):
        raise ValueError("normalization parser output.task_solutions must be array")
    if not isinstance(unmapped_text, str):
        raise ValueError("normalization parser output.unmapped_text must be string")

    task_solutions: list[NormalizationTaskSolution] = []
    seen_task_ids: set[str] = set()
    answer_missing = object()
    for index, entry in enumerate(task_solutions_raw):
        if not isinstance(entry, dict):
            raise ValueError("normalization parser output.task_solutions[] must be object")
        task_id = entry.get("task_id")
        answer: object = entry.get("answer", answer_missing)
        if answer is answer_missing:
            answer = entry.get("solution", answer_missing)
        if not isinstance(task_id, str) or not task_id:
            task_id = _fallback_task_id(index=index, expected_task_ids=expected_task_ids)
            logger.warning(
                "normalization parser omitted task_id; using fallback from assignment order",
                extra={"component": COMPONENT_ID, "task_id": task_id, "index": index},
            )
        if answer is answer_missing:
            logger.warning(
                "normalization parser omitted answer; coercing to empty string",
                extra={"component": COMPONENT_ID, "task_id": task_id},
            )
            answer = ""
        answer_text = _coerce_answer_text(answer=answer, task_id=task_id)
        if task_id in seen_task_ids:
            logger.warning(
                "normalization parser produced duplicate task_id; keeping first answer",
                extra={"component": COMPONENT_ID, "task_id": task_id},
            )
            continue
        seen_task_ids.add(task_id)
        task_solutions.append(NormalizationTaskSolution(task_id=task_id, answer=answer_text))

    return NormalizationParserOutput(task_solutions=tuple(task_solutions), unmapped_text=unmapped_text)


def _fallback_task_id(*, index: int, expected_task_ids: tuple[str, ...]) -> str:
    if expected_task_ids:
        if index < len(expected_task_ids):
            return expected_task_ids[index]
        return expected_task_ids[-1]
    return f"task_{index + 1}"


def _coerce_answer_text(*, answer: object, task_id: str) -> str:
    if isinstance(answer, str):
        return answer
    if answer is None:
        logger.warning(
            "normalization parser produced null answer; coercing to empty string",
            extra={"component": COMPONENT_ID, "task_id": task_id},
        )
        return ""
    if isinstance(answer, (int, float, bool)):
        logger.warning(
            "normalization parser produced non-string answer; coercing to text",
            extra={"component": COMPONENT_ID, "task_id": task_id, "answer_type": type(answer).__name__},
        )
        return str(answer)
    if isinstance(answer, (list, dict)):
        logger.warning(
            "normalization parser produced structured answer; coercing to JSON string",
            extra={"component": COMPONENT_ID, "task_id": task_id, "answer_type": type(answer).__name__},
        )
        return json.dumps(answer, ensure_ascii=False, sort_keys=True)
    logger.warning(
        "normalization parser produced unsupported answer type; coercing to text",
        extra={"component": COMPONENT_ID, "task_id": task_id, "answer_type": type(answer).__name__},
    )
    return str(answer)
