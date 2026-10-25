"""Turning a user message and its attachments into the content a provider is sent.

Messages go to the provider SDKs in the OpenAI shape, where `content` is a string or a list of
parts: `{"type": "text", ...}`, `{"type": "image_url", "image_url": {"url": ...}}` (data: URLs work,
and vision models read them) and `{"type": "file", "file": {"filename", "file_data"}}` for PDFs. So an
image or PDF attachment is just a part added to the user turn, and the Anthropic and Gemini providers
convert the parts to their own block shapes.

`build_user_content` returns a plain string when there are no attachments, which is the text-only
path, and the list of parts otherwise.
"""

from __future__ import annotations

import base64
from typing import Any, Callable, Optional

MAX_ATTACHMENTS = 8  # with one message
# Ceilings on a data URL's length, which is about 4/3 of the file it carries.
MAX_IMAGE_CHARS = 12_000_000  # an 8-9 MB image: past that one picture is most of a request
MAX_PDF_CHARS = 15_000_000  # a 10 MB PDF, the most the app lets a person pick
MAX_FILE_CHARS = 20_000_000  # about 15 MB, for any other kind of file
MAX_TEXT_CHARS = 200_000  # the longest text attachment taken, in characters

#: A text attachment at or below this size rides inline whole: a note, a small CSV or a config
#: fragment saves the model a tool call. Above it the body is spilled and the model gets a head plus
#: the path, because a 200k-character file inlined at every request costs ~50k tokens until
#: compaction, which is more than the work it informs. Inlining a head lets the agent answer about a
#: short document without a tool round.
ATTACHMENT_INLINE_CHARS = 12_000
#: How much of a spilled attachment stays model-visible inline (cut at a line boundary).
ATTACHMENT_HEAD_CHARS = 4_000

# How a text part starts when it carries an attached file. Whatever reads attachments back out of
# a message looks for exactly this, so it is spelled once, here.
ATTACHED_TEXT_PREFIX = "[Attached file: "


def _text_part(name: str, body: str, spill: Optional[Callable[[str], Optional[str]]]) -> str:
    """One text attachment as the model reads it: whole when small, a head and a locator when not.

    `spill` is the session's sink (`SpillStore.sink` in tools/spill.py): it writes the complete body
    inside a session root and returns the path. Every failure falls back to inlining, because an
    attachment must never cost the turn that carried it, the contract the spill store already keeps
    for tool output.
    """
    prefix = f"{ATTACHED_TEXT_PREFIX}{name}]"
    if spill is None or len(body) <= ATTACHMENT_INLINE_CHARS:
        return f"{prefix}\n{body[:MAX_TEXT_CHARS]}"
    try:
        saved = spill(body)
    except Exception:
        saved = None
    if not saved:
        return f"{prefix}\n{body[:MAX_TEXT_CHARS]}"
    head = body[:ATTACHMENT_HEAD_CHARS]
    # Cut at a line boundary when one falls far enough in, so the head does not end mid-token and
    # read as a corrupted line.
    cut = head.rfind("\n")
    if cut > ATTACHMENT_HEAD_CHARS // 2:
        head = head[:cut]
    dropped = len(body) - len(head)
    lines = body.count("\n") + 1
    return (
        f"{prefix}\n{head}\n\n"
        f"…[{dropped:,} more characters of this {lines:,}-line file are not shown — "
        f"the complete text is at {saved} — read it with read_file(path={saved!r})]"
    )


def _is_data_url(url: Any, prefix: str, *, base64_anywhere: bool = False) -> bool:
    return isinstance(url, str) and url.startswith(prefix) and (not base64_anywhere or ";base64," in url)


def _is_data_image(url: Any) -> bool:
    return _is_data_url(url, "data:image/", base64_anywhere=True)


def _is_data_pdf(url: Any) -> bool:
    return _is_data_url(url, "data:application/pdf;base64,")


def _is_data_file(url: Any) -> bool:
    return _is_data_url(url, "data:", base64_anywhere=True)


def _decode_data_url(url: str) -> Optional[bytes]:
    try:
        return base64.b64decode(url.partition(",")[2])
    except Exception:
        return None


def _try_text(raw: bytes) -> Optional[str]:
    """The bytes as text when they decode as UTF-8, else None (binary)."""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _file_reference(name: str, mime: str, size: int, path: Optional[str]) -> str:
    """The model-visible handle for a file that is not sent natively.

    It must never be silence, since a dropped attachment reads as "the user attached nothing": the
    model is told where the file was saved, or that it could not be.
    """
    if path:
        return (
            f"[Attached file: {name}] ({mime}, {size:,} bytes) — saved at {path} — "
            f"read it with read_file(path={path!r}) or the terminal."
        )
    return (
        f"[Attached file: {name}] ({mime}, {size:,} bytes) — it could not be saved for "
        "reading; tell the user it was not stored and ask how to proceed."
    )


