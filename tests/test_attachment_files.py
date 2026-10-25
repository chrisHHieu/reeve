"""Chat attachments beyond images and PDFs — what the model reads, and that nothing vanishes.

The audit that produced these (2026-09-30) found four ways an attachment failed quietly:

  - a CSV saved by Excel on a Vietnamese or Central-European Windows is cp1258 / cp1250, and was
    read as UTF-8 by the GUI: `Báo cáo` arrived as replacement characters, inline, with no notice;
  - a HEIC or BMP photo went to the provider unchanged and came back as a 400 in the middle of a turn
    (Anthropic reads exactly JPEG, PNG, GIF and WebP);
  - a session that pasted screenshots past the 21st hit Anthropic's stricter per-image pixel limit;
  - the ninth attachment was cut without a word.

And the formats that had no reader: a Word or Excel file reached the model as a path it had to
open, an archive as a path with no hint of what was inside.
"""

from __future__ import annotations

import base64
import io
import unicodedata
import zipfile

from harness.llm.attachments import MAX_ATTACHMENTS, build_user_content
from harness.tools.text_sniff import decode_bytes


def _url(mime: str, raw: bytes) -> str:
    return f"data:{mime};base64,{base64.b64encode(raw).decode()}"


def _png(width: int, height: int) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (width, height), (10, 120, 200)).save(buf, format="PNG")
    return buf.getvalue()


def _image_as(fmt: str, width: int = 40, height: int = 30) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (width, height), (200, 30, 30)).save(buf, format=fmt)
    return buf.getvalue()


def _cp1258(text: str) -> bytes:
    """What Excel writes for a Vietnamese CSV: precomposed letters where cp1258 has them, a base
    letter plus a combining tone mark where it does not."""
    tones = "̣̀́̃̉"
    out = bytearray()
    for ch in text:
        parts = "".join(sorted(unicodedata.normalize("NFD", ch), key=lambda c: c in tones))
        i = 0
        while i < len(parts):
            for j in range(len(parts), i, -1):
                try:
                    out += unicodedata.normalize("NFC", parts[i:j]).encode("cp1258")
                    i = j
                    break
                except UnicodeEncodeError:
                    continue
    return bytes(out)


VIETNAMESE = (
    "Báo cáo doanh thu quý ba\nKhách hàng: Nguyễn Văn Ánh, Trần Thị Hương, Lê Quốc Đạt\n"
    "Tổng cộng: 1.250.000 đồng\n"
) * 4


def _file(name: str, raw: bytes, mime: str = "") -> dict:
    return {"kind": "file", "name": name, "mime": mime, "data_url": _url(mime or "application/octet-stream", raw)}


def _text_of(parts) -> str:
    return "\n".join(p["text"] for p in parts if p["type"] == "text")


# -------------------------------------------------------------------- encodings


def test_a_vietnamese_excel_csv_is_read_in_its_own_encoding():
    parts = build_user_content("", [_file("doanh_thu.csv", _cp1258(VIETNAMESE), "text/csv")])
    text = _text_of(parts)
    assert "Báo cáo doanh thu" in text and "Nguyễn Văn Ánh" in text
    assert "�" not in text
    assert "decoded as cp1258" in text  # said, so a wrong guess is visible


def test_utf16_with_and_without_a_bom_is_read_as_text():
    sample = "name,city\nAnn,Hà Nội\n" * 20
    for encoding in ("utf-16", "utf-16-le", "utf-16-be"):
        parts = build_user_content("", [_file("out.txt", sample.encode(encoding))])
        assert "Hà Nội" in _text_of(parts), encoding


def test_plain_utf8_is_untouched_and_says_nothing_about_encodings():
    parts = build_user_content("", [_file("a.txt", "xin chào".encode("utf-8"), "text/plain")])
    assert "xin chào" in _text_of(parts) and "decoded as" not in _text_of(parts)


def test_decode_bytes_is_the_read_file_decision_on_a_buffer():
    assert decode_bytes(b"\x89PNG\r\n\x1a\n\x00\x00") is None  # NULs without a UTF-16 shape: binary
    assert decode_bytes("héllo".encode("utf-8")) == ("héllo", "utf-8")
    # French in cp1252 must stay cp1252: the guesser called it Baltic, which reads the same bytes
    # as different accented letters
    french = "Café crème brûlée façade naïve — déjà vu.\n" * 20
    assert decode_bytes(french.encode("cp1252")) == (french, "cp1252")


# ------------------------------------------------------------------------ images


