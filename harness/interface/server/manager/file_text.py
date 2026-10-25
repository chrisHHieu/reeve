"""A file's bytes as text for the Files tab, and that text as the same bytes again.

Reading is the decision `read_file` makes for the model (`tools/text_sniff.py`): a BOM, UTF-16
without one, UTF-8, then the encoding the bytes look like. That last step is a guess. Measured
2026-10-11 on Vietnamese Windows-1258 text, it is right from about 100 bytes on and wrong for a
37-byte note (named Central European) and a 15-byte one (named Big5), so the person can name
the encoding themselves, from `OFFERED`.

Writing puts the text back in the encoding it was read as, with the original's BOM and line
endings. A character that encoding has no place for refuses the save: it is never written as
"?".
"""

from __future__ import annotations

import codecs
import unicodedata
from typing import Optional

from harness.tools.text_sniff import decode_bytes

#: What the person can pick by hand: the encodings a text file on this kind of machine is
#: likely to be in. A name outside this list is not a text codec this code will run.
OFFERED = (
    "utf-8",
    "utf-16",
    "cp1258",
    "cp1252",
    "cp1250",
    "cp1251",
    "shift_jis",
    "gb18030",
    "big5",
    "euc_kr",
)
_BOM = codecs.BOM_UTF8
#: The five tone marks Windows-1258 stores as a byte of their own, after the letter.
_VIETNAMESE_TONES = frozenset("̣̀́̃̉")


def known(encoding: object) -> Optional[str]:
    """`encoding` as one of `OFFERED`, or None."""
    name = str(encoding or "").strip().lower().replace("_", "-")
    for offered in OFFERED:
        if name == offered.replace("_", "-"):
            return offered
    return None


def _tidy(text: str, encoding: str) -> str:
    if encoding == "cp1258":
        # A letter and its tone mark, composed: the form every other file and search uses.
        text = unicodedata.normalize("NFC", text)
    # A BOM is the file's, not the text's, and the editor works in "\n".
    return text.removeprefix("﻿").replace("\r\n", "\n").replace("\r", "\n")


def read_text(raw: bytes, encoding: Optional[str] = None) -> Optional[tuple[str, str]]:
    """`(text, encoding)`, or None when the bytes are not text. With `encoding` (one of
    `OFFERED`) the bytes are read as that, whatever they look like."""
    chosen = known(encoding)
    if chosen is not None:
        if chosen == "utf-8" and raw.startswith(_BOM):
            raw = raw[len(_BOM):]
        return _tidy(raw.decode(chosen, errors="replace"), chosen), chosen
    decoded = decode_bytes(raw)
    if decoded is None:
        return None
    text, name = decoded
    return _tidy(text, name), name


def _legacy(text: str, encoding: str) -> bytes:
    """`text` in a single-byte or CJK encoding. Raises ValueError naming the first character
    the encoding has no place for."""
    out = bytearray()
    for char in text:
        try:
            out += char.encode(encoding)
            continue
        except UnicodeEncodeError:
            pass
        try:
            if encoding != "cp1258":
                raise UnicodeError
            # "ệ" is not a byte in Windows-1258: it is "ê", then the dot below.
            parts = unicodedata.normalize("NFD", char)
            letter = unicodedata.normalize("NFC", "".join(p for p in parts if p not in _VIETNAMESE_TONES))
            out += (letter + "".join(p for p in parts if p in _VIETNAMESE_TONES)).encode(encoding)
        except UnicodeError:
            what = "a part that could not be read" if char == "�" else repr(char)
            raise ValueError(
                f"This file is saved as {encoding}, which has no way to write {what}. "
                "Take it out, or save a copy of the file as UTF-8 in your editor."
            ) from None
    return bytes(out)


def write_text(text: str, original: bytes, encoding: Optional[str] = None) -> bytes:
    """The edited text as bytes: in `encoding` when the person named one, else in the encoding
    the original reads as, with the original's BOM and line endings put back. The read
    normalised all three, and a save must not rewrite a whole file's encoding or line endings as
    a side effect. Raises ValueError when the encoding cannot hold the text."""
    crlf = original.count(b"\r\n") > (original.count(b"\n") - original.count(b"\r\n"))
    text = text.replace("\r\n", "\n")
    if crlf:
        text = text.replace("\n", "\r\n")
    name = known(encoding)
    if name is None:
        decoded = decode_bytes(original)
        name = decoded[1] if decoded else "utf-8"
    if name in ("utf-8", "utf-8-sig"):
        data = text.encode("utf-8")
        return _BOM + data if original.startswith(_BOM) else data
    if name == "utf-16":  # named by its BOM: keep that byte order
        big = original.startswith(codecs.BOM_UTF16_BE)
        return (codecs.BOM_UTF16_BE if big else codecs.BOM_UTF16_LE) + text.encode("utf-16-be" if big else "utf-16-le")
    if name.startswith("utf-16"):
        return text.encode(name)
    return _legacy(text, name)