def _text_block(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


# Each builder turns one attachment into the parts it contributes. An empty list means it was
# skipped (invalid, oversized or empty) and the turn carries on without it.


def _image_parts(item: dict, spill: Optional[Callable]) -> list[dict[str, Any]]:
    url = item.get("data_url") or ""
    if url.startswith("data:image/svg+xml") and len(url) <= MAX_FILE_CHARS:
        # SVG is XML the model can read, and no provider accepts it as a picture.
        raw = _decode_data_url(url)
        body = _try_text(raw) if raw is not None else None
        return [_text_block(_text_part(str(item.get("name") or "image.svg"), body, spill))] if body else []
    if _is_data_image(url) and len(url) <= MAX_IMAGE_CHARS:
        return [{"type": "image_url", "image_url": {"url": url}}]
    return []


def _pdf_parts(item: dict) -> list[dict[str, Any]]:
    url = item.get("data_url") or ""
    if _is_data_pdf(url) and len(url) <= MAX_PDF_CHARS:
        return [{"type": "file", "file": {"filename": str(item.get("name") or "attachment.pdf"), "file_data": url}}]
    return []


def _text_parts(item: dict, spill: Optional[Callable]) -> list[dict[str, Any]]:
    body = str(item.get("text") or "")[:MAX_TEXT_CHARS]
    if not body:
        return []
    return [_text_block(_text_part(str(item.get("name") or "attachment"), body, spill))]


def _other_parts(item: dict, spill: Optional[Callable], save_file: Optional[Callable]) -> list[dict[str, Any]]:
    """Any other format: text-like bytes take the text path (head and spill), and a binary is saved
    where the model can read it and named in a handle line, never dropped."""
    url = item.get("data_url") or ""
    if not (_is_data_file(url) and len(url) <= MAX_FILE_CHARS):
        return []
    raw = _decode_data_url(url)
    if raw is None:
        return []
    from .attachment_files import file_parts

    return list(
        file_parts(
            str(item.get("name") or "attachment"),
            str(item.get("mime") or "application/octet-stream").lower(),
            raw,
            text_part=lambda n, body: _text_part(n, body, spill),
            reference=_file_reference,
            save_file=save_file,
        )
    )


def build_user_content(
    text: Optional[str],
    attachments: Optional[list[dict]] = None,
    *,
    spill: Optional[Callable[[str], Optional[str]]] = None,
    save_file: Optional[Callable[[str, bytes], Optional[str]]] = None,
    model: str = "",
    provider: Any = None,
) -> Any:
    """The text alone (a `str`) when nothing is attached, else a list of OpenAI content parts.

    An attachment is `{"kind": "image" | "pdf" | "file" | "text", "name"?, "data_url"? (image, pdf,
    file), "text"? (text)}`. One that is malformed or too large is left out; the turn goes on.

    `spill` keeps a long text attachment on disk and gives its path (`_text_part`); with no spill
    the text goes inline, up to `MAX_TEXT_CHARS`.

    `model` is where the images are going: each image part is made to fit that provider's limits
    (`harness/llm/images.py`). The server always passes the conversation's model; `provider`, the
    router, lets the adapter state its own limits instead of the guesses by name and prefix.
    """
    text = (text or "").strip()
    if not attachments:
        return text

    parts: list[dict[str, Any]] = [_text_block(text)] if text else []
    if len(attachments) > MAX_ATTACHMENTS:
        # Said, not dropped: the model and the person both see that the rest did not go.
        parts.append(
            _text_block(
                f"[{len(attachments) - MAX_ATTACHMENTS} more attachment(s) were not included — "
                f"at most {MAX_ATTACHMENTS} go with one message; send the rest in another]"
            )
        )
    builders = {
        "image": lambda item: _image_parts(item, spill),
        "pdf": _pdf_parts,
        "file": lambda item: _other_parts(item, spill, save_file),
        "text": lambda item: _text_parts(item, spill),
    }
    added = 0  # attachments that contributed at least one part
    for item in attachments[:MAX_ATTACHMENTS]:
        build = builders.get(item.get("kind")) if isinstance(item, dict) else None
        contributed = build(item) if build else []
        if contributed:
            parts.extend(contributed)
            added += 1

    if not added:
        return text  # nothing attached could be used: the text alone, which may be ""
    if model and any(isinstance(part, dict) and part.get("type") == "image_url" for part in parts):
        # Fitted to where it is going: an image that provider would refuse is made smaller when
        # this build can, or becomes a line of text, rather than a 400 in the middle of the turn.
        from .images import fit_attach_parts

        parts = fit_attach_parts(parts, model=model, provider=provider)[0]
    return parts


def content_to_text(content: Any, *, image_placeholder: str = "[image]") -> str:
    """Message content, a string or parts, as one line of text: for titles, previews and search.
    An image is written as `image_placeholder`; "" leaves images out (a title wants that)."""
    if not isinstance(content, (str, list)):
        return ""
    if isinstance(content, str):
        return content
    pieces = []
    for part in content:
        if not isinstance(part, dict):
            continue
        kind = part.get("type")
        if kind == "text":
            pieces.append(str(part.get("text", "")))
        elif kind == "image_url" and image_placeholder:
            pieces.append(image_placeholder)
        elif kind == "file" and image_placeholder:
            from .media_support import kind_of, media_mime

            mime = media_mime(part)
            pieces.append(f"[{kind_of(mime)}]" if mime else "[pdf]")
    return " ".join(pieces).strip()
