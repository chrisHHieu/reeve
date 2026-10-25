"""`/v1/chat/completions`: lets any OpenAI-format client use the runtime as a model backend."""

from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi import FastAPI

from harness.llm.providers import AssistantTurn


def completion_body(model: str, turn: AssistantTurn) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": turn.text or ""}
    if turn.tool_calls:
        message["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
            }
            for call in turn.tool_calls
        ]
    return {
        "id": "chatcmpl-" + uuid.uuid4().hex[:12],
        "object": "chat.completion",
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": turn.finish_reason or "stop"}],
    }


def register_openai(app: FastAPI, manager) -> None:
    @app.post("/v1/chat/completions")
    def chat_completions(body: dict) -> dict[str, Any]:
        model = body.get("model", manager.model)
        turn = manager.provider_complete(model, body.get("messages", []), body.get("tools"))
        return completion_body(model, turn)
