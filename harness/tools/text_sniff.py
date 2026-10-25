"""How to open a file as text, or why not to (2026-09-25 audit, W10).

`read_file` opened everything as UTF-8 with replacement, so a binary file poured ~2.5k
characters of noise into the context and a UTF-16 file — what `cmd > out.txt` writes on
Windows PowerShell 5.1 — read as a NUL between every letter. This decides from the head
of the file: a BOM names its encoding, NULs without a UTF-16 shape mean binary, and a head
that is not UTF-8 is read in the encoding it looks like (a Vietnamese or Central-European
Excel "CSV" is cp1258 or cp1250, not cp1252) and cp1252 only when nothing better is known.

`decode_bytes` is the same decision for bytes already in memory — an attachment — so the GUI
never has to guess an encoding (it read everything as UTF-8 and turned `Báo cáo` into
replacement characters) and a chat attachment and `read_file` agree on what a file says.
"""

from __future__ import annotations

import codecs
from pathlib import Path
from typing import Optional

_HEAD = 64 * 1024
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff"}
_BOMS = (
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
)


_DISTINCT = {
    "cp1258", "cp1250", "cp1251", "cp1253", "cp1255", "cp1256", "cp874", "koi8_r", "koi8_u",
    "gb18030", "gbk", "gb2312", "big5", "big5hkscs", "cp950", "shift_jis", "cp932", "euc_jp",
    "iso2022_jp", "euc_kr", "cp949", "iso2022_kr",
}


def _legacy_encoding(head: bytes) -> str:
    """The single-byte or CJK encoding a non-UTF-8 head most likely is.

    `charset_normalizer` (already a dependency of the HTTP stack) does the guessing; when it
    is missing or has no confident answer the Windows Western default stands. Only encodings
    Python can decode are returned, so the caller never meets an unknown codec.
    """
    try:
        from charset_normalizer import from_bytes

        best = from_bytes(head).best()
        name = ((best.encoding if best else "") or "").lower().replace("-", "_")
        # Only a guess the bytes can tell apart from Western text is trusted. cp1252, cp1257
        # (Baltic) and the ISO-8859 family decode the same bytes to different accented
        # letters, and telling them apart from a few accents is a coin toss: French text
        # came back as Baltic. Scripts that differ (Cyrillic, Greek, Hebrew, Arabic, Thai,
        # CJK) and the two that have a real signature here (Vietnamese, Central European)
        # are distinct enough to name.
        if name in _DISTINCT:
            return codecs.lookup(name).name
    except Exception:
        pass
    return "cp1252"


def _utf16_without_bom(head: bytes) -> Optional[str]:
    """`utf-16-le` / `utf-16-be` for text written without a BOM, else None.

    ASCII-heavy UTF-16 has a NUL in every other byte: the odd bytes for little-endian, the
    even ones for big-endian. The other half must be free of NULs, or it is just binary.
    """
    even, odd = head[0::2], head[1::2]
    if not even or not odd:
        return None
    if odd.count(0) / len(odd) > 0.4 and even.count(0) / len(even) < 0.1:
        return "utf-16-le"
    if even.count(0) / len(even) > 0.4 and odd.count(0) / len(odd) < 0.1:
        return "utf-16-be"
    return None


def decode_bytes(raw: bytes) -> Optional[tuple[str, str]]:
    """`(text, encoding)` for bytes that are text, or None for a binary.

    The same rules as `sniff_text`, on a buffer: BOM, UTF-16 without one, UTF-8, then the
    encoding the bytes look like. Never raises and never returns replacement characters for
    a file whose encoding could be named: a wrong guess is a different problem from a lossy one.
    """
    head = raw[:_HEAD]
    for bom, encoding in _BOMS:
        if head.startswith(bom):
            return raw.decode(encoding, errors="replace"), encoding
    if b"\x00" in head:
        guess = _utf16_without_bom(head)
        if guess is None:
            return None
        return raw.decode(guess, errors="replace"), guess
    try:
        return raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        pass
    encoding = _legacy_encoding(head)
    text = raw.decode(encoding, errors="replace")
    if encoding == "cp1258":
        # Vietnamese Windows stores most tone marks as a separate combining character; the
        # same letters composed are what every other file, search and model expects.
        import unicodedata

        text = unicodedata.normalize("NFC", text)
    return text, encoding


def sniff_text(target: Path) -> tuple[Optional[str], Optional[str]]:
    """`(encoding, None)` to read `target` as text, or `(None, why)` when it is not text."""
    with open(target, "rb") as fh:
        head = fh.read(_HEAD)
    for bom, encoding in _BOMS:
        if head.startswith(bom):
            return encoding, None
    if b"\x00" in head:
        guess = _utf16_without_bom(head)
        if guess:
            return guess, None  # UTF-16 text written without a BOM
        size = target.stat().st_size
        hint = (
            " — an image read_file cannot show (png, jpg, gif and webp it can)"
            if target.suffix.lower() in _IMAGE_SUFFIXES
            else ""
        )
        return None, f"binary file ({size:,} bytes), not shown as text{hint}"
    try:
        codecs.getincrementaldecoder("utf-8")().decode(head, final=len(head) < _HEAD)
    except UnicodeDecodeError:
        return _legacy_encoding(head), None
    return "utf-8", None
