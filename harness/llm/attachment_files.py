"""What a chat attachment that is not an image or a PDF becomes for the model.

The GUI sends anything it does not recognise as `kind: "file"`, base64 and unnamed by type. The
model must get the file's CONTENT where it can be read, and never silence where it cannot:

  - an image the browser did not label (HEIC, a `.jfif`, a file with no MIME type) is an image;
  - SVG is text — XML the model can read — not a picture (no provider accepts it as one);
  - a document (`.docx`, `.xlsx`, `.pptx`, `.ipynb`) is read locally: its text is inlined the way a
    small text file is, head first, so the question can be answered without a tool round;
  - an archive is listed, so the model knows what is inside before it asks for anything;
  - text is decoded in the encoding it is really in (a Vietnamese Excel "CSV" is not UTF-8);
  - anything else is saved and named in one handle line (`_file_reference`).

Every branch that saves a file names the path, so the complete original is one `read_file` away.
"""

from __future__ import annotations

import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any, Callable, Optional

from .images import pillow_available
from harness.tools.office import OFFICE_SUFFIXES, office_text_from_bytes
from harness.tools.text_sniff import decode_bytes

#: Entries an archive listing shows before saying how many more there are.
MAX_LISTED = 200


def sniff_image_mime(raw: bytes) -> Optional[str]:
    """The MIME type of image bytes by their content, or None. Needs Pillow; a build without
    it treats an unlabelled image as the binary it cannot show."""
    if not pillow_available():
        return None
    try:
        from PIL import Image

        with Image.open(BytesIO(raw)) as img:
            fmt = (img.format or "").upper()
    except Exception:
        return None
    return {
        "JPEG": "image/jpeg", "PNG": "image/png", "GIF": "image/gif", "WEBP": "image/webp",
        "BMP": "image/bmp", "TIFF": "image/tiff", "ICO": "image/x-icon", "MPO": "image/jpeg",
    }.get(fmt)


def is_svg(name: str, mime: str, raw: bytes) -> bool:
    return (
        mime == "image/svg+xml"
        or name.lower().endswith(".svg")
        or raw.lstrip()[:512].lower().startswith((b"<svg", b"<?xml"))
        and b"<svg" in raw[:2048].lower()
    )


def archive_listing(name: str, raw: bytes, path: Optional[str]) -> Optional[str]:
    """`name` as a table of contents, or None when it is not a readable zip."""
    try:
        with zipfile.ZipFile(BytesIO(raw)) as zf:
            infos = [i for i in zf.infolist() if not i.is_dir()]
    except (zipfile.BadZipFile, OSError):
        return None
    shown = "\n".join(f"  {i.filename} ({i.file_size:,} bytes)" for i in infos[:MAX_LISTED])
    more = f"\n  … and {len(infos) - MAX_LISTED:,} more" if len(infos) > MAX_LISTED else ""
    where = f" It is saved at {path}; extract what you need with the terminal." if path else ""
    return f"[Attached archive: {name}] — {len(infos):,} files:\n{shown}{more}\n{where}".rstrip()


def file_parts(
    name: str,
    mime: str,
    raw: bytes,
    *,
    text_part: Callable[[str, str], str],
    reference: Callable[[str, str, int, Optional[str]], str],
    save_file: Optional[Callable[[str, bytes], Optional[str]]],
) -> list[dict[str, Any]]:
    """The content parts for one `kind: "file"` attachment. Never empty, never raises."""
    image_mime: Optional[str] = None
    if mime.startswith("image/") and mime != "image/svg+xml":
        image_mime = sniff_image_mime(raw) or mime
    elif not mime.startswith("text/"):
        image_mime = sniff_image_mime(raw)  # an image the browser gave no type to
    if image_mime:
        import base64

        url = f"data:{image_mime};base64," + base64.b64encode(raw).decode("ascii")
        return [{"type": "image_url", "image_url": {"url": url}}]

    if is_svg(name, mime, raw):
        decoded = decode_bytes(raw)
        if decoded is not None:
            return [{"type": "text", "text": text_part(name, decoded[0])}]

    suffix = Path(name).suffix.lower()
    kept: list[Optional[str]] = []

    def saved_path() -> Optional[str]:
        """Save the original once, when a branch needs its path (plain text never does)."""
        if not kept:
            kept.append(save_file(name, raw) if save_file is not None else None)
        return kept[0]

    if mime.startswith(("audio/", "video/")):
        import base64

        # Kept whole for a model that takes it; the saved path lets one that does not (and any
        # later turn, once the recording stops being resent) still name the file.
        url = f"data:{mime};base64," + base64.b64encode(raw).decode("ascii")
        return [{"type": "file", "file": {"filename": name, "file_data": url}, "saved_at": saved_path()}]

    if suffix in OFFICE_SUFFIXES:
        lines, problem = office_text_from_bytes(name, raw)
        if lines is not None:
            body = text_part(name, "\n".join(lines))
            saved = saved_path()
            note = f"\n[The original {suffix} file is saved at {saved}.]" if saved else ""
            return [{"type": "text", "text": body + note}]
        return [{"type": "text", "text": f"{reference(name, mime, len(raw), saved_path())} ({problem})"}]

    if suffix == ".zip" or mime in ("application/zip", "application/x-zip-compressed"):
        listing = archive_listing(name, raw, saved_path())
        if listing is not None:
            return [{"type": "text", "text": listing}]

    decoded = decode_bytes(raw)
    if decoded is not None:
        text, encoding = decoded
        body = text_part(name, text if encoding.startswith(("utf-8", "utf-16")) else f"(decoded as {encoding})\n{text}")
        return [{"type": "text", "text": body}]

    return [{"type": "text", "text": reference(name, mime, len(raw), saved_path())}]
