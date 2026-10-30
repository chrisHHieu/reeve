"""OpenCode Go — one gateway, several wires, an endpoint per model.

Most of OpenCode Go's catalogue is OpenAI chat/completions, but some families are served
over the Anthropic Messages API, some over the OpenAI Responses API and Gemini over Google's
own. models.dev records this per model as `provider.npm` (the wire) and `provider.api` (the
endpoint), and opencode itself reads exactly those to reach a model. Reeve's router picks ONE
protocol per provider, so a single `OpenAIProvider` could only ever reach part of the gateway.

This client keeps that promise instead of hiding the rest: it reads each model's `wire` and
`api` from the projected catalogue (providers/catalog.py keeps both) and sends the call through
the matching transport at that endpoint. A base URL on any other host is a proxy the person
runs, in the gateway's original single-root shape, and is used as given.

`x-opencode-session`, which the gateway 400s without, rides every wire with the session id, so
each conversation is routed (and its prompt cached) as one, whatever the model's wire.
"""

from __future__ import annotations

import re
from typing import Any, Optional
from urllib.parse import urlparse

from .claude import AnthropicProvider
from .base import AssistantTurn, ModelCapabilities, ProviderClient
from .capabilities import capabilities_for
from .chat import OpenAIProvider
from .gemini import GeminiProvider
from .responses import OpenAIResponsesProvider

#: The gateway wants a routing session on EVERY request, whatever the wire. OpenCode's own client
#: (`packages/core/src/session/model-request.ts`, read 2026-10-26) also sends the session's affinity
#: as `x-session-affinity` and `X-Session-Id`, which a gateway can pass to the upstream that serves
#: the model. Sent the same way here, all three with the one value. Their effect on DeepSeek's cache
#: is the store's to say (prediction `opencode-affinity-headers`).
_SESSION_HEADER = ("x-opencode-session", "x-session-affinity", "X-Session-Id")

#: The gateway's own host. A base here is routed by the catalogue's endpoints.
_GATEWAY_HOST = "opencode.ai"

#: Each wire's endpoint on the gateway, for a model the catalogue has no endpoint for
#: (https://opencode.ai/docs/go/#endpoints, 2026-10-21). The website's `/zen/go/v1` forwarder
#: was removed on 2026-10-20 (opencode #57434); the path still answered the next day.
GATEWAY_ROOTS = {
    "chat": "https://opencode.ai/inference/go/openai/v1",
    "responses": "https://opencode.ai/inference/go/openai/v1",
    "messages": "https://opencode.ai/inference/go/anthropic/v1",
    "gemini": "https://opencode.ai/inference/go/google/v1beta",
}

#: What the endpoint field is prefilled with: the gateway's provider-level `api` on models.dev.
DEFAULT_BASE_URL = GATEWAY_ROOTS["chat"]


def _messages_root(base_url: str) -> str:
    """Anthropic's SDK appends `/v1/messages`, but the gateway's endpoints already end in
    `/v1`. Drop the trailing segment so the Messages path lands on `<root>/v1/messages`, not
    `<root>/v1/v1/messages`."""
    return re.sub(r"/v1/?$", "", (base_url or "").rstrip("/"))


def _gemini_root(base_url: str) -> str:
    """For a base in the original shape: the gateway's old `/zen/go/v1` path forwarded chat,
    Messages and Responses, while Gemini's `/models/{id}:*` route was mounted on
    `/inference/go/v1`. A proxy on another host is left as it is."""
    return re.sub(r"/zen/go/v1/?$", "/inference/go/v1", (base_url or "").rstrip("/"))


def on_gateway(base_url: str) -> bool:
    """Whether a base URL is the gateway itself (any of its paths, old or new), not a proxy."""
    host = (urlparse((base_url or "").strip()).hostname or "").lower()
    return host == _GATEWAY_HOST


