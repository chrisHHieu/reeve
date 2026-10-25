"""Rendering a PDF's pages to PNG images."""

from __future__ import annotations

import base64
import logging
import struct
import zlib
from typing import Optional

from .source import cached, digest, pdf_bytes

logger = logging.getLogger(__name__)

RASTER_SCALE = 2.0  # about 144 dpi: readable text without giant payloads
RASTER_MAX_PAGES = 100  # a hard ceiling. The person's page threshold gates at attach time.

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def encode_png(width: int, height: int, pixels: bytes, stride: int, channels: int) -> bytes:
    """A minimal 8-bit RGB or RGBA PNG writer, so Pillow need not ship for this alone. The packaged
    sidecar leaves PIL out on purpose, for bundle size and signing surface."""
    color_type = 6 if channels == 4 else 2
    row_bytes = width * channels
    rows = bytearray()
    for y in range(height):
        rows.append(0)  # filter type: none
        rows.extend(pixels[y * stride : y * stride + row_bytes])

    def chunk(tag: bytes, payload: bytes) -> bytes:
        checksum = zlib.crc32(tag + payload) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + tag + payload + struct.pack(">I", checksum)

    header = struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0)
    return (
        _PNG_SIGNATURE
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(bytes(rows), 6))
        + chunk(b"IEND", b"")
    )


def rasterize(file_data: str, max_pages: int = RASTER_MAX_PAGES) -> Optional[list[str]]:
    """Each page as a PNG data URL, or None when rendering is not possible (pypdfium2 is missing or
    the document is broken). Callers then fall back to text."""

    def compute() -> Optional[list[str]]:
        raw = pdf_bytes(file_data)
        if raw is None:
            return None
        try:
            import pypdfium2

            document = pypdfium2.PdfDocument(raw)
            urls: list[str] = []
            try:
                for index in range(min(len(document), max_pages)):
                    # rev_byteorder turns pdfium's native BGR(A) into the RGB(A) a PNG wants.
                    bitmap = document[index].render(scale=RASTER_SCALE, rev_byteorder=True)
                    png = encode_png(
                        bitmap.width, bitmap.height, bytes(bitmap.buffer), bitmap.stride, bitmap.n_channels
                    )
                    urls.append("data:image/png;base64," + base64.b64encode(png).decode("ascii"))
            finally:
                document.close()
            return urls or None
        except Exception:
            logger.warning("pdf rasterization failed", exc_info=True)
            return None

    return cached((digest(file_data), f"images:{max_pages}"), compute)
