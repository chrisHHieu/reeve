"""Audio and video on the chat-completions wire.

OpenAI, OpenRouter, vLLM and the servers that follow them take audio as an `input_audio` part
whose `format` is the container's short name, and video as a `video_url` data URL. The model
picker decides which models receive which; this only spells the part.
"""

from __future__ import annotations

from typing import Any

#: MIME subtype (as browsers report it) -> the wire's format name.
_AUDIO_FORMATS = {
    "mpeg": "mp3", "mp3": "mp3", "wav": "wav", "x-wav": "wav", "wave": "wav", "vnd.wave": "wav",
    "mp4": "m4a", "x-m4a": "m4a", "m4a": "m4a", "aac": "aac", "x-aac": "aac", "ogg": "ogg",
    "flac": "flac", "x-flac": "flac", "aiff": "aiff", "x-aiff": "aiff", "webm": "webm", "opus": "opus",
}


def _audio_format(mime: str) -> str:
    subtype = mime.split("/", 1)[-1].lower()
    return _AUDIO_FORMATS.get(subtype, subtype)


def carry_media(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The audio and video `file` parts the loop stores, as this wire's parts. Only parts the
    loop admitted for this model get here; PDFs, which use the same `file` part, are left
    alone."""
    from ...media_support import media_mime

    def convert(part: Any) -> Any:
        mime = media_mime(part)
        if mime is None:
            return part
        url = str(part["file"]["file_data"])
        if mime.startswith("audio/"):
            return {"type": "input_audio",
                    "input_audio": {"data": url.split(",", 1)[1], "format": _audio_format(mime)}}
        return {"type": "video_url", "video_url": {"url": url}}

    out = []
    for message in messages:
        content = message.get("content")
        if isinstance(content, list) and any(media_mime(part) for part in content):
            message = {**message, "content": [convert(part) for part in content]}
        out.append(message)
    return out
