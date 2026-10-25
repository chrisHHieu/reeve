"""Cutting a file into the window `read_file` returns, and reading a PDF as text."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

DEFAULT_MAX_LINES = 2000
MAX_LINE_CHARS = 500

# The total characters one read may return, line-number prefixes included. The engine shrinks any
# single tool result over RESULT_CEILING_CHARS (50,000) into a head and tail stub with a spill locator,
# and a read that gets shrunk is a read the model must repeat, because the dropped middle is exactly
# what it asked for. So the tool caps itself under that line and names the next `start_line`, which
# turns "the result was truncated" into "continue from line N". 40k leaves room for the JSON
# envelope. The real limit for a caller that wants less is `max_lines`.
MAX_TOTAL_CHARS = 40_000

# How much text is extracted from one PDF before the read stops and says so. The window's own budget
# is smaller. This bounds the extraction step, which runs before the window and must not be able to
# pull a 500-page book into memory.
MAX_PDF_CHARS = 2_000_000


def clip_line(raw: str) -> str:
    return raw if len(raw) <= MAX_LINE_CHARS else raw[:MAX_LINE_CHARS] + "… (line truncated)"


def window_result(label: str, start: int, total: int, selected: list[str], numbered: list[dict]) -> dict:
    """The result dict for a window that has been cut, card metadata and continuation note included."""
    end = start + len(selected) - 1 if selected else start - 1
    result: dict[str, Any] = {
        "path": label,
        "start_line": start,
        "end_line": end,
        "total_lines": total,
        "content": "\n".join(selected),
    }
    # The structured window, for the row's card. It cannot be recovered downstream without
    # re-parsing a format this code chose, so it travels beside `content` as `_card_meta`. The engine
    # lifts it off before either the model or the presenter sees the value, as it does `_display`
    # (engine._record_result).
    #
    # There is no `lang` on purpose. A language id is a highlighter concept and the surface already
    # has the path, so sending one would mean a second extension-to-language table to keep in step.
    result["_card_meta"] = {
        "read": {"path": label, "offset": start, "total_lines": total, "lines": numbered}
    }
    if end < total:
        result["note"] = (
            f"showing lines {start}-{end} of {total}; call again with start_line={end + 1} to continue"
        )
    return result


def windowed_read_text(whole: str, label: str, start: int, count: int) -> dict[str, Any]:
    """A whole fetched file as the windowed `read_file` result, card included.

    The remote backend uses it. The local reader streams the file line by line and was left alone,
    and this exists so the remote tool answers in the same shape, with one formatting dialect that
    cannot drift."""
    lines = whole.splitlines()
    selected: list[str] = []
    numbered: list[dict[str, Any]] = []
    used = 0
    for number, raw in enumerate(lines[start - 1 :], start=start):
        if len(selected) >= count:
            break
        text = clip_line(raw)
        entry = f"{number:>6}\t{text}"
        if selected and used + len(entry) + 1 > MAX_TOTAL_CHARS:
            # One line always gets through, exactly as the local reader guarantees.
            break
        used += len(entry) + 1
        selected.append(entry)
        numbered.append({"number": number, "text": text})
    return window_result(label, start, len(lines), selected, numbered)


def is_pdf(target: Path) -> bool:
    """A PDF by suffix, with the magic number as the tie-breaker. A `.pdf` that is not one falls
    back to the text reader, which gives its honest failure, and a PDF named otherwise still reads."""
    if target.suffix.lower() == ".pdf":
        return True
    try:
        with open(target, "rb") as handle:
            return handle.read(5) == b"%PDF-"
    except OSError:
        return False


def pdf_text_lines(target: Path) -> "tuple[Optional[list[str]], str]":
    """A PDF's text split into lines with a `[page N]` marker for each page, or an error.

    It extracts text and not page images because that works for every provider (a document part is
    vendor-specific), it is what a person means by "read the PDF", and the windowing and
    continuation machinery then applies unchanged. A PDF with no text layer, a scan, says so and does
    not return noise. Sending the page as an image needs a route that accepts one, which is a
    different feature."""
    try:
        from pypdf import PdfReader
    except ImportError:  # pragma: no cover - the packaged build always ships it
        return None, (
            "this build has no PDF reader (pypdf is missing) — read the file with a "
            "shell tool instead"
        )
    try:
        reader = PdfReader(str(target))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:
                return None, "that PDF is encrypted; unlock it first, then read it"
        pages: list[str] = []
        extracted = 0
        for number, page in enumerate(reader.pages, 1):
            try:
                text = page.extract_text() or ""
            except Exception as exc:  # a broken page must not cost the whole read
                text = f"[page {number}: could not extract text ({exc})]"
                extracted += 1
            extracted += len(text)
            pages.append(f"[page {number}]\n{text.strip()}")
            if extracted >= MAX_PDF_CHARS:
                pages.append(
                    f"[stopped after page {number}: the extracted text passed "
                    f"{MAX_PDF_CHARS} characters]"
                )
                break
    except Exception as exc:  # pypdf raises its own families
        return None, f"could not read that PDF: {exc}"
    if extracted == 0:
        return None, (
            "that PDF has no extractable text (a scan, or images only). Page images are "
            "not sent to the model from a PDF — convert the pages to PNG and read_file "
            "those, or run OCR."
        )
    return "\n\n".join(pages).splitlines(), ""
