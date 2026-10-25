"""`ProviderRouter`: the one `ProviderClient` every engine holds, passing each call to its provider.

The model string says where it goes: `ollama:llama3.3` is Ollama's, a bare `gpt-5.5` the default
provider's (OpenAI). Every call already carries the whole string, so the engine keeps no routing
state of its own. The provider's prefix is taken off before the call goes on, since each SDK wants
its own model name. A provider's client is built from its stored profile the first time it is
needed and kept; `invalidate()` throws the kept ones away when a key or a URL changes, and the next
call, in any engine already open, builds from the new settings.
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Optional

from .base import ProviderClient
from .capabilities import capabilities_for
from .registry import build_provider_client, get_descriptor


class ProviderRouter(ProviderClient):
    def __init__(self, secrets: Any = None, *, default_provider: str = "openai", on_use: Any = None) -> None:
        self._secrets = secrets
        self._default = default_provider
        self._clients: dict[str, ProviderClient] = {}
        self._building = threading.Lock()
        # Told the provider's name each time a call is sent: Settings' "Last used" line. Whatever
        # it does, a model call never fails for it.
        self._on_use = on_use

    # -- the model string -------------------------------------------------------------------

    @staticmethod
    def _prefix(model: str) -> Optional[str]:
        """The provider named before the colon, or None. The name has to be a provider this app
        knows: in `qwen2.5-coder:32b` the colon starts a version tag."""
        head, colon, _rest = model.partition(":")
        return head if colon and get_descriptor(head) is not None else None

    def _split(self, model: str) -> tuple[str, str]:
        """`(provider, the model's own name)`."""
        prefix = self._prefix(model)
        return (self._default, model) if prefix is None else (prefix, model[len(prefix) + 1 :])

    def _provider_name(self, model: str) -> str:
        return self._split(model)[0]

    @staticmethod
    def _bare(model: str) -> str:
        prefix = ProviderRouter._prefix(model)
        return model[len(prefix) + 1 :] if prefix else model

    # -- clients ----------------------------------------------------------------------------

    def _client_for(self, model: str) -> ProviderClient:
        provider = self._provider_name(model)
        with self._building:
            if provider not in self._clients:
                profile = (self._secrets.get(f"provider:{provider}") or {}) if self._secrets is not None else {}
                self._clients[provider] = build_provider_client(provider, profile, self._secrets)
            return self._clients[provider]

    def invalidate(self, name: Optional[str] = None) -> None:
        """Forget the client of `name`, or every client; the next call builds from the settings as
        they are now."""
        with self._building:
            if name is None:
                self._clients.clear()
            else:
                self._clients.pop(name, None)

    def _used(self, model: str) -> None:
        if self._on_use is not None:
            try:
                self._on_use(self._provider_name(model))
            except Exception:
                pass  # bookkeeping; the call goes ahead

    # -- the calls --------------------------------------------------------------------------

    def complete(self, *, model: str, messages: list[dict[str, Any]], tools: Optional[list[dict[str, Any]]] = None, **settings: Any):
        self._used(model)
        return self._client_for(model).complete(model=self._bare(model), messages=messages, tools=tools, **settings)

    def stream(self, *, model: str, messages: list[dict[str, Any]], tools: Optional[list[dict[str, Any]]] = None, **settings: Any):
        self._used(model)
        return self._client_for(model).stream(model=self._bare(model), messages=messages, tools=tools, **settings)

    # -- what a route can do, asked of the client behind it ---------------------------------

    def _ask(self, model: str, question: Callable[[ProviderClient], Any], otherwise: Any) -> Any:
        """The routed client's answer, or `otherwise` when it cannot give one: a route that cannot
        be built now is asked again when it is used."""
        try:
            return question(self._client_for(model))
        except Exception:
            return otherwise

    def capabilities(self, model: str):
        return capabilities_for(model)

    def media_carry(self, model: str, mime: str) -> bool:
        return bool(self._ask(model, lambda client: client.media_carry(self._bare(model), mime), False))

    def image_limits(self, model: str) -> tuple[int, Optional[int]]:
        """The adapter's own image limits, so the engine and the server, which only hold the router,
        get what the adapter declares rather than a guess; a client that cannot say falls back to
        the table in `harness/llm/images.py`."""
        from ..images import limits_for

        try:
            return self._client_for(model).image_limits(model)
        except Exception:
            return limits_for(model)

    def reasoning_passback(self, model: str) -> Optional[str]:
        """The routed client's answer (`providers/reasoning.py`). While the router gave the base
        class's None, no thinking-mode route got its reasoning back on the wire: on 2026-10-10
        DeepSeek through OpenCode Go refused 7 requests for that (5 of them the first after a
        compaction, a cold prefix the gateway sent to another backend); the same requests with the
        reasoning echoed were answered 3 of 3."""
        return self._ask(model, lambda client: client.reasoning_passback(self._bare(model)), None)

    def cache_ttl_seconds(self, model: str) -> Optional[float]:
        """How long the routed client's prompt cache lives, when it manages one (Anthropic's
        5-minute and 1-hour breakpoints). Not knowing is None: the engine's prune of a cold cache
        must not take silence for cold."""
        getter = getattr(self._client_for(model), "cache_ttl_seconds", None)
        if not callable(getter):
            return None
        try:
            return getter(model)
        except Exception:
            return None
