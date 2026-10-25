"""AnthropicSubscriptionAccountMixin: signing in to the Claude subscription provider."""

from __future__ import annotations

import asyncio
from typing import Any


class AnthropicSubscriptionAccountMixin:
    def begin_anthropic_subscription_signin(self) -> None:
        """Flag `authorizing` before background sign-in starts."""
        self._anthropic_subscription_authorizing = True
        self._anthropic_subscription_error = None

    async def anthropic_subscription_signin(self) -> dict[str, Any]:
        """Run the interactive browser sign-in for Claude subscription and store tokens."""
        from harness.llm.providers.claude_subscription import signin

        self._anthropic_subscription_authorizing = True
        self._anthropic_subscription_error = None
        try:
            result = await signin.sign_in(self.secrets)
        except Exception as exc:
            self._anthropic_subscription_error = str(exc)
            return {"ok": False, "error": str(exc)}
        finally:
            self._anthropic_subscription_authorizing = False

        self._refresh_provider("anthropic-subscription")
        # Add default model if not configured
        default_model = "anthropic-subscription:claude-sonnet-4-6"
        self.add_model(default_model)
        if not self._provider_configured(self._model_provider(self.model)):
            self.set_default_model(default_model)
        return result

    def anthropic_subscription_status(self) -> dict[str, Any]:
        from harness.llm.providers.claude_subscription import signin
        from harness.llm.providers.claude_subscription.tokens import AnthropicSubscriptionTokenStore

        store = AnthropicSubscriptionTokenStore(self.secrets)
        return {
            "signed_in": store.signed_in(),
            "account": store.account_label(),
            "authorizing": getattr(self, "_anthropic_subscription_authorizing", False),
            "last_error": getattr(self, "_anthropic_subscription_error", None),
            "authorize_url": signin.last_authorize_url,
        }

    def anthropic_subscription_signout(self) -> dict[str, Any]:
        from harness.llm.providers.claude_subscription.tokens import AnthropicSubscriptionTokenStore

        had_tokens = AnthropicSubscriptionTokenStore(self.secrets).clear()
        self._anthropic_subscription_error = None
        self._refresh_provider("anthropic-subscription")
        return {"ok": True, "had_tokens": had_tokens}
