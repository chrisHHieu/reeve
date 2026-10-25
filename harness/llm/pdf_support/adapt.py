"""Swapping a PDF part for something a model without PDF support can read."""

from __future__ import annotations

from typing import Any

from .raster import rasterize
from .source import extract_text

FALLBACK_MODES = ("text", "images")

# The person's preference. The server manager sets it from prefs at startup and when settings change,
# and CLI or library use keeps the "text" default.
_fallback_mode = "text"


def set_fallback_mode(mode: Any) -> str:
    global _fallback_mode
    _fallback_mode = mode if mode in FALLBACK_MODES else "text"
    return _fallback_mode


def fallback_mode() -> str:
    return _fallback_mode


def _is_pdf_part(part: Any) -> bool:
    return (
        isinstance(part, dict)
        and part.get("type") == "file"
        and str((part.get("file") or {}).get("file_data") or "").startswith("data:application/pdf")
    )


def adapt_content(content: list[dict[str, Any]], caps: Any) -> list[dict[str, Any]]:
    """Replace the `file` parts of a message for a model without native PDF support.

    A model with vision, in `images` mode, gets page images. Otherwise it gets the extracted text.
    Both ways end in a visible note when nothing usable comes out, because a PDF must never vanish
    from the turn without a word."""
    adapted: list[dict[str, Any]] = []
    for part in content:
        # Audio and video ride the same part type and belong to `media_support`.
        if not _is_pdf_part(part):
            adapted.append(part)
            continue
        file = part.get("file") or {}
        name = str(file.get("filename") or "attachment.pdf")
        file_data = file.get("file_data") or ""

        if fallback_mode() == "images" and getattr(caps, "vision", False):
            pages = rasterize(file_data)
            if pages:
                adapted.append(
                    {
                        "type": "text",
                        "text": f"[Attached PDF: {name} — {len(pages)} page image(s), rendered locally]",
                    }
                )
                adapted.extend({"type": "image_url", "image_url": {"url": url}} for url in pages)
                continue

        text = extract_text(file_data)
        if text:
            note = (
                f"[Attached PDF: {name} — text extracted locally; "
                f"this model has no native PDF support]\n{text}"
            )
        else:
            note = (
                f"[Attached PDF: {name} — no extractable text (likely scanned). "
                "A model with native PDF support (Claude, GPT, Gemini) can read it.]"
            )
        adapted.append({"type": "text", "text": note})
    return adapted
