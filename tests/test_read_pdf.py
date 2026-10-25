"""`read_file` on a PDF: text, page markers, and the same window/continuation contract.

Why text extraction rather than a page image: a document part is vendor-specific, while
extracted text works for every provider and is what a person means by "read the PDF".
The window, the continuation note and the read card then apply unchanged — the point of
this test file is that the PDF path is the SAME path, not a parallel one.
"""

from __future__ import annotations

import pytest

from harness.tools.files import file_tools
from harness.tools.files.window import MAX_PDF_CHARS as _MAX_PDF_CHARS

pypdf = pytest.importorskip("pypdf")


def _write_pdf(path, pages: list[str]) -> None:
    """Author a tiny text PDF with pypdf's low-level objects (no extra dependency)."""
    from pypdf import PdfWriter
    from pypdf.generic import (
        DecodedStreamObject,
        DictionaryObject,
        NameObject,
    )

    writer = PdfWriter()
    for text in pages:
        page = writer.add_blank_page(width=612, height=792)
        stream = DecodedStreamObject()
        escaped = text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        stream.set_data(f"BT /F1 18 Tf 72 720 Td ({escaped}) Tj ET".encode("latin-1"))
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        page[NameObject("/Contents")] = writer._add_object(stream)
        page[NameObject("/Resources")] = DictionaryObject(
            {
                NameObject("/Font"): DictionaryObject(
                    {NameObject("/F1"): writer._add_object(font)}
                )
            }
        )
    with open(path, "wb") as fh:
        writer.write(fh)


def _read(tmp_path, name="doc.pdf"):
    tools = {t.__name__: t for t in file_tools(str(tmp_path))}
    return tools["read_file"](path=name)


def test_a_pdf_reads_as_text_with_page_markers(tmp_path):
    _write_pdf(tmp_path / "doc.pdf", ["First page body", "Second page body"])
    out = _read(tmp_path)

    assert "error" not in out
    assert out["path"] == "doc.pdf"
    assert "[page 1]" in out["content"] and "First page body" in out["content"]
    assert "[page 2]" in out["content"] and "Second page body" in out["content"]
    # The line window (and therefore the continuation note) is the ordinary one.
    assert out["start_line"] == 1 and out["end_line"] == out["total_lines"]
    assert out["_card_meta"]["read"]["lines"][0]["text"] == "[page 1]"


def test_a_pdf_windows_and_continues_like_any_text_file(tmp_path):
    """pypdf's own leading whitespace is its business; the CONTRACT is what is pinned here:
    the window stops, the note names the line to continue from, and continuing returns the
    rest."""
    _write_pdf(tmp_path / "doc.pdf", ["one", "two", "three"])
    tools = {t.__name__: t for t in file_tools(str(tmp_path))}

    whole = tools["read_file"](path="doc.pdf")
    assert "[page 3]" in whole["content"] and "three" in whole["content"]

    first = tools["read_file"](path="doc.pdf", max_lines=3)
    assert first["end_line"] < first["total_lines"]
    assert f"start_line={first['end_line'] + 1}" in first["note"]

    rest = tools["read_file"](path="doc.pdf", start_line=first["end_line"] + 1)
    assert "error" not in rest and rest["content"].strip()


def test_a_pdf_with_no_text_layer_says_so(tmp_path):
    """A scan: pypdf extracts nothing. The honest answer names the alternatives instead of
    returning binary noise as text."""
    # An image-only PDF is simplest to author with Pillow (a real dependency).
    Image = pytest.importorskip("PIL.Image")
    Image.new("RGB", (64, 64), (255, 255, 255)).save(tmp_path / "scan.pdf")
    out = _read(tmp_path, "scan.pdf")
    assert "no extractable text" in out["error"]
    assert "read_file those" in out["error"]  # the pages as PNGs, which read_file shows


def test_a_broken_pdf_reports_its_cause(tmp_path):
    (tmp_path / "broken.pdf").write_bytes(b"%PDF-1.4 not really a pdf")
    out = _read(tmp_path, "broken.pdf")
    assert "error" in out and "could not read that PDF" in out["error"]


def test_the_pdf_reader_is_capped(tmp_path, monkeypatch):
    """The extraction step is bounded before the window runs, so one PDF cannot pull a
    book into memory. The stop is STATED in the text."""
    import harness.tools.files.window as files

    monkeypatch.setattr(files, "MAX_PDF_CHARS", 30)
    _write_pdf(tmp_path / "doc.pdf", ["x" * 40, "second page never read"])
    out = _read(tmp_path, "doc.pdf")
    assert "stopped after page 1" in out["content"]
    assert "second page never read" not in out["content"]
    assert _MAX_PDF_CHARS >= 1_000_000  # the shipped bound stays generous
