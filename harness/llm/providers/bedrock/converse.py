"""The Converse API: Bedrock's one wire format for Llama, Nova, Mistral, Cohere, DeepSeek and
the rest.

boto3 returns plain dicts, so every mapping here is dict-shaped, unlike the attribute objects
the other SDKs hand back.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from ..arguments import parse_arguments
from ..base import AssistantTurn, ModelCapabilities, ProviderClient, StreamChunk, TokenUsage, ToolCall
from ..capabilities import capabilities_for
from .caching import add_cache_points, rejects_cache_points, strip_cache_points
from .convert import convert_messages, convert_tools, inference_config
from .credentials import session_kwargs

_FINISH_REASONS = {
    "end_turn": "stop",
    "tool_use": "tool_calls",
    "max_tokens": "length",
    "stop_sequence": "stop",
    "guardrail_intervened": "stop",
    "content_filtered": "stop",
}


def _finish_reason(stop_reason: Any) -> Any:
    return _FINISH_REASONS.get(stop_reason, stop_reason)


def usage_from(usage: Any) -> Optional[TokenUsage]:
    """Counts from a Converse `usage` dict. `inputTokens` leaves out the cached parts."""
    if not isinstance(usage, dict):
        return None
    return TokenUsage(
        input=int(usage.get("inputTokens") or 0),
        output=int(usage.get("outputTokens") or 0),
        cache_read=int(usage.get("cacheReadInputTokens") or 0),
        cache_write=int(usage.get("cacheWriteInputTokens") or 0),
    )


class ConverseClient(ProviderClient):
    """Any Bedrock model, over Converse."""

    def __init__(
        self,
        client: Any = None,
        *,
        region: Optional[str] = None,
        bedrock_api_key: Optional[str] = None,
        profile_name: Optional[str] = None,
        access_key_id: Optional[str] = None,
        secret_access_key: Optional[str] = None,
        session_token: Optional[str] = None,
    ):
        self._client = client  # tests pass a fake that returns dicts
        self._region = region
        self._bedrock_api_key = bedrock_api_key
        self._session_kwargs = session_kwargs(profile_name, access_key_id, secret_access_key, session_token)
        # Whether each model accepts `cachePoint`. Assumed yes until the service says no, then
        # remembered, so the refusal costs one request per model per process.
        self._cacheable: dict[str, bool] = {}

    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            import boto3
        except ImportError as exc:
            raise RuntimeError(
                "AWS Bedrock support needs the boto3 package — "
                "install with `pip install 'reeve[bedrock]'`."
            ) from exc
        # boto3 has no per-client bearer argument. It reads the environment variable and then
        # prefers bearer auth for Bedrock, so the configured key is published there; this
        # process is ours.
        if self._bedrock_api_key:
            os.environ["AWS_BEARER_TOKEN_BEDROCK"] = self._bedrock_api_key
        session = boto3.session.Session(**self._session_kwargs)
        self._client = session.client("bedrock-runtime", region_name=self._region)
        return self._client

    def _request_kwargs(self, *, model: str, messages: list[dict[str, Any]],
                        tools: Optional[list[dict[str, Any]]], settings: dict[str, Any]) -> dict[str, Any]:
        system, converted = convert_messages(messages)
        kwargs: dict[str, Any] = {
            "modelId": model,
            "messages": converted,
            "inferenceConfig": inference_config(settings),
        }
        if system:
            kwargs["system"] = system
        tool_config = convert_tools(tools)
        if tool_config:
            kwargs["toolConfig"] = tool_config
        if self._cacheable.get(model, True):
            add_cache_points(kwargs)
        return kwargs

    def _call(self, client: Any, method: str, kwargs: dict[str, Any]) -> Any:
        try:
            return getattr(client, method)(**kwargs)
        except Exception as exc:
            # Converse serves every Bedrock model, and most of them (Llama, Mistral, Titan,
            # Cohere) refuse `cachePoint` instead of ignoring it. No allowlist can be checked
            # without an AWS account, so ask once and trust the answer: strip the cache
            # points, remember it for the model, send again. That costs one refused request
            # per model per process and never a turn.
            if rejects_cache_points(exc) and strip_cache_points(kwargs):
                self._cacheable[kwargs.get("modelId", "")] = False
                return getattr(client, method)(**kwargs)
            if exc.__class__.__name__ == "NoCredentialsError":
                raise RuntimeError(
                    "No AWS credentials found — add keys or a profile in Settings ▸ "
                    "Models, or configure the AWS CLI (`aws configure` / `aws sso login`)."
                ) from exc
            raise

    def complete(self, *, model: str, messages: list[dict[str, Any]],
                 tools: Optional[list[dict[str, Any]]] = None, **settings: Any) -> AssistantTurn:
        kwargs = self._request_kwargs(model=model, messages=messages, tools=tools, settings=settings)
        response = self._call(self._ensure_client(), "converse", kwargs)

        text: list[str] = []
        reasoning: list[str] = []
        calls: list[ToolCall] = []
        for block in ((response.get("output") or {}).get("message") or {}).get("content") or []:
            if "text" in block:
                text.append(block["text"] or "")
            elif "toolUse" in block:
                tool = block["toolUse"]
                calls.append(ToolCall(
                    id=tool.get("toolUseId") or "",
                    name=tool.get("name") or "",
                    arguments=parse_arguments(tool.get("input")),
                ))
            elif "reasoningContent" in block:
                thought = (block["reasoningContent"].get("reasoningText") or {}).get("text") or ""
                if thought:
                    reasoning.append(thought)
        stop_reason = response.get("stopReason")
        return AssistantTurn(
            text="".join(text) or None,
            tool_calls=calls,
            finish_reason=_finish_reason(stop_reason),
            raw=response,
            reasoning="".join(reasoning) or None,
            usage=usage_from(response.get("usage")),
        )

    def stream(self, *, model: str, messages: list[dict[str, Any]],
               tools: Optional[list[dict[str, Any]]] = None, **settings: Any):
        kwargs = self._request_kwargs(model=model, messages=messages, tools=tools, settings=settings)
        response = self._call(self._ensure_client(), "converse_stream", kwargs)

        text: list[str] = []
        reasoning: list[str] = []
        tool_input: dict[int, dict[str, str]] = {}
        stop_reason = None
        usage: Optional[TokenUsage] = None

        for event in response.get("stream") or []:
            if "contentBlockStart" in event:
                start = event["contentBlockStart"]
                tool = (start.get("start") or {}).get("toolUse")
                if tool:
                    tool_input[start.get("contentBlockIndex", 0)] = {
                        "id": tool.get("toolUseId") or "",
                        "name": tool.get("name") or "",
                        "json": "",
                    }
            elif "contentBlockDelta" in event:
                block = event["contentBlockDelta"]
                delta = block.get("delta") or {}
                if delta.get("text"):
                    text.append(delta["text"])
                    yield StreamChunk(text_delta=delta["text"])
                elif "toolUse" in delta:
                    part = tool_input.get(block.get("contentBlockIndex", 0))
                    if part is not None:
                        part["json"] += delta["toolUse"].get("input") or ""
                elif "reasoningContent" in delta:
                    thought = delta["reasoningContent"].get("text") or ""
                    if thought:
                        reasoning.append(thought)
                        yield StreamChunk(reasoning_delta=thought)
            elif "messageStop" in event:
                stop_reason = event["messageStop"].get("stopReason") or stop_reason
            elif "metadata" in event:
                usage = usage_from(event["metadata"].get("usage")) or usage

        calls = [
            ToolCall(id=part["id"], name=part["name"], arguments=parse_arguments(part["json"]))
            for _, part in sorted(tool_input.items())
        ]
        yield StreamChunk(turn=AssistantTurn(
            text="".join(text) or None,
            tool_calls=calls,
            finish_reason=_finish_reason(stop_reason),
            reasoning="".join(reasoning) or None,
            usage=usage,
        ))

    def capabilities(self, model: str) -> ModelCapabilities:
        return capabilities_for(f"bedrock:other/{model}")
