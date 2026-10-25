"""`read_file` on files that are not UTF-8 text (2026-09-25 audit, W10).

A binary file poured noise into the context, a UTF-16 file (what `cmd > out.txt` writes
on Windows PowerShell 5.1) read as a NUL between every letter, and a start past the end
answered "lines 50-49 of 1".
"""

from __future__ import annotations

from harness.tools.files import file_tools
from harness.tools.files.renders import _read_render


def _read(tmp_path, name, **kwargs):
    tool = next(t for t in file_tools(str(tmp_path)) if t.__name__ == "read_file")
    value = tool(path=name, **kwargs)
    return value, _read_render({"path": name}, value)


def test_a_binary_file_is_named_not_shown(tmp_path):
    (tmp_path / "blob.bin").write_bytes(bytes(range(256)) * 40)
    (tmp_path / "shot.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + b"\x00" * 64)
    _, text = _read(tmp_path, "blob.bin")
    assert text == "Error: binary file (10,240 bytes), not shown as text"
    # A picture is shown, not refused: the sentence, with the image attached.
    value, text = _read(tmp_path, "shot.png")
    assert "attached below" in text and value["_attach"]


def test_utf16_text_reads_as_text_with_or_without_a_bom(tmp_path):
    (tmp_path / "bom.txt").write_bytes("héllo\r\nworld\r\n".encode("utf-16"))
    (tmp_path / "bare.txt").write_bytes("plain ascii\nsecond\n".encode("utf-16-le"))
    value, text = _read(tmp_path, "bom.txt")
    assert "héllo" in text and "\x00" not in text and "read as utf-16" in text
    _, text = _read(tmp_path, "bare.txt")
    assert "plain ascii" in text and "\x00" not in text


def test_a_windows_codepage_file_is_decoded_and_says_how(tmp_path):
    (tmp_path / "legacy.txt").write_bytes("café crème\n".encode("cp1252"))
    _, text = _read(tmp_path, "legacy.txt")
    assert "café crème" in text and "read as cp1252" in text


def test_a_start_past_the_end_says_how_long_the_file_is(tmp_path):
    (tmp_path / "one.txt").write_text("only line\n", encoding="utf-8")
    _, text = _read(tmp_path, "one.txt", start_line=50)
    assert text.startswith("one.txt (start_line 50 is past the end: the file has 1 line)")
