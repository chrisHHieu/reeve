"""CodexAccountMixin: signing in to the ChatGPT-subscription provider, which has no API key."""

from __future__ import annotations

import asyncio
from typing import Any


class CodexAccountMixin:
    def begin_codex_signin(self) -> None:
        """Flag `authorizing` BEFORE the background sign-in starts, so the GUI's first poll after
        the button press already shows it (the reasoning of `begin_mcp_connect`)."""
        self._codex_authorizing = True
        self._codex_error = None

    async def codex_signin(self) -> dict[str, Any]:
        """Run the interactive browser sign-in and store the tokens. It is long-running, since
        the person finishes it in the browser, so routes run it as a background task and the GUI
        polls `codex_status` for the flip."""
        from harness.llm.providers.codex import signin

        self._codex_authorizing = True
        self._codex_error = None
        try:
            result = await signin.sign_in(self.secrets)
        except Exception as exc:
            self._codex_error = str(exc)
            return {"ok": False, "error": str(exc)}
        finally:
            self._codex_authorizing = False
        self._refresh_provider("openai-codex")
        # The first model the Codex catalog actually reports is added. The sign-in is not an API
        # key, so the list comes live from the catalog; if it cannot be read, nothing is added
        # and the Settings page still lists whatever it can fetch.
        live = await asyncio.to_thread(self.provider_models, "openai-codex")
        first = next((model for model in (live.get("models") or []) if model), "")
        if first:
            added = f"openai-codex:{first}"
            self.add_model(added)
            if not self._provider_configured(self._model_provider(self.model)):
                self.set_default_model(added)
        return result

    def codex_status(self) -> dict[str, Any]:
        from harness.llm.providers.codex import signin
        from harness.llm.providers.codex.tokens import CodexTokenStore

        store = CodexTokenStore(self.secrets)
        return {
            "signed_in": store.signed_in(),
            "account": store.account_label(),
            "authorizing": self._codex_authorizing,
            "last_error": self._codex_error,
            "authorize_url": signin.last_authorize_url,
        }

    def codex_signout(self) -> dict[str, Any]:
        from harness.llm.providers.codex.tokens import CodexTokenStore

        had_tokens = CodexTokenStore(self.secrets).clear()
        self._codex_error = None
        self._refresh_provider("openai-codex")
        return {"ok": True, "had_tokens": had_tokens}
