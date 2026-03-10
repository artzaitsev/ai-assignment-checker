from __future__ import annotations

from io import BytesIO
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from app.domain.models import AssignmentSnapshot


_CONTENT_TYPES_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>
"""

_RELS_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>
"""


def build_assignment_template_docx(assignment: AssignmentSnapshot) -> bytes:
    if assignment.task_schema is None:
        raise ValueError("assignment task_schema is required for template export")

    paragraph_texts: list[str] = [
        "Шаблон выполнения задания",
        f"ID задания: {assignment.assignment_public_id}",
        "",
        "Название задания",
        assignment.title,
        "",
        "Описание",
    ]
    paragraph_texts.extend(_split_lines(assignment.description))
    paragraph_texts.extend(["", "Список заданий"])

    for index, task in enumerate(assignment.task_schema.tasks, start=1):
        paragraph_texts.append(f"{index}. {task.title}")
        paragraph_texts.append("Ваш ответ:")
        paragraph_texts.append("____________________________________________")
        paragraph_texts.append("")

    document_xml = _build_document_xml(paragraph_texts)

    stream = BytesIO()
    with ZipFile(stream, mode="w", compression=ZIP_DEFLATED) as archive:
        _write_fixed_file(archive, "[Content_Types].xml", _CONTENT_TYPES_XML)
        _write_fixed_file(archive, "_rels/.rels", _RELS_XML)
        _write_fixed_file(archive, "word/document.xml", document_xml)
    return stream.getvalue()


def _build_document_xml(paragraph_texts: list[str]) -> str:
    paragraphs = "".join(_paragraph_xml(text) for text in paragraph_texts)
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
        "<w:document xmlns:w=\"http://schemas.openxmlformats.org/wordprocessingml/2006/main\">"
        "<w:body>"
        f"{paragraphs}"
        "<w:sectPr><w:pgSz w:w=\"11906\" w:h=\"16838\"/>"
        "<w:pgMar w:top=\"1440\" w:right=\"1440\" w:bottom=\"1440\" w:left=\"1440\"/></w:sectPr>"
        "</w:body></w:document>"
    )


def _paragraph_xml(text: str) -> str:
    escaped = escape(text)
    return f"<w:p><w:r><w:t xml:space=\"preserve\">{escaped}</w:t></w:r></w:p>"


def _split_lines(text: str) -> list[str]:
    lines = text.splitlines()
    if not lines:
        return [""]
    return lines


def _write_fixed_file(archive: ZipFile, file_name: str, payload: str) -> None:
    info = ZipInfo(filename=file_name)
    info.date_time = (1980, 1, 1, 0, 0, 0)
    info.compress_type = ZIP_DEFLATED
    archive.writestr(info, payload.encode("utf-8"))
