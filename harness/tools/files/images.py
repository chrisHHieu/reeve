"""Looking at a picture on disk: what `read_file` does when the file is an image.

It was a tool of its own, `read_image`, and the model had to pick the right reader from the file's
extension; a picture is now one more kind of file `read_file` knows, told apart by its
first bytes as well as its name."""

from __future__ import annotations

import base64
import posixpath
from pathlib import Path
from typing import Any, Optional


# What a model can actually be shown. Everything else is bytes it would have to guess at.
IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}

# Base64 inflates by a third, and the encoded string is charged as tokens on every turn it stays in
# context. A screenshot is well under this and a print-resolution scan is not, and refusing loudly
# beats quietly spending someone's context window on it.
MAX_IMAGE_BYTES = 4 * 1024 * 1024


def _too_big(size: int) -> dict[str, Any]:
    return {
        "error": f"that image is {size // 1024} KB, over the {MAX_IMAGE_BYTES // 1024} KB "
        "limit for looking at one — resize or crop it first, or read what "
        "produced it."
    }


def _shown(label: str, size: int, media: str, data: bytes) -> dict[str, Any]:
    url = f"data:{media};base64," + base64.b64encode(data).decode("ascii")
    return {
        "path": label,
        "bytes": size,
        # Lifted off by the engine and attached where the model will see it. The string half is what
        # the model reads as the tool's answer.
        "_attach": [{"type": "image_url", "image_url": {"url": url}}],
        "shown": f"{label} is attached below — look at it before answering.",
    }


#: The first bytes of each image kind, for a picture whose name does not say what it is (a
#: screenshot saved without an extension, a `.bin` a tool wrote).
_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


def image_media(target: Path) -> Optional[str]:
    """The media type of an image the model can be shown, or None: by name, then by first bytes."""
    media = IMAGE_TYPES.get(target.suffix.lower())
    if media is not None:
        return media
    try:
        with open(target, "rb") as handle:
            head = handle.read(12)
    except OSError:
        return None
    for magic, kind in _MAGIC:
        if head.startswith(magic):
            return kind
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return None


def look_at(target: Path, label: str, media: str) -> dict[str, Any]:
    """An image on this machine as `read_file` returns it: a sentence and the attached picture."""
    size = target.stat().st_size
    if size > MAX_IMAGE_BYTES:
        return _too_big(size)
    try:
        data = target.read_bytes()
    except OSError as exc:
        return {"error": f"read failed: {exc}"}
    return _shown(label, size, media, data)


def look_at_remote(backend: Any, path: str) -> Optional[dict[str, Any]]:
    """The same over a remote world, or None when the name is not an image's (the remote side is
    told apart by name only: sniffing would cost a round trip on every read)."""
    suffix = posixpath.splitext(str(path))[1].lower()
    media = IMAGE_TYPES.get(suffix)
    if media is None:
        return None
    try:
        target = backend.locate(path)
        if backend.stat_kind(target) != "f":
            return {"error": f"not a file: {path}"}
        data = backend.read_bytes(target)
    except PermissionError as exc:
        return {"error": str(exc)}
    except (ValueError, OSError) as exc:
        return {"error": f"read failed: {exc}"}
    if len(data) > MAX_IMAGE_BYTES:
        return _too_big(len(data))
    return _shown(backend.display(path), len(data), media, data)
