"""OpenAI's Responses API (`/responses`), for native OpenAI and for compatible vendors that
implement it.

Chat Completions refuses function tools together with any reasoning effort but `none` on
GPT-5.6 and later ("use /v1/responses"), which left reasoning pinned off for native OpenAI
models. This provider is the path that allows both: reasoning with tools at real effort
levels, streamed reasoning summaries (the same `reasoning_delta` and `AssistantTurn.reasoning`
the app already draws), and chain-of-thought continuity across tool round trips through
`store: false` with `include: ["reasoning.encrypted_content"]`, so nothing is kept on the
server.

Routing: the `openai` entry with no custom `base_url` builds this class. Most custom endpoints
(Azure, vLLM, the compatible vendors) stay on the Chat Completions provider; a vendor that
implements this wire can opt in with its own base URL (`registry.py`).
"""

from __future__ import annotations

import uuid
from typing import Any, Optional

from ..base import AssistantTurn, ModelCapabilities, ProviderClient
from ..capabilities import capabilities_for
from ..chat.credentials import resolve_api_key
from ..turns import session_headers
from .reply import read_stream, turn_from_response
from .request import build_request, send


class OpenAIResponsesProvider(ProviderClient):
    def __init__(
        self,
        client: Any = None,
        *,
        default_model: str = "gpt-5.6-sol",
        api_key: Optional[str] = None,
        secrets: Any = None,
        base_url: Optional[str] = None,
        reasoning_summary: bool = True,
        default_headers: Optional[dict[str, str]] = None,
        session_header: Optional[str] = None,
    ):
        """Built lazily like the Chat Completions provider: the SDK client is made on first
        use and the key looked up then (argument, environment, secret store). `base_url` is
        opt-in; stock OpenAI leaves it unset. `session_header` is a gateway's routing header,
        valued with the session id as on the chat wire."""
        self._client = client
        self._api_key = api_key
        self._secrets = secrets
        self._base_url = (base_url or "").strip().rstrip("/") or None
        if not isinstance(reasoning_summary, bool):
            raise TypeError("reasoning_summary must be a bool")
        self._reasoning_summary = reasoning_summary
        self._default_headers = dict(default_headers or {})  # static headers a gateway demands
        self._session_header = session_header
        self._session_id = uuid.uuid4().hex
        self.default_model = default_model

    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        from openai import OpenAI

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
        self._client = OpenAI(**options)
        return self._client

    def _request_kwargs(self, *, model: str, messages: list[dict[str, Any]],
                        tools: Optional[list[dict[str, Any]]], settings: dict[str, Any]) -> dict[str, Any]:
        kwargs = build_request(
            model=model, messages=messages, tools=tools, settings=settings,
            base_url=self._base_url, reasoning_summary=self._reasoning_summary,
        )
        routing = session_headers(self._session_header, settings, self._session_id)
        if routing:
            kwargs["extra_headers"] = routing
        return kwargs

    def _create(self, client: Any, kwargs: dict[str, Any]) -> Any:
        return send(client.responses.create, kwargs)

    def complete(self, *, model: str, messages: list[dict[str, Any]],
                 tools: Optional[list[dict[str, Any]]] = None, **settings: Any) -> AssistantTurn:
        kwargs = self._request_kwargs(model=model, messages=messages, tools=tools, settings=settings)
        return turn_from_response(self._create(self._ensure_client(), kwargs))

    def stream(self, *, model: str, messages: list[dict[str, Any]],
               tools: Optional[list[dict[str, Any]]] = None, **settings: Any):
        kwargs = self._request_kwargs(model=model, messages=messages, tools=tools, settings=settings)
        kwargs["stream"] = True
        yield from read_stream(self._create(self._ensure_client(), kwargs))

    def capabilities(self, model: str) -> ModelCapabilities:
        return capabilities_for(model)
