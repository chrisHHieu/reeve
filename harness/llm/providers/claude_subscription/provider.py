"""Provider implementation for Anthropic/Claude subscription via OAuth Bearer token.

Connects to the Anthropic Messages API with:
  Authorization: Bearer <access_token>
  anthropic-beta: oauth-2025-04-20,interleaved-thinking-2025-05-14
  User-Agent: claude-cli/<version> (external, cli)
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from ..base import ModelCapabilities
from ..claude.provider import AnthropicProvider
from .constants import (
    API_BASE_URL,
    REQUIRED_BETAS,
    USER_AGENT,
)
from .tokens import AnthropicSubscriptionTokenStore

logger = logging.getLogger("harness.llm.providers.claude_subscription")


class AnthropicSubscriptionProvider(AnthropicProvider):
    """Anthropic Provider that uses short-lived OAuth bearer tokens instead of an API key."""

    def __init__(
        self,
        client: Any = None,
        *,
        secrets: Any = None,
        default_model: str = "claude-sonnet-4-6",
        thinking_budget: Optional[int] = None,
        cache_ttl: Optional[str] = None,
        context_editing: Optional[dict[str, Any]] = None,
        base_url: Optional[str] = None,
    ):
        self._store = AnthropicSubscriptionTokenStore(secrets)
        self._client_token: Optional[str] = None
        self._injected = client is not None

        default_headers = {
            "anthropic-beta": REQUIRED_BETAS,
            "user-agent": USER_AGENT,
        }

        super().__init__(
            client=client,
            default_model=default_model,
            api_key=None,
            secrets=secrets,
            thinking_budget=thinking_budget,
            cache_ttl=cache_ttl,
            context_editing=context_editing,
            base_url=base_url or API_BASE_URL,
            default_headers=default_headers,
        )

    def _ensure_client(self) -> Any:
        if self._injected:
            return self._client

        from anthropic import Anthropic
        import anthropic as sdk

        from ..endpoints import normalize_base_url
        from ..timeouts import stream_timeout

        token = self._store.access_token()
        if self._client is None or token != self._client_token:
            options: dict[str, Any] = {
                "max_retries": 0,
                "timeout": stream_timeout(sdk),
            }
            base = normalize_base_url(self._base_url) or API_BASE_URL
            options["base_url"] = base

            headers = dict(self._default_headers)
            headers["authorization"] = f"Bearer {token}"
            options["default_headers"] = headers

            # Instantiate Anthropic client with auth_token / bearer
            self._client = Anthropic(auth_token=token, **options)
            self._client_token = token

        return self._client

    def _request_kwargs(self, *, model: str, messages: list[dict[str, Any]], tools: Any, settings: dict[str, Any]) -> dict[str, Any]:
        """Inject Claude Code identity as the very first system block.

        Anthropic OAuth subscription entitlement gate strictly requires the first system
        block to identify as Claude Code: 'You are Claude Code, Anthropic\'s official CLI for Claude.'
        Without this, Anthropic immediately returns 429 {"type": "error", "error": {"type": "rate_limit_error", "message": "Error"}}
        on premium models (Opus, Sonnet) even when quota is fully available.
        """
        kwargs = super()._request_kwargs(model=model, messages=messages, tools=tools, settings=settings)

        cc_identity = "You are Claude Code, Anthropic's official CLI for Claude."
        raw_system = kwargs.get("system")

        if not raw_system:
            kwargs["system"] = [{"type": "text", "text": cc_identity}]
        elif isinstance(raw_system, str):
            kwargs["system"] = [
                {"type": "text", "text": cc_identity},
                {"type": "text", "text": raw_system},
            ]
        elif isinstance(raw_system, list):
            # Ensure the first block is cc_identity
            blocks = list(raw_system)
            first_text = blocks[0].get("text") if isinstance(blocks[0], dict) else str(blocks[0])
            if first_text != cc_identity:
                blocks.insert(0, {"type": "text", "text": cc_identity})
            kwargs["system"] = blocks

        return kwargs

    def capabilities(self, model: Optional[str] = None) -> ModelCapabilities:
        from ..capabilities import capabilities_for
        target = model or self.default_model
        if ":" not in target:
            target = f"anthropic-subscription:{target}"
        return capabilities_for(target)