def test_a_bmp_and_a_tiff_are_converted_because_no_provider_reads_them():
    for fmt in ("BMP", "TIFF"):
        parts = build_user_content(
            "look",
            [{"kind": "image", "name": "p", "data_url": _url(f"image/{fmt.lower()}", _image_as(fmt))}],
            model="anthropic:claude-sonnet-4",
        )
        image = next(p for p in parts if p["type"] == "image_url")["image_url"]["url"]
        assert image.startswith("data:image/png;base64,"), fmt
        assert "converted from" in _text_of(parts), fmt


def test_an_unlabelled_image_is_recognised_by_its_bytes():
    parts = build_user_content("", [_file("IMG_0042", _image_as("PNG"))], model="anthropic:claude-sonnet-4")
    assert [p["type"] for p in parts] == ["image_url"]


def test_an_image_that_cannot_be_read_says_so_instead_of_reaching_the_provider():
    parts = build_user_content(
        "look",
        [{"kind": "image", "name": "p", "data_url": _url("image/heic", b"\x00\x00\x00\x18ftypheic....")}],
        model="anthropic:claude-sonnet-4",
    )
    assert not [p for p in parts if p["type"] == "image_url"]
    assert "not sent" in _text_of(parts) and "PNG or JPEG" in _text_of(parts)


def test_svg_is_text_because_no_provider_takes_it_as_a_picture():
    svg = b"<svg xmlns='http://www.w3.org/2000/svg' width='10' height='10'><circle r='4'/></svg>"
    for attachment in (
        {"kind": "image", "name": "logo.svg", "data_url": _url("image/svg+xml", svg)},
        _file("logo.svg", svg, "image/svg+xml"),
    ):
        parts = build_user_content("", [attachment], model="anthropic:claude-sonnet-4")
        assert not [p for p in parts if p["type"] == "image_url"]
        assert "<circle" in _text_of(parts)


def test_a_large_screenshot_is_brought_under_the_many_image_limit_on_claude_only():
    big = {"kind": "image", "name": "s.png", "data_url": _url("image/png", _png(3000, 200))}

    def width(model):
        parts = build_user_content("", [big], model=model)
        url = next(p for p in parts if p["type"] == "image_url")["image_url"]["url"]
        from PIL import Image

        return Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1]))).width

    assert width("anthropic:claude-sonnet-4") == 2000
    assert width("openai:gpt-4o") == 3000  # no pixel ceiling there, so nothing to fit under


# ---------------------------------------------------------------------- documents


def _docx(paragraphs: list[str]) -> bytes:
    ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    xml = f'<?xml version="1.0"?><w:document xmlns:w="{ns}"><w:body>{body}</w:body></w:document>'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("word/document.xml", xml)
    return buf.getvalue()


def test_a_word_document_arrives_as_its_text_with_the_original_kept(tmp_path):
    saved = {}

    def save_file(name, data):
        saved["path"] = str(tmp_path / name)
        return saved["path"]

    parts = build_user_content(
        "summarise",
        [_file("brief.docx", _docx(["Quarterly plan", "Ship the importer in March"]))],
        save_file=save_file,
    )
    text = _text_of(parts)
    assert "Quarterly plan" in text and "Ship the importer in March" in text
    assert saved["path"] in text  # the original is one read_file away


def test_a_document_that_cannot_be_read_still_names_where_it_is(tmp_path):
    parts = build_user_content(
        "",
        [_file("broken.docx", b"PK\x03\x04 not really a zip")],
        save_file=lambda name, data: str(tmp_path / name),
    )
    text = _text_of(parts)
    assert "broken.docx" in text and str(tmp_path / "broken.docx") in text and "could not extract" in text


def test_an_archive_is_listed_so_the_model_knows_what_is_inside(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("src/main.py", "print(1)")
        zf.writestr("README.md", "hello")
    parts = build_user_content(
        "", [_file("project.zip", buf.getvalue(), "application/zip")], save_file=lambda n, d: str(tmp_path / n)
    )
    text = _text_of(parts)
    assert "2 files" in text and "src/main.py" in text and "README.md" in text
    assert str(tmp_path / "project.zip") in text


# ---------------------------------------------------------------------- the limit


def test_attachments_past_the_limit_are_named_not_dropped():
    many = [{"kind": "text", "name": f"n{i}.txt", "text": f"note {i}"} for i in range(MAX_ATTACHMENTS + 3)]
    text = _text_of(build_user_content("", many))
    assert f"note {MAX_ATTACHMENTS - 1}" in text and f"note {MAX_ATTACHMENTS}" not in text
    assert "3 more attachment(s) were not included" in text
