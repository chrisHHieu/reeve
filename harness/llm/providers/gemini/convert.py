"""The conversation in the loop's own (OpenAI-shaped) form, as Gemini `contents`.

What Gemini wants differently, and what is done about it:

- The system prompt is `system_instruction` in the request config, not a role.
- Roles are `user` and `model`; a tool result is a `function_response` part in a user turn.
- Function calls carry no ids on the wire. The loop gets synthesized ones, and a result is
  matched back to its call by function name, through an id-to-name map built from the
  assistant turns as they are converted.
- Gemini 3 returns a `thought_signature` on response parts that must go back on the same parts
  in later requests, or tool loops break. They travel on the stored assistant message as the
  `_gemini` sidecar, base64 text that the SDK turns back into bytes on send.
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from ..arguments import parse_arguments

_IMAGE_DATA_URL = re.compile(r"^data:(image/[a-z0-9.+-]+);base64,(.+)$", re.IGNORECASE | re.DOTALL)
_PDF_DATA_URL = re.compile(r"^data:application/pdf;base64,(.+)$", re.IGNORECASE | re.DOTALL)
_MEDIA_DATA_URL = re.compile(
    r"^data:((?:audio|video)/[a-z0-9.+-]+);base64,(.+)$", re.IGNORECASE | re.DOTALL
)


def _inline(mime: str, data: str) -> dict[str, Any]:
    return {"inline_data": {"mime_type": mime, "data": data}}


def _image_part(url: str) -> Optional[dict[str, Any]]:
    """An image as an inline part. Attachments are data URLs; an http(s) URL is not fetched by
    the API, so it cannot be sent."""
    found = _IMAGE_DATA_URL.match(url or "")
    return _inline(found.group(1).lower(), found.group(2)) if found else None


def _file_part(part: dict[str, Any]) -> Optional[dict[str, Any]]:
    """A `file` part (a PDF, or audio or video) as an inline part."""
    data = (part.get("file") or {}).get("file_data") or ""
    pdf = _PDF_DATA_URL.match(data)
    if pdf:
        return _inline("application/pdf", pdf.group(1))
    media = _MEDIA_DATA_URL.match(data)
    return _inline(media.group(1).lower(), media.group(2)) if media else None


def _user_parts(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return [{"text": content}] if content else []
    parts: list[dict[str, Any]] = []
    for part in content or []:
        kind = part.get("type") if isinstance(part, dict) else None
        if kind == "text":
            if part.get("text"):
                parts.append({"text": part["text"]})
        elif kind == "image_url":
            url = (part.get("image_url") or {}).get("url") or ""
            parts.append(_image_part(url) or {"text": "[unsupported image attachment]"})
        elif kind == "file":
            parts.append(_file_part(part) or {"text": "[unsupported file attachment]"})
    return parts


def _result_payload(content: Any) -> dict[str, Any]:
    """A tool result as the JSON object a function response has to be."""
    if isinstance(content, dict):
        return content
    try:
        parsed = json.loads(content)
    except (TypeError, json.JSONDecodeError):
        return {"result": str(content or "")}
    return parsed if isinstance(parsed, dict) else {"result": parsed}


def _model_parts(message: dict[str, Any], call_names: dict[str, str]) -> list[dict[str, Any]]:
    sidecar = message.get("_gemini") or {}
    signatures = sidecar.get("call_sigs") or []
    parts: list[dict[str, Any]] = []
    if isinstance(message.get("content"), str) and message["content"]:
        part: dict[str, Any] = {"text": message["content"]}
        if sidecar.get("text_sig"):
            part["thought_signature"] = sidecar["text_sig"]
        parts.append(part)
    for position, call in enumerate(message.get("tool_calls") or []):
        function = call.get("function") or {}
        name = function.get("name") or ""
        call_names[call.get("id") or ""] = name
        part = {"function_call": {"name": name, "args": parse_arguments(function.get("arguments"))}}
        if position < len(signatures) and signatures[position]:
            part["thought_signature"] = signatures[position]
        parts.append(part)
    return parts


def carries_responses(content: dict[str, Any]) -> bool:
    """Whether a content is a function-response turn, which may hold nothing else.

    A content with no parts answers False. It cannot arrive today, since a message that
    converts to nothing is dropped before merging, but `all([])` is True, and calling an empty
    turn a response turn would fold real text into it the day one appears.
    """
    parts = content["parts"]
    return bool(parts) and all("function_response" in part for part in parts)


def convert_messages(messages: list[dict[str, Any]]) -> tuple[Optional[str], list[dict[str, Any]]]:
    """(`system_instruction`, Gemini `contents`)."""
    leading = 0
    system: list[str] = []
    while leading < len(messages) and messages[leading].get("role") == "system":
        content = messages[leading].get("content")
        if isinstance(content, str) and content:
            system.append(content)
        leading += 1

    call_names: dict[str, str] = {}
    converted: list[dict[str, Any]] = []
    for message in messages[leading:]:
        role = message.get("role")
        if role == "system":
            if message.get("content"):
                converted.append({"role": "user",
                                  "parts": [{"text": f"<system>\n{message['content']}\n</system>"}]})
        elif role == "user":
            parts = _user_parts(message.get("content"))
            if parts:
                converted.append({"role": "user", "parts": parts})
        elif role == "assistant":
            parts = _model_parts(message, call_names)
            if parts:
                converted.append({"role": "model", "parts": parts})
        elif role == "tool":
            call_id = message.get("tool_call_id") or ""
            converted.append({"role": "user", "parts": [{"function_response": {
                "name": call_names.get(call_id) or call_id,
                "response": _result_payload(message.get("content")),
            }}]})

    # Neighbours with the same role merge into one content, with one exception: a turn made of
    # `function_response` parts holds nothing else. Tool results are user turns, so a plain user
    # message after them (the to-do nudge, for one) used to be folded in, and Vertex refuses
    # that turn outright. With it discarded it then reports the model turn before it as the
    # last one, "Requests ending with a model turn are not supported". Replaying a real call
    # against the live API showed `[function_response, text]` in one turn is that 400, while
    # `[function_response]` and then a separate `[text]` turn is accepted. Consecutive user
    # turns are fine; a mixed one is not. Every 400 of this shape in the store (9 of 9) followed
    # a nudge, all on Vertex Gemini, and the same nudge elsewhere is harmless, so the fix is at
    # this boundary and not in the nudge.
    merged: list[dict[str, Any]] = []
    for message in converted:
        if (merged and merged[-1]["role"] == message["role"]
                and carries_responses(merged[-1]) == carries_responses(message)):
            merged[-1]["parts"].extend(message["parts"])
        else:
            merged.append(message)

    if not merged:
        raise ValueError("no convertible messages for the Gemini API")
    if merged[0]["role"] != "user":
        merged.insert(0, {"role": "user", "parts": [{"text": "(continued)"}]})
    if merged[-1]["role"] != "user":
        # Gemini refuses a request that ends on a model turn, as a hard error, so the turn
        # would die with a red notice. Every other provider accepts one, which makes this the
        # boundary that has to know; the alternative is each caller remembering a rule that
        # holds for one vendor. It mirrors the leading guard above, for the same reason. It does
        # not claim to know how the history got here: a resumed turn, a compaction boundary
        # that lands on an assistant message, or a tool result that never came back can each do
        # it, and the right thing at this layer is to make the request valid.
        merged.append({"role": "user", "parts": [{"text": "(continue)"}]})
    return ("\n\n".join(system) or None), merged
