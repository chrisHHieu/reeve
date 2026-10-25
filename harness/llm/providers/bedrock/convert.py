"""The conversation in the loop's own (OpenAI-shaped) form, as a Converse API request.

Converse follows the same discipline as the Messages API: leading system messages become the
top-level `system`, tool results become `toolResult` blocks inside a user message, and
neighbours with the same role are merged so a turn's parallel results share one message.
It differs in carrying images and documents as raw bytes (no URL sources) and in naming
fields in camelCase.
"""

from __future__ import annotations

import base64
import re
from typing import Any, Optional

from ..arguments import parse_arguments

#: Converse has no required output limit, but the default differs a lot between families
#: (Meta's is 512, which cuts an agent turn off inside a tool call). 4096 fits all of them.
DEFAULT_MAX_TOKENS = 4096

_IMAGE_DATA_URL = re.compile(r"^data:image/([a-z0-9.+-]+);base64,(.+)$", re.IGNORECASE | re.DOTALL)
_PDF_DATA_URL = re.compile(r"^data:application/pdf;base64,(.+)$", re.IGNORECASE | re.DOTALL)
#: A document name may hold only letters, digits, whitespace, hyphens, parentheses, brackets.
_NOT_IN_DOCUMENT_NAME = re.compile(r"[^A-Za-z0-9\s\-\(\)\[\]]+")


def _image(url: str) -> dict[str, Any]:
    found = _IMAGE_DATA_URL.match(url)
    if not found:
        return {"text": "[unsupported image attachment]"}
    kind = found.group(1).lower()
    return {"image": {"format": "jpeg" if kind == "jpg" else kind,
                      "source": {"bytes": base64.b64decode(found.group(2))}}}


def _document(part: dict[str, Any]) -> dict[str, Any]:
    file = part.get("file") or {}
    found = _PDF_DATA_URL.match(file.get("file_data") or "")
    if not found:
        return {"text": "[unsupported file attachment]"}
    name = _NOT_IN_DOCUMENT_NAME.sub("-", str(file.get("filename") or "document"))
    return {"document": {"format": "pdf", "name": name or "document",
                         "source": {"bytes": base64.b64decode(found.group(1))}}}


def _user_content(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return [{"text": content}] if content else []
    blocks: list[dict[str, Any]] = []
    for part in content or []:
        kind = part.get("type") if isinstance(part, dict) else None
        if kind == "text":
            if part.get("text"):
                blocks.append({"text": part["text"]})
        elif kind == "image_url":
            blocks.append(_image((part.get("image_url") or {}).get("url") or ""))
        elif kind == "file":
            blocks.append(_document(part))
    return blocks


def _assistant_content(message: dict[str, Any]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    if isinstance(message.get("content"), str) and message["content"]:
        blocks.append({"text": message["content"]})
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        blocks.append({"toolUse": {
            "toolUseId": call.get("id") or "",
            "name": function.get("name") or "",
            "input": parse_arguments(function.get("arguments")),
        }})
    return blocks


def convert_messages(messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(Converse `system`, Converse `messages`)."""
    leading = 0
    system: list[str] = []
    while leading < len(messages) and messages[leading].get("role") == "system":
        content = messages[leading].get("content")
        if isinstance(content, str) and content:
            system.append(content)
        leading += 1

    converted: list[dict[str, Any]] = []
    for message in messages[leading:]:
        role = message.get("role")
        if role == "system":
            if message.get("content"):
                converted.append({"role": "user",
                                  "content": [{"text": f"<system>\n{message['content']}\n</system>"}]})
        elif role == "user":
            blocks = _user_content(message.get("content"))
            if blocks:
                converted.append({"role": "user", "content": blocks})
        elif role == "assistant":
            blocks = _assistant_content(message)
            if blocks:
                converted.append({"role": "assistant", "content": blocks})
        elif role == "tool":
            converted.append({"role": "user", "content": [{"toolResult": {
                "toolUseId": message.get("tool_call_id") or "",
                "content": [{"text": str(message.get("content") or "")}],
            }}]})

    merged: list[dict[str, Any]] = []
    for message in converted:
        if merged and merged[-1]["role"] == message["role"]:
            merged[-1]["content"].extend(message["content"])
        else:
            merged.append(message)

    if not merged:
        raise ValueError("no convertible messages for the Bedrock Converse API")
    if merged[0]["role"] != "user":
        merged.insert(0, {"role": "user", "content": [{"text": "(continued)"}]})
    return ([{"text": "\n\n".join(system)}] if system else []), merged


def convert_tools(tools: Optional[list[dict[str, Any]]]) -> Optional[dict[str, Any]]:
    """Function schemas as a Converse `toolConfig`, or None without tools (Converse refuses an
    empty list)."""
    specs = []
    for tool in tools or []:
        function = tool.get("function") or {}
        schema = function.get("parameters")
        if not isinstance(schema, dict) or not schema.get("type"):
            schema = {"type": "object", "properties": {}}
        spec: dict[str, Any] = {"name": function.get("name") or "", "inputSchema": {"json": schema}}
        if function.get("description"):
            spec["description"] = function["description"]
        specs.append({"toolSpec": spec})
    return {"tools": specs} if specs else None


def inference_config(settings: dict[str, Any]) -> dict[str, Any]:
    """The request settings as Converse's `inferenceConfig`."""
    config: dict[str, Any] = {"maxTokens": int(settings.get("max_tokens") or DEFAULT_MAX_TOKENS)}
    if settings.get("temperature") is not None:
        config["temperature"] = settings["temperature"]
    if settings.get("top_p") is not None:
        config["topP"] = settings["top_p"]
    stop = settings.get("stop_sequences") or settings.get("stop")
    if stop:
        config["stopSequences"] = [stop] if isinstance(stop, str) else list(stop)
    return config