class OpenCodeGoProvider(ProviderClient):
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        headers: Optional[dict[str, str]] = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = (base_url or "").strip().rstrip("/") or DEFAULT_BASE_URL
        self._headers = dict(headers or {})
        self._routed = on_gateway(self._base_url)
        self.default_model = "deepseek-v4.1-flash"
        #: One transport per (wire, endpoint), made on first use.
        self._clients: dict[tuple[str, str], ProviderClient] = {}

    def _entry(self, model: str) -> dict[str, Any]:
        """The model's catalogue row. Disk-only: the picker that offered the model already
        warmed the cache, and a fetch here would be a network call on the hot path."""
        try:
            from .catalog import models_dev

            return models_dev().get(f"opencode-go:{model}") or {}
        except Exception:
            return {}

    def _wire_for(self, model: str) -> str:
        """The model's transport from the catalogue (`wire`), defaulting to chat."""
        wire = self._entry(model).get("wire")
        if wire and not (wire == "chat" and model.startswith("gemini-")):
            return wire
        if model.startswith("gemini-"):
            return "gemini"
        return "chat"

    def _endpoint_for(self, model: str, wire: str) -> str:
        """Where the model is served: the catalogue's endpoint for it on the gateway, else the
        wire's documented root; on a proxy, the person's base in the original shape."""
        if not self._routed:
            return _gemini_root(self._base_url) if wire == "gemini" else self._base_url
        stated = self._entry(model).get("api")
        if isinstance(stated, str) and on_gateway(stated):
            return stated
        return GATEWAY_ROOTS.get(wire, DEFAULT_BASE_URL)

    def _client_for(self, model: str) -> ProviderClient:
        wire = self._wire_for(model)
        endpoint = self._endpoint_for(model, wire)
        client = self._clients.get((wire, endpoint))
        if client is None:
            client = self._clients[(wire, endpoint)] = self._make(wire, endpoint)
        return client

    def _make(self, wire: str, endpoint: str) -> ProviderClient:
        common: dict[str, Any] = {
            "api_key": self._api_key,
            "default_headers": self._headers,
            "session_header": _SESSION_HEADER,
        }
        if wire == "messages":
            return AnthropicProvider(base_url=_messages_root(endpoint), **common)
        if wire == "responses":
            return OpenAIResponsesProvider(base_url=endpoint, **common)
        if wire == "gemini":
            return GeminiProvider(base_url=endpoint, **common)
        return OpenAIProvider(base_url=endpoint, **common)

    def complete(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
        **settings: Any,
    ) -> AssistantTurn:
        client = self._client_for(model)
        if isinstance(client, AnthropicProvider):
            # The Anthropic SDK refuses a NON-streaming call whose max_tokens could run
            # past ten minutes — Reeve's 32k default does — and this family is served
            # over Messages only. Drain the streaming path and return its final turn
            # (it always ends with one), so `complete()` works for the extractor,
            # organizer and the /v1/chat/completions proxy too, not just the live engine.
            turn: Optional[AssistantTurn] = None
            for chunk in client.stream(
                model=model, messages=messages, tools=tools, **settings
            ):
                if chunk.turn is not None:
                    turn = chunk.turn
            if turn is not None:
                return turn
        return client.complete(model=model, messages=messages, tools=tools, **settings)

    def stream(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
        **settings: Any,
    ):
        return self._client_for(model).stream(
            model=model, messages=messages, tools=tools, **settings
        )

    def capabilities(self, model: str) -> ModelCapabilities:
        return capabilities_for(f"opencode-go:{model}")

    def media_carry(self, model: str, mime: str) -> bool:
        """What the model's own wire can carry: a chat-wire model (MiMo) takes WAV/MP3 audio and
        MP4/MPEG/MOV/WebM video, a Messages-wire one (Claude-family) takes none. Asking the sub-client
        keeps this gateway from being the one route that hears nothing whatever the catalogue says."""
        return self._client_for(model).media_carry(model, mime)

    def reasoning_passback(self, model: str) -> Optional[str]:
        """The sub-client's answer: its chat wire echoes reasoning for the thinking-mode families
        (DeepSeek, Kimi, GLM, MiMo); the Messages wire replays Claude's thinking itself."""
        return self._client_for(model).reasoning_passback(model)
