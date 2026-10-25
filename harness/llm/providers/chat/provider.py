"""The Chat Completions provider, the workhorse for everything that speaks that wire.

It uses the OpenAI SDK's `chat.completions` only, which is what the whole OpenAI-compatible
world implements: DeepSeek, Z AI, Kimi and the like, resellers, Ollama, custom endpoints
(Azure OpenAI, vLLM) and the Bedrock and Vertex hosted-model paths. Native OpenAI models go to
`responses/` instead, because Chat Completions refuses function tools together with
reasoning on GPT-5.6 and later.
"""

from __future__ import annotations

import uuid
from typing import Any, Optional

from ..base import AssistantTurn, ModelCapabilities, ProviderClient
from ..capabilities import capabilities_for
from ..reasoning import reasoning_echo_field
from ..timeouts import stream_timeout
from .credentials import resolve_api_key
from .reply import read_stream, turn_from_response
from .request import build_request, send


class OpenAIProvider(ProviderClient):
    def __init__(
        self,
        client: Any = None,
        *,
        default_model: str = "gpt-5.6-sol",
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        secrets: Any = None,
        default_headers: Optional[dict[str, str]] = None,
        session_header: Optional[str] = None,
    ):
        """The SDK client is made on first use, so a loop can be assembled before any key
        exists (the desktop app takes the key in Settings after launch). The key is looked up
        then: argument, `OPENAI_API_KEY`, secret store. Tests pass `client`.

        `base_url` aims the same SDK at any compatible endpoint (Ollama at
        `http://localhost:11434/v1` with a placeholder key, a gateway); without it the
        behaviour is stock OpenAI. `default_headers` and `session_header` are for gateways that
        demand routing headers: `session_header` names the header whose value is the request's
        cache affinity (the session id), so one conversation is routed consistently, while the
        static headers go on every request.
        """
        self._client = client
        self._api_key = api_key
        self._base_url = base_url
        self._secrets = secrets
        self._default_headers = dict(default_headers or {})
        self._session_header = session_header
        # Stands in when a call has no session affinity (background passes). Any non-empty
        # value satisfies a gateway; a fixed one keeps those calls in one routing bucket.
        self._session_id = uuid.uuid4().hex
        self.default_model = default_model

    def _request_headers(self, affinity: Any) -> Optional[dict[str, str]]:
        if not self._session_header:
            return None
        return {**self._default_headers, self._session_header: str(affinity) if affinity else self._session_id}

    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        from openai import OpenAI
        import openai as sdk

        key = self._api_key or resolve_api_key(self._secrets)
        if not key:
            raise RuntimeError(
                "No model API key configured. Set OPENAI_API_KEY in the environment, "
                "or add your key in Manage → Settings."
            )
        options: dict[str, Any] = {"api_key": key}
        if self._base_url:
            options["base_url"] = self._base_url
        if self._default_headers:
            options["default_headers"] = self._default_headers
        # One attempt per call. The loop owns the retry policy, and a retry hidden inside the
        # SDK would multiply the budget and never reach the log.
        options["max_retries"] = 0
        # A stalled stream has to fail as a transport error the loop can retry, not hang the
        # turn for the SDK's ten-minute default.
        options["timeout"] = stream_timeout(sdk)
        self._client = OpenAI(**options)
        return self._client

    def _request(self, model, messages, tools, settings, *, streaming: bool) -> dict[str, Any]:
        kwargs, affinity = build_request(
            model=model, messages=messages, tools=tools, settings=settings,
            base_url=self._base_url, streaming=streaming,
        )
        headers = self._request_headers(affinity)
        if headers:
            kwargs["extra_headers"] = headers
        return kwargs

    def complete(self, *, model: str, messages: list[dict[str, Any]],
                 tools: Optional[list[dict[str, Any]]] = None, **settings: Any) -> AssistantTurn:
        kwargs = self._request(model, messages, tools, settings, streaming=False)
        client = self._ensure_client()
        response = send(client.chat.completions.create, kwargs)
        return turn_from_response(response, tools=tools)

    def stream(self, *, model: str, messages: list[dict[str, Any]],
               tools: Optional[list[dict[str, Any]]] = None, **settings: Any):
        kwargs = self._request(model, messages, tools, settings, streaming=True)
        client = self._ensure_client()
        chunks = send(client.chat.completions.create, kwargs)
        yield from read_stream(chunks, tools=tools)

    def capabilities(self, model: str) -> ModelCapabilities:
        return capabilities_for(model)

    def media_carry(self, model: str, mime: str) -> bool:
        # The model picker's "Inputs" column is the gate, not a list of formats.
        return mime.startswith(("audio/", "video/"))

    def reasoning_passback(self, model: str) -> Optional[str]:
        return reasoning_echo_field(model, self._base_url)
