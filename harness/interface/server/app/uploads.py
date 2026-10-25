"""Checking the attachments of one `user_message` frame before they become model content."""

from __future__ import annotations

from typing import Any, Callable, Optional

from harness.llm.attachments import MAX_FILE_CHARS, MAX_IMAGE_CHARS, MAX_PDF_CHARS, MAX_TEXT_CHARS

from .limits import MAX_ATTACHMENT_BYTES, MAX_ATTACHMENTS, json_size

_KINDS = {"image", "pdf", "text", "file"}

# kind -> (what a bad one is called, does its data URL have the right shape, size ceiling).
# A file of any other format is saved where the model can read it, and a binary dropped
# silently would read as "nothing was attached", the rule images already follow.
_DATA_URLS: dict[str, tuple[str, Callable[[str], bool], int]] = {
    "image": (
        "image",
        lambda data: data.startswith("data:image/") and ";base64," in data,
        MAX_IMAGE_CHARS,
    ),
    "pdf": ("PDF", lambda data: data.startswith("data:application/pdf;base64,"), MAX_PDF_CHARS),
    "file": ("file", lambda data: data.startswith("data:") and ";base64," in data, MAX_FILE_CHARS),
}


def _problem_with(attachment: dict[str, Any]) -> Optional[str]:
    kind = attachment.get("kind")
    name = attachment.get("name")
    mime = attachment.get("mime")
    if kind not in _KINDS:
        return "Invalid attachment kind."
    if name is not None and (not isinstance(name, str) or len(name) > 1024):
        return "Invalid attachment name."
    if mime is not None and (not isinstance(mime, str) or len(mime) > 255):
        return "Invalid attachment MIME type."
    if kind == "text":
        body = attachment.get("text")
        if not isinstance(body, str) or len(body) > MAX_TEXT_CHARS:
            return "Invalid or oversized text attachment."
        return None
    label, well_formed, ceiling = _DATA_URLS[kind]
    data = attachment.get("data_url")
    if not isinstance(data, str) or not well_formed(data) or len(data) > ceiling:
        return f"Invalid or oversized {label} attachment."
    return None


def attachment_problem(attachments: Any) -> Optional[str]:
    """Why these attachments are refused, or None. Whatever fails is dropped with a visible
    error and never buffered into a turn."""
    if not isinstance(attachments, list):
        return "Invalid attachments: expected a list."
    if len(attachments) > MAX_ATTACHMENTS:
        return f"Too many attachments ({len(attachments)}; limit {MAX_ATTACHMENTS})."
    if any(not isinstance(item, dict) for item in attachments):
        return "Invalid attachment: expected an object."
    if json_size(attachments) > MAX_ATTACHMENT_BYTES:
        return "Attachments too large (limit 15 MB per message)."
    for attachment in attachments:
        problem = _problem_with(attachment)
        if problem:
            return problem
    return None
