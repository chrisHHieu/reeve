"""Reading a PDF carried as a data URL: its size, and its embedded text.

Results are cached by content hash, because the history is replayed on every turn and extracting or
rendering a 10 MB PDF is the expensive part.
"""

from __future__ import annotations

import base64
import hashlib
import io
import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

MAX_EXTRACT_CHARS = 200_000  # matches attachments.MAX_TEXT_CHARS

_DATA_PREFIX = "data:application/pdf;base64,"

# A small cache keyed by (sha256 of the data URL, operation), evicting the oldest first.
_cache: dict[tuple[str, str], Any] = {}
_CACHE_MAX = 8


def cached(key: tuple[str, str], compute: Callable[[], Any]) -> Any:
    if key in _cache:
        return _cache[key]
    value = compute()
    if len(_cache) >= _CACHE_MAX:
        _cache.pop(next(iter(_cache)))
    _cache[key] = value
    return value


def digest(file_data: str) -> str:
    return hashlib.sha256(file_data.encode("ascii", "ignore")).hexdigest()


def pdf_bytes(file_data: str) -> Optional[bytes]:
    """The document's bytes, or None when this is not a PDF data URL."""
    if not isinstance(file_data, str) or not file_data.startswith(_DATA_PREFIX):
        return None
    try:
        return base64.b64decode(file_data[len(_DATA_PREFIX):], validate=False)
    except Exception:
        return None


def inspect(file_data: str) -> dict[str, Any]:
    """Page count and size of a PDF data URL, for the threshold check at attach time.

    It never raises. Anything unreadable comes back as `{"ok": False, "error": ...}`."""
    raw = pdf_bytes(file_data)
    if raw is None:
        return {"ok": False, "error": "not a PDF data URL"}
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(raw), strict=False)
        if reader.is_encrypted:
            try:
                reader.decrypt("")  # a PDF with only an owner password opens this way
            except Exception:
                return {"ok": False, "error": "PDF is password-protected"}
        return {"ok": True, "pages": len(reader.pages), "bytes": len(raw)}
    except Exception as exc:
        return {"ok": False, "error": f"could not read PDF: {exc.__class__.__name__}"}


def extract_text(file_data: str) -> Optional[str]:
    """The embedded text of the whole document, capped, or None when it cannot be read.

    A scanned PDF legitimately gives "", and callers report that case on its own."""

    def compute() -> Optional[str]:
        raw = pdf_bytes(file_data)
        if raw is None:
            return None
        try:
            from pypdf import PdfReader

            pieces: list[str] = []
            length = 0
            for page in PdfReader(io.BytesIO(raw), strict=False).pages:
                text = page.extract_text() or ""
                if text:
                    pieces.append(text)
                    length += len(text)
                    if length >= MAX_EXTRACT_CHARS:
                        break
            return "\n\n".join(pieces)[:MAX_EXTRACT_CHARS]
        except Exception:
            logger.warning("pdf text extraction failed", exc_info=True)
            return None

    return cached((digest(file_data), "text"), compute)
