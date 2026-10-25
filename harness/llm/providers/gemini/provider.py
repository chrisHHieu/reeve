"""Google's Gemini API, through the `google-genai` SDK.

The loop keeps its history in OpenAI-shaped messages. This provider converts a request into
Gemini's `generateContent` form (`convert.py`, `schema.py`), sends it, and reads the reply back
into an `AssistantTurn` (`reply.py`).
"""

from __future__ import annotations

import re
import uuid
from typing import Any, Optional

from ..base import AssistantTurn, ModelCapabilities, ProviderClient
from ..capabilities import capabilities_for
from ..endpoints import normalize_base_url
from ..turns import session_headers
from ..variants import budget_for
from .convert import convert_messages
from .credentials import resolve_api_key, resolve_base_url
from .reply import read_stream, turn_from_response
from .schema import convert_tools

#: The config keys passed through. Anything else the loop carries (a frequency penalty, say)
#: is dropped.
_ACCEPTED_SETTINGS = {"temperature", "top_p", "top_k", "max_output_tokens", "stop_sequences"}


def _thinking_config(model: str, effort: Any) -> Optional[dict[str, Any]]:
    """How much the model thinks, and whether it reports it.

    Thinking models (2.5 and 3.x, which is every id we list) think by default, and thought
    tokens are billed as output. A call that asks for no thinking at all (a session title)
    gets it switched fully off, because on a tight `max_output_tokens` the
    thoughts silently eat the answer: a session title once came back as one cut-off word. A
    level the person chose maps to a budget (`variants.py`); any other effort value has no
    meaning here and is ignored. Otherwise thought summaries are requested so the app can show
    them; the reader keeps them out of the answer text.
    """
    if not model.startswith("gemini-"):
        return None
    budget = budget_for("gemini", effort if isinstance(effort, str) else None)
    if budget == 0:
        return {"include_thoughts": False, "thinking_budget": 0}
    if budget:
        return {"include_thoughts": True, "thinking_budget": budget}
    return {"include_thoughts": True}


class GeminiProvider(ProviderClient):
    def __init__(
        self,
        client: Any = None,
        *,
        default_model: str = "gemini-2.5-flash",
        api_key: Optional[str] = None,
        secrets: Any = None,
        base_url: Optional[str] = None,
        default_headers: Optional[dict[str, str]] = None,
        session_header: Optional[str] = None,
    ):
        """The SDK client is made on first use, so a loop can be assembled before any key
        exists; the key is looked up then (argument, environment, secret store). Tests, and
        Vertex with its own project-scoped client, pass `client`. `base_url` is a proxy that
        speaks the Generative Language API, and is ignored when a client is passed.
        `session_header` is a gateway's routing header, valued with the session id per request."""
        self._client = client
        self._api_key = api_key
        self._secrets = secrets
        self.default_model = default_model
        self._base_url = base_url
        self._default_headers = dict(default_headers or {})
        self._session_header = session_header
        self._session_id = uuid.uuid4().hex

    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        from google import genai

        key = self._api_key or resolve_api_key(self._secrets)
        if not key:
            raise RuntimeError(
                "No Gemini API key configured. Set GEMINI_API_KEY in the environment, "
                "or add your key in Manage → Configure Models."
            )
        options: dict[str, Any] = {}
        base_url = normalize_base_url(self._base_url) or resolve_base_url(self._secrets)
        if base_url or self._default_headers:  # no http_options without one: an empty set would replace the SDK's own
            from google.genai import types

            http_kwargs: dict[str, Any] = {}
            if base_url:
                http_kwargs["base_url"] = base_url
                if re.search(r"/v1(?:beta)?/?$", base_url):
                    http_kwargs["base_url_resource_scope"] = types.ResourceScope.COLLECTION
            if self._default_headers:
                http_kwargs["headers"] = dict(self._default_headers)
            options["http_options"] = types.HttpOptions(**http_kwargs)
        self._client = genai.Client(api_key=key, **options)
        return self._client

    def _request_kwargs(self, *, model: str, messages: list[dict[str, Any]],
                        tools: Optional[list[dict[str, Any]]], settings: dict[str, Any]) -> dict[str, Any]:
        system, contents = convert_messages(messages)
        if "max_tokens" in settings and "max_output_tokens" not in settings:
            settings["max_output_tokens"] = settings["max_tokens"]
        if "stop" in settings and "stop_sequences" not in settings:
            stop = settings["stop"]
            settings["stop_sequences"] = [stop] if isinstance(stop, str) else list(stop)

        config: dict[str, Any] = {k: v for k, v in settings.items() if k in _ACCEPTED_SETTINGS}
        thinking = _thinking_config(model, settings.get("reasoning_effort"))
        if thinking is not None:
            config["thinking_config"] = thinking
        if system:
            config["system_instruction"] = system
        declarations = convert_tools(tools) if tools else []
        if declarations:
            config["tools"] = declarations
        routing = session_headers(self._session_header, settings, self._session_id)
        if routing:
            config["http_options"] = {"headers": routing}  # merged over the client's own headers
        return {"model": model, "contents": contents, "config": config}

    def complete(self, *, model: str, messages: list[dict[str, Any]],
                 tools: Optional[list[dict[str, Any]]] = None, **settings: Any) -> AssistantTurn:
        kwargs = self._request_kwargs(model=model, messages=messages, tools=tools, settings=settings)
        response = self._ensure_client().models.generate_content(**kwargs)
        return turn_from_response(response)

    def stream(self, *, model: str, messages: list[dict[str, Any]],
               tools: Optional[list[dict[str, Any]]] = None, **settings: Any):
        kwargs = self._request_kwargs(model=model, messages=messages, tools=tools, settings=settings)
        client = self._ensure_client()
        yield from read_stream(client.models.generate_content_stream(**kwargs))

    def capabilities(self, model: str) -> ModelCapabilities:
        return capabilities_for(model)

    def media_carry(self, model: str, mime: str) -> bool:
        return mime.startswith(("audio/", "video/"))  # inline data takes the MIME type as given
