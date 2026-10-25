"""Local PDF handling for models that cannot take a PDF themselves.

The canonical history always stores a PDF attachment as an OpenAI `file` content part
(`attachments.py`). At send time the engine checks the active model's capabilities
(`ModelCapabilities.pdf`). When the model cannot take PDFs natively, the file part is replaced just
before the provider call. The stored history is never changed, so switching to a PDF-capable model
mid-session sends the real document again.

There are two fallback modes, a user setting under Settings, Token savings:

- `text` extracts the embedded text locally with pypdf, which is pure Python.
- `images` renders each page to a PNG with pypdfium2 and sends image parts. It only helps when the
  model has vision, and otherwise it degrades to text anyway.

Everything runs locally, and the document never goes to a vendor's file-extraction endpoint.
"""

from __future__ import annotations

from .adapt import FALLBACK_MODES, adapt_content, fallback_mode, set_fallback_mode
from .raster import RASTER_MAX_PAGES, RASTER_SCALE, rasterize
from .source import MAX_EXTRACT_CHARS, extract_text, inspect

__all__ = [
    "FALLBACK_MODES",
    "MAX_EXTRACT_CHARS",
    "RASTER_MAX_PAGES",
    "RASTER_SCALE",
    "adapt_content",
    "extract_text",
    "fallback_mode",
    "inspect",
    "rasterize",
    "set_fallback_mode",
]
