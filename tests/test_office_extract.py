"""Office/document extraction before the model sees the bytes (harness/tools/office.py).

`read_file` opens with `errors="replace"`, so a docx/xlsx/pptx opened as text is
replacement characters — the model answers about a document it never read. another harness's
read tool extracts locally (mammoth for docx, its own xlsx reader); this pins the
stdlib-only equivalent: paragraphs, sheet rows and slide text in document order, with
truncation said out loud.
"""

from __future__ import annotations

import json
import zipfile

from harness.tools.office import is_office, office_text_lines
from harness.tools.files import file_tools

_NS_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _write_zip(path, members: dict) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name, body in members.items():
            zf.writestr(name, body)


def _docx(tmp_path, paragraphs=("Hello world", "cell text")):
    path = tmp_path / "doc.docx"
    runs = "".join(
        f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>" for text in paragraphs
    )
    _write_zip(
        path,
        {
            "word/document.xml": (
                f'<w:document xmlns:w="{_NS_W}"><w:body>{runs}</w:body></w:document>'
            )
        },
    )
    return path


def test_a_docx_extracts_its_paragraphs(tmp_path):
    lines, error = office_text_lines(_docx(tmp_path))

    assert error == ""
    assert lines == ["Hello world", "cell text"]


def test_a_docx_table_cell_reads_in_document_order(tmp_path):
    path = tmp_path / "table.docx"
    _write_zip(
        path,
        {
            "word/document.xml": (
                f'<w:document xmlns:w="{_NS_W}"><w:body>'
                "<w:p><w:r><w:t>before</w:t></w:r></w:p>"
                "<w:tbl><w:tr><w:tc><w:p><w:r><w:t>in cell</w:t></w:r></w:p></w:tc></w:tr></w:tbl>"
                "</w:body></w:document>"
            )
        },
    )

    lines, _ = office_text_lines(path)

    assert lines == ["before", "in cell"]


def test_a_spreadsheet_extracts_rows_with_shared_strings_and_columns(tmp_path):
    path = tmp_path / "book.xlsx"
    _write_zip(
        path,
        {
            "xl/sharedStrings.xml": "<sst><si><t>alpha</t></si><si><t>beta</t></si></sst>",
            "xl/workbook.xml": '<workbook><sheets><sheet name="Data"/></sheets></workbook>',
            "xl/worksheets/sheet1.xml": (
                "<worksheet><sheetData>"
                '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>'
                '<row r="2"><c r="A2"><v>42</v></c></row>'
                "</sheetData></worksheet>"
            ),
        },
    )

    lines, error = office_text_lines(path)

    assert error == ""
    assert lines == ["--- sheet: Data ---", "alpha\tbeta", "42"]


def test_a_presentation_extracts_slide_text(tmp_path):
    path = tmp_path / "deck.pptx"
    slide = (
        '<p:sld xmlns:p="p" xmlns:a="a"><p:cSld><p:sp><p:txBody>'
        "<a:p><a:r><a:t>{}</a:t></a:r></a:p>"
        "</p:txBody></p:sp></p:cSld></p:sld>"
    )
    _write_zip(
        path,
        {"ppt/slides/slide1.xml": slide.format("Title"), "ppt/slides/slide2.xml": slide.format("Body")},
    )

    lines, _ = office_text_lines(path)

    assert lines == ["--- slide 1 ---", "Title", "--- slide 2 ---", "Body"]


def test_a_notebook_extracts_cells(tmp_path):
    path = tmp_path / "nb.ipynb"
    path.write_text(
        json.dumps(
            {
                "cells": [
                    {"cell_type": "markdown", "source": ["# Heading\n", "text"]},
                    {"cell_type": "code", "source": ["print(1)"]},
                ]
            }
        ),
        encoding="utf-8",
    )

    lines, _ = office_text_lines(path)

    # Numbered cells: an old notebook has no ids, so none are shown.
    assert lines == ["--- cell 1 · markdown ---", "# Heading", "text", "--- cell 2 · code ---", "print(1)"]


def test_a_broken_archive_is_an_error_not_mojibake(tmp_path):
    path = tmp_path / "broken.docx"
    path.write_bytes(b"this is not a zip")

    lines, error = office_text_lines(path)

    assert lines is None
    assert "could not extract text" in error


def test_an_empty_document_says_so(tmp_path):
    lines, _ = office_text_lines(_docx(tmp_path, paragraphs=()))

    assert lines == ["(this document contains no extractable text)"]


def test_read_file_routes_a_docx_through_the_extractor(tmp_path):
    _docx(tmp_path)
    read_file = file_tools(str(tmp_path))[0]

    result = read_file("doc.docx")

    assert "Hello world" in json.dumps(result)
    assert "\ufffd" not in json.dumps(result)


def test_is_office_names_only_extractable_suffixes(tmp_path):
    assert is_office(tmp_path / "a.docx")
    assert is_office(tmp_path / "a.xlsx")
    assert is_office(tmp_path / "a.pptx")
    assert is_office(tmp_path / "a.ipynb")
    assert not is_office(tmp_path / "a.pdf")
    assert not is_office(tmp_path / "a.txt")
