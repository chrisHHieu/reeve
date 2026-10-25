"""A local HTTP server that speaks four provider wire formats — for SDK-boundary tests.

Every provider in `harness/llm/providers` reaches its API through a vendor SDK, and nothing
in the suite exercised that SDK's own transport: tests inject a fake `client`. So when
`anthropic` 1.0 moved to httpx2 and stopped accepting the `httpx.Timeout` we built, every
Anthropic-wire request died on its first connect (`TypeError` at `socket.settimeout`) and
4,500 green tests said nothing. This server lets the REAL SDK client send a REAL request
over a local socket and parse a REAL-shaped reply — text or a tool call, whole or
streamed — with no key and no network.

Shapes follow each vendor's published wire format (OpenAI Chat Completions + Responses,
Anthropic Messages, Gemini generateContent), trimmed to the fields a parser reads.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

TEXT = "pong"
TOOL = {"name": "read_file", "arguments": {"path": "a.py"}}
USAGE = {"input": 11, "output": 2, "cache_read": 5}


def _sse(events: list[tuple[str | None, dict | str]]) -> bytes:
    out = []
    for name, data in events:
        if name:
            out.append(f"event: {name}\n")
        payload = data if isinstance(data, str) else json.dumps(data)
        out.append(f"data: {payload}\n\n")
    return "".join(out).encode("utf-8")


# -- OpenAI Chat Completions ---------------------------------------------------------


def _chat_usage() -> dict:
    return {
        "prompt_tokens": USAGE["input"] + USAGE["cache_read"],
        "completion_tokens": USAGE["output"],
        "total_tokens": USAGE["input"] + USAGE["cache_read"] + USAGE["output"],
        "prompt_tokens_details": {"cached_tokens": USAGE["cache_read"]},
    }


def chat(body: dict, tool: bool) -> tuple[str, bytes]:
    call = {
        "id": "call_1",
        "type": "function",
        "function": {"name": TOOL["name"], "arguments": json.dumps(TOOL["arguments"])},
    }
    if not body.get("stream"):
        message: dict[str, Any] = {"role": "assistant", "content": None if tool else TEXT}
        if tool:
            message["tool_calls"] = [call]
        return "application/json", json.dumps(
            {
                "id": "chatcmpl-1", "object": "chat.completion", "created": 1, "model": body.get("model"),
                "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if tool else "stop"}],
                "usage": _chat_usage(),
            }
        ).encode()
    base = {"id": "chatcmpl-1", "object": "chat.completion.chunk", "created": 1, "model": body.get("model")}
    chunks = []
    if tool:
        chunks.append({**base, "choices": [{"index": 0, "delta": {"role": "assistant", "tool_calls": [{"index": 0, **call, "function": {"name": TOOL["name"], "arguments": ""}}]}, "finish_reason": None}]})
        chunks.append({**base, "choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "function": {"arguments": json.dumps(TOOL["arguments"])}}]}, "finish_reason": None}]})
        chunks.append({**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]})
    else:
        chunks.append({**base, "choices": [{"index": 0, "delta": {"role": "assistant", "content": TEXT[:2]}, "finish_reason": None}]})
        chunks.append({**base, "choices": [{"index": 0, "delta": {"content": TEXT[2:]}, "finish_reason": None}]})
        chunks.append({**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
    chunks.append({**base, "choices": [], "usage": _chat_usage()})
    return "text/event-stream", _sse([(None, c) for c in chunks] + [(None, "[DONE]")])


# -- OpenAI Responses ----------------------------------------------------------------


def _responses_body(model: str, tool: bool) -> dict:
    item = (
        {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": TOOL["name"],
         "arguments": json.dumps(TOOL["arguments"]), "status": "completed"}
        if tool
        else {"type": "message", "id": "msg_1", "status": "completed", "role": "assistant",
              "content": [{"type": "output_text", "text": TEXT, "annotations": []}]}
    )
    return {
        "id": "resp_1", "object": "response", "created_at": 1, "status": "completed", "model": model,
        "output": [item], "parallel_tool_calls": True, "tool_choice": "auto", "tools": [],
        "usage": {
            "input_tokens": USAGE["input"] + USAGE["cache_read"],
            "input_tokens_details": {"cached_tokens": USAGE["cache_read"]},
            "output_tokens": USAGE["output"],
            "output_tokens_details": {"reasoning_tokens": 0},
            "total_tokens": USAGE["input"] + USAGE["cache_read"] + USAGE["output"],
        },
    }


def responses(body: dict, tool: bool) -> tuple[str, bytes]:
    final = _responses_body(body.get("model", "m"), tool)
    if not body.get("stream"):
        return "application/json", json.dumps(final).encode()
    started = {**final, "status": "in_progress", "output": [], "usage": None}
    events: list[tuple[str | None, dict | str]] = [
        ("response.created", {"type": "response.created", "sequence_number": 0, "response": started}),
    ]
    item = final["output"][0]
    events.append(("response.output_item.added", {"type": "response.output_item.added", "sequence_number": 1, "output_index": 0, "item": {**item, "status": "in_progress"}}))
    if tool:
        events.append(("response.function_call_arguments.delta", {"type": "response.function_call_arguments.delta", "sequence_number": 2, "item_id": "fc_1", "output_index": 0, "delta": item["arguments"]}))
        events.append(("response.function_call_arguments.done", {"type": "response.function_call_arguments.done", "sequence_number": 3, "item_id": "fc_1", "output_index": 0, "arguments": item["arguments"]}))
    else:
        events.append(("response.output_text.delta", {"type": "response.output_text.delta", "sequence_number": 2, "item_id": "msg_1", "output_index": 0, "content_index": 0, "delta": TEXT, "logprobs": []}))
        events.append(("response.output_text.done", {"type": "response.output_text.done", "sequence_number": 3, "item_id": "msg_1", "output_index": 0, "content_index": 0, "text": TEXT, "logprobs": []}))
    events.append(("response.output_item.done", {"type": "response.output_item.done", "sequence_number": 4, "output_index": 0, "item": item}))
    events.append(("response.completed", {"type": "response.completed", "sequence_number": 5, "response": final}))
    return "text/event-stream", _sse(events)


# -- Anthropic Messages --------------------------------------------------------------


def _anthropic_usage(output: int) -> dict:
    return {"input_tokens": USAGE["input"], "output_tokens": output,
            "cache_read_input_tokens": USAGE["cache_read"], "cache_creation_input_tokens": 0}


def anthropic(body: dict, tool: bool, delta_usage: bool = False) -> tuple[str, bytes]:
    """`delta_usage`: the prompt counts arrive in `message_delta`, with zeros in
    `message_start` — MiniMax's shape on opencode-go's Anthropic wire."""
    block = (
        {"type": "tool_use", "id": "toolu_1", "name": TOOL["name"], "input": TOOL["arguments"]}
        if tool
        else {"type": "text", "text": TEXT}
    )
    stop = "tool_use" if tool else "end_turn"
    if not body.get("stream"):
        return "application/json", json.dumps(
            {"id": "msg_1", "type": "message", "role": "assistant", "model": body.get("model"),
             "content": [block], "stop_reason": stop, "stop_sequence": None,
             "usage": _anthropic_usage(USAGE["output"])}
        ).encode()
    opening_usage = (
        {"input_tokens": 0, "output_tokens": 0} if delta_usage else _anthropic_usage(1)
    )
    start = {"type": "message_start", "message": {
        "id": "msg_1", "type": "message", "role": "assistant", "model": body.get("model"), "content": [],
        "stop_reason": None, "stop_sequence": None, "usage": opening_usage}}
    closing_usage = _anthropic_usage(USAGE["output"]) if delta_usage else {"output_tokens": USAGE["output"]}
    if tool:
        opening = {**block, "input": {}}
        delta = {"type": "input_json_delta", "partial_json": json.dumps(TOOL["arguments"])}
    else:
        opening = {"type": "text", "text": ""}
        delta = {"type": "text_delta", "text": TEXT}
    events = [
        ("message_start", start),
        ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": opening}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": delta}),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop, "stop_sequence": None},
                           "usage": closing_usage}),
        ("message_stop", {"type": "message_stop"}),
    ]
    return "text/event-stream", _sse(events)


# -- Gemini --------------------------------------------------------------------------


def gemini(stream: bool, tool: bool) -> tuple[str, bytes]:
    part = {"functionCall": {"name": TOOL["name"], "args": TOOL["arguments"]}} if tool else {"text": TEXT}
    reply = {
        "candidates": [{"content": {"role": "model", "parts": [part]}, "finishReason": "STOP", "index": 0}],
        "usageMetadata": {
            "promptTokenCount": USAGE["input"] + USAGE["cache_read"],
            "candidatesTokenCount": USAGE["output"],
            "totalTokenCount": USAGE["input"] + USAGE["cache_read"] + USAGE["output"],
            "cachedContentTokenCount": USAGE["cache_read"],
        },
        "modelVersion": "fake",
    }
    if not stream:
        return "application/json", json.dumps(reply).encode()
    return "text/event-stream", _sse([(None, reply)])


# -- server ----------------------------------------------------------------------------


class WireServer:
    """`with WireServer(tool=False) as server:` — `server.url`, `server.requests`."""

    def __init__(self, *, tool: bool = False, anthropic_delta_usage: bool = False) -> None:
        self.tool = tool
        self.anthropic_delta_usage = anthropic_delta_usage
        self.requests: list[dict[str, Any]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:  # quiet
                pass

            def do_POST(self) -> None:  # noqa: N802 — the stdlib's name
                length = int(self.headers.get("content-length") or 0)
                raw = self.rfile.read(length) if length else b""
                try:
                    body = json.loads(raw or b"{}")
                except ValueError:
                    body = {}
                path = self.path
                outer.requests.append({"path": path, "body": body, "headers": dict(self.headers)})
                if "chat/completions" in path:
                    kind, data = chat(body, outer.tool)
                elif path.rstrip("/").endswith("responses") or "/responses?" in path:
                    kind, data = responses(body, outer.tool)
                elif "/messages" in path:
                    kind, data = anthropic(body, outer.tool, outer.anthropic_delta_usage)
                elif "generateContent" in path or "GenerateContent" in path:
                    kind, data = gemini("streamGenerateContent" in path, outer.tool)
                else:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("content-type", kind)
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self) -> "WireServer":
        self._thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self._server.shutdown()
        self._server.server_close()
