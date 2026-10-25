"""ProvidersMixin: the model providers as Settings configures them."""

from __future__ import annotations

import os
import time
from datetime import date
from typing import Any, Optional

from harness.llm.providers import descriptor_configured, get_descriptor, provider_descriptors, verify_provider_key

_USE_STAMP_EVERY_SECONDS = 60


class ProvidersMixin:
    def provider_complete(self, model, messages, tools=None):
        return self.provider.complete(model=model, messages=messages, tools=tools)

    def _refresh_provider(self, name: Optional[str] = None) -> None:
        """Drop the router's cached client or clients, so the next turn rebuilds with fresh
        config. It does nothing for an injected provider that is not a router (tests)."""
        invalidate = getattr(self.provider, "invalidate", None)
        if callable(invalidate):
            invalidate(name)

    def get_providers(self) -> list[dict[str, Any]]:
        """Each provider's descriptor and status for the Settings UI. Secret values never leave;
        non-secret field values (an Ollama base URL, say) do, so the form can prefill."""
        rows: list[dict[str, Any]] = []
        for descriptor in provider_descriptors():
            profile = self.secrets.get(f"provider:{descriptor.name}") or {}
            row = {
                **descriptor.to_dict(),
                "configured": descriptor_configured(descriptor, profile),
                "values": {
                    field.key: profile.get(field.key)
                    for field in descriptor.fields
                    if not field.secret and profile.get(field.key)
                },
                "suggested_models": self._suggested_models(descriptor.name),
                # Key hygiene for the pane: when the key was saved (stamped by `set_provider`)
                # and when the provider last served a completion (stamped by the router's
                # `on_use` hook). Both are absent for a key that comes only from the environment.
                "key_set_at": profile.get("key_set_at"),
                "last_used_at": (self._prefs.get("provider_last_used") or {}).get(descriptor.name),
            }
            if descriptor.auth == "oauth":
                # Sign-in state instead of key state. The token values never leave the secret
                # store, and `signed_in` means the browser flow stored tokens.
                row["signed_in"] = bool(profile.get("tokens"))
                row["account"] = profile.get("account_email") or profile.get("account_id")
                if descriptor.name == "openai-codex":
                    row["authorizing"] = self._codex_authorizing
                    row["last_error"] = self._codex_error
                elif descriptor.name == "anthropic-subscription":
                    row["authorizing"] = getattr(self, "_anthropic_subscription_authorizing", False)
                    row["last_error"] = getattr(self, "_anthropic_subscription_error", None)
            rows.append(row)
        return rows

    def _note_provider_use(self, name: str) -> None:
        """The router's `on_use` hook: remember when a provider last served a completion. It
        fires on every model call, from engine threads, so the write is throttled to once per
        provider per minute; the prefs file is no place for a write per unit of work."""
        now = time.time()
        used = self._prefs.setdefault("provider_last_used", {})
        if now - float(used.get(name) or 0) < _USE_STAMP_EVERY_SECONDS:
            return
        used[name] = now
        try:
            self._save_prefs()
        except OSError:
            pass

    def provider_models(self, name: str, refresh: bool = False) -> dict[str, Any]:
        """The live model list for one provider's setup page (`GET /v1/providers/{name}/models`).

        It is fetched from the provider's own API with the stored credential. When that cannot
        answer, the curated suggestions come back with `source: "curated"` and the reason, so
        the page always has rows. It never raises, and does blocking HTTP, so routes call it off
        the loop."""
        from harness.llm.providers.live_models import list_provider_models

        profile = dict(self.secrets.get(f"provider:{name}") or {})
        descriptor = get_descriptor(name)
        env_key = getattr(descriptor, "env_key", None) if descriptor else None
        key = str(profile.get("api_key") or (os.environ.get(env_key) if env_key else "") or "")
        result = list_provider_models(
            name, api_key=key, base_url=profile.get("base_url"), fields=profile, refresh=refresh
        )
        if result.get("ok"):
            return result
        return {
            "ok": False,
            "models": self._suggested_models(name),
            "source": "curated",
            "error": result.get("error") or "couldn't list the provider's models",
        }

    def _suggested_models(self, name: str) -> list[str]:
        """Bare model names for the "add model" form, per provider. Ollama reports what it has
        pulled (best effort); everyone else reads from the curated matrix and then the
        models.dev catalogue (from disk only on this path, since a Settings row must not wait on
        a download). There is no per-provider table in the source: a vendor's list is whatever
        it publishes, and a copy would drift."""
        if name == "ollama":
            return [model.split(":", 1)[-1] for model in self._ollama_models()]
        from harness.llm.providers.matrix import provider_model_ids

        return provider_model_ids(name)

    def set_provider(self, name: str, fields: Optional[dict[str, Any]]) -> dict[str, Any]:
        """Store a provider's config in its `provider:<name>` profile and rebuild its cached
        client. The given fields merge into whatever profile exists."""
        descriptor = get_descriptor(name)
        if descriptor is None:
            return {"ok": False, "error": f"unknown provider: {name}"}
        fields = fields or {}
        profile = dict(self.secrets.get(f"provider:{name}") or {})
        for field in descriptor.fields:
            if field.key not in fields:
                continue
            value = fields.get(field.key)
            if isinstance(value, str):
                value = value.strip()
            if value:
                profile[field.key] = value
            elif not field.required:
                profile.pop(field.key, None)
        missing = [field.label for field in descriptor.fields if field.required and not profile.get(field.key)]
        if missing:
            return {"ok": False, "error": "missing: " + ", ".join(missing)}
        # A key pasted again stamps its save date, so Settings can say "key added <date>" and
        # stale keys show. A save that only changes the endpoint keeps the original stamp.
        if isinstance(fields.get("api_key"), str) and fields["api_key"].strip():
            profile["key_set_at"] = date.today().isoformat()
        self.secrets.put(f"provider:{name}", profile)
        self._refresh_provider(name)
        self._forget_live_models()
        # The first known model is added so the composer has something from this provider at
        # once, and it becomes the default when the current default's provider is not configured.
        # The list is what the provider's own page will show (live when reachable, curated
        # otherwise), so there is no "recommended" field to drift.
        first = next((model for model in self._suggested_models(name) if model), "")
        if first:
            added = first if name == "openai" else f"{name}:{first}"
            self.add_model(added)
            if not self._provider_configured(self._model_provider(self.model)):
                self.set_default_model(added)
        return {"ok": True, "provider": name}

    def remove_provider(self, name: str) -> dict[str, Any]:
        """Forget a provider's stored config (Settings ▸ Models ▸ "Remove key"). The whole
        profile goes, key and endpoint and save date, so the provider reads as never configured.
        Curated models stay and gray out until a new key."""
        if get_descriptor(name) is None:
            return {"ok": False, "error": f"unknown provider: {name}"}
        self.secrets.delete(f"provider:{name}")
        self._refresh_provider(name)
        self._forget_live_models()
        return {"ok": True, "provider": name}

    @staticmethod
    def _forget_live_models() -> None:
        """A new key or endpoint invalidates the live model list."""
        from harness.llm.providers.live_models import clear_cache

        clear_cache()

    def verify_provider(self, name: str, fields: Optional[dict[str, Any]]) -> dict[str, Any]:
        """Test a provider's credentials with a live read-only call, without saving them, so
        onboarding can offer a "Test" button. A field the form left blank falls back to the
        stored or environment value, which is how an already configured provider is tested."""
        descriptor = get_descriptor(name)
        if descriptor is None:
            return {"ok": False, "error": f"unknown provider: {name}"}
        if descriptor.auth == "oauth":
            if name == "openai-codex":
                from harness.llm.providers.codex.verify import verify
                return verify(self.secrets)
            if name == "anthropic-subscription":
                from harness.llm.providers.claude_subscription.verify import verify
                return verify(self.secrets)
            return {"ok": False, "error": f"{descriptor.title} does not support verification."}
        fields = fields or {}
        profile = self.secrets.get(f"provider:{name}") or {}
        merged: dict[str, Any] = {}
        for field in descriptor.fields:
            value = fields.get(field.key) or profile.get(field.key) or ""
            if isinstance(value, str):
                value = value.strip()
            if value:
                merged[field.key] = value
        api_key = merged.get("api_key", "")
        if not api_key and descriptor.env_key:
            api_key = os.environ.get(descriptor.env_key, "").strip()
        has_key_field = any(field.key == "api_key" for field in descriptor.fields)
        if descriptor.needs_key and has_key_field and not api_key:
            return {"ok": False, "error": "Enter an API key to test."}
        if descriptor.needs_key and not has_key_field:
            # A multi-field cloud provider (Bedrock): the required fields must be present, while
            # the credentials themselves may be ambient (~/.aws, the environment) and are
            # checked by the call.
            missing = [field.label for field in descriptor.fields if field.required and not merged.get(field.key)]
            if missing:
                return {"ok": False, "error": "missing: " + ", ".join(missing)}
        return verify_provider_key(name, api_key=api_key, base_url=merged.get("base_url", ""), fields=merged)

    def _model_provider(self, model: str) -> str:
        """The provider a model string routes to: a known `prefix:`, else the OpenAI default."""
        if ":" in (model or ""):
            prefix = model.split(":", 1)[0]
            if get_descriptor(prefix) is not None:
                return prefix
        return "openai"

    def _provider_configured(self, name: str) -> bool:
        descriptor = get_descriptor(name)
        if descriptor is None:
            return False
        return descriptor_configured(descriptor, self.secrets.get(f"provider:{name}") or {})
