"""Audio and video attachments, sent where a model can take them and said where it cannot.

Two things must both be true before the bytes go: the MODEL takes that kind (`capabilities(...).audio`
or `.video`, read from the same list the model picker shows as "Inputs", never from the model's name)
and the ROUTE has a way to carry it (`ProviderClient.media_carry`: Gemini's inline data and the
chat-completions wire's `input_audio` / `video_url` do; Anthropic, the Responses wire and Bedrock
have none). What the picker lists is sent; nothing narrower. Otherwise the honest thing is a sentence that
says what was attached, where the file is saved and why it was not sent — never silence, which reads
as "the user attached nothing", and never the bytes, which the provider would answer with a 400.
There is no local speech-to-text.

Media rides the history as the same `file` part a PDF does (`file_data` is a data URL; the
mime type says which), with the saved path beside it. Like the PDF and image adaptations it is
decided per outbound call and never written back, so switching to a Gemini model mid-session
sends the real recording.

Only the newest recordings stay inline: a 10 MB clip resent on every later turn costs the
same each time and the model has already answered about it. Older ones become a line naming the
saved file, which the terminal (ffmpeg, a transcriber) can still open.
"""

from __future__ import annotations

import re
from typing import Any, Optional

#: How many recordings, newest first, ride inline. The rest are named, not resent.
KEEP_INLINE = 2

_MEDIA_URL = re.compile(r"^data:((?:audio|video)/[a-z0-9.+-]+);base64,", re.IGNORECASE)


def media_mime(part: Any) -> Optional[str]:
    """The audio/video MIME type of a `file` part, or None for a PDF or anything else."""
    if not (isinstance(part, dict) and part.get("type") == "file"):
        return None
    match = _MEDIA_URL.match(str((part.get("file") or {}).get("file_data") or ""))
    return match.group(1).lower() if match else None


def kind_of(mime: str) -> str:
    return "audio" if mime.startswith("audio/") else "video"


def data_url(mime: str, raw_b64: str) -> str:
    return f"data:{mime};base64,{raw_b64}"


def _note(part: dict[str, Any], mime: str, *, why_not: str) -> dict[str, Any]:
    name = str((part.get("file") or {}).get("filename") or "recording")
    saved = part.get("saved_at")
    where = f" It is saved at {saved} — open it with the terminal." if saved else ""
    return {"type": "text", "text": f"[Attached {kind_of(mime)}: {name} ({mime}) — {why_not}{where}]"}


def _why_not(kind: str, mime: str, model_takes: bool) -> str:
    if not model_takes:
        return f"this model does not take {kind}, and nothing here transcribes it."
    return (
        f"this model takes {kind}, but the connection it is reached over has no way to carry it "
        "(the API it uses has no audio or video input), so it was not sent."
    )


def has_media(messages: list[dict[str, Any]]) -> bool:
    """Whether any message carries an audio or video part. Checked before the model's
    capabilities are asked for, so a thread without media never touches the provider."""
    return any(
        media_mime(p) for m in messages if isinstance(m.get("content"), list) for p in m["content"]
    )


def adapt_messages(
    messages: list[dict[str, Any]], caps: Any, carry: Any = None
) -> list[dict[str, Any]]:
    """The outbound messages with every audio/video part either kept or replaced by a note.

    `carry(mime)` is the route's answer (`ProviderClient.media_carry`); without one nothing is sent.
    """
    if not has_media(messages):
        return messages
    kept = 0
    out: list[Optional[dict[str, Any]]] = [None] * len(messages)
    for index in range(len(messages) - 1, -1, -1):  # newest first: those are the ones that stay
        message = messages[index]
        content = message.get("content")
        if not isinstance(content, list):
            out[index] = message
            continue
        parts: list[Any] = []
        for part in reversed(content):
            mime = media_mime(part)
            if mime is None:
                parts.append(part)
                continue
            kind = kind_of(mime)
            takes = bool(getattr(caps, kind, False))
            if takes and carry is not None and carry(mime):
                if kept < KEEP_INLINE:
                    kept += 1
                    parts.append(part)
                else:
                    parts.append(_note(part, mime, why_not="it came earlier in the conversation and is not sent again."))
            else:
                parts.append(_note(part, mime, why_not=_why_not(kind, mime, takes)))
        out[index] = {**message, "content": list(reversed(parts))}
    return [m for m in out if m is not None]
