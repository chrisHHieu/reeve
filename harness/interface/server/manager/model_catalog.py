"""ModelCatalogMixin: the models the composer's picker offers, and the person's edits to them."""

from __future__ import annotations

import time
from typing import Any

from harness.llm.providers import get_descriptor, provider_descriptors

_OLLAMA_DEFAULT_URL = "http://localhost:11434"
_OLLAMA_ALIVE_CACHE_SECONDS = 30


def _ollama_base(profile: dict[str, Any]) -> str:
    """The server root of a configured Ollama: its base URL without the OpenAI-style `/v1`."""
    base = (profile.get("base_url") or _OLLAMA_DEFAULT_URL).strip().rstrip("/")
    return base[: -len("/v1")] if base.endswith("/v1") else base


class ModelCatalogMixin:
    def _ollama_alive(self) -> bool:
        """Best-effort liveness of the local Ollama, cached for 30 seconds because settings are
        read on every GUI fetch and a probe must not run inline each time. Keyless is not the
        same as PRESENT: `ollama:*` entries render only when an Ollama actually answers, so a
        machine with none never shows phantom local models (a stray pasted string once got
        saved as a model id)."""
        now = time.monotonic()
        cached = getattr(self, "_ollama_alive_cache", None)
        if cached and now - cached[0] < _OLLAMA_ALIVE_CACHE_SECONDS:
            return cached[1]
        base = _ollama_base(self.secrets.get("provider:ollama") or {})
        try:
            import httpx

            alive = httpx.get(base + "/api/tags", timeout=0.8).status_code == 200
        except Exception:
            alive = False
        self._ollama_alive_cache = (now, alive)
        return alive

    def _ollama_models(self) -> list[str]:
        """The models pulled into the configured Ollama (its native `/api/tags`) as
        `ollama:<name>`, directly selectable. Empty when none is configured or reachable; it
        never raises."""
        profile = self.secrets.get("provider:ollama")
        if not profile:
            return []
        try:
            import httpx

            data = httpx.get(_ollama_base(profile) + "/api/tags", timeout=2.0).json()
            return [f"ollama:{model['name']}" for model in data.get("models", []) if model.get("name")]
        except Exception:
            return []

    def _curated_models(self) -> list[str]:
        """The models the composer's selector offers: every curated-matrix model (settings drops
        those whose provider has no key), plus custom ids the person added, minus matrix models
        they removed. There is deliberately no built-in seed list: a fresh install offers nothing
        until a provider key exists, and then exactly that provider's matrix models. The active
        default always stays selectable."""
        from harness.llm.providers.matrix import MATRIX

        added = self._prefs.get("models")
        added = added if isinstance(added, list) else []
        hidden = set(self._prefs.get("hidden_models") or [])
        models = [model for model in [*MATRIX, *added] if model not in hidden]
        return list(dict.fromkeys([self.model, *models]))

    def models_catalog(self) -> dict[str, Any]:
        """The model picker's data: every model of every CONFIGURED provider, with the catalogue
        facts (name, context, modalities, reasoning, cost, free) that the curated matrix cannot
        carry.

        This is the one path allowed to refresh models.dev: an explicit read by the person, never
        a session build. A provider with no key contributes no rows, since a model nobody can
        call is not a suggestion, and the curated and added ids are held to the same bar (an
        added id whose prefix is no known provider routes to the OpenAI default, per the router).
        """
        from harness.llm.providers.catalog import models_dev
        from harness.llm.providers.matrix import model_details, provider_model_ids

        try:
            models_dev().entries()  # fetched at most once per process, best effort
        except Exception:
            pass

        selected = set(self._curated_models())
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()

        def append(model_id: str, *, provider_title: str = "") -> None:
            if model_id in seen:
                return
            seen.add(model_id)
            details = model_details(model_id)
            title = provider_title or getattr(get_descriptor(details["provider"]), "title", "")
            details["provider_title"] = title or details["provider"]
            details["selected"] = model_id in selected
            rows.append(details)

        def usable(model_id: str) -> bool:
            provider = self._model_provider(model_id)
            return self._ollama_alive() if provider == "ollama" else self._provider_configured(provider)

        for descriptor in sorted(provider_descriptors(), key=lambda d: d.name):
            if descriptor.name == "ollama":
                # Keyless and live: its list exists only while the server answers.
                for full in self._ollama_models():
                    append(full, provider_title=descriptor.title)
                continue
            if not self._provider_configured(descriptor.name):
                continue
            for bare in provider_model_ids(descriptor.name):
                append(
                    bare if descriptor.name == "openai" else f"{descriptor.name}:{bare}",
                    provider_title=descriptor.title,
                )
        for model_id in self._curated_models():
            if usable(model_id):
                append(model_id)
        return {"models": rows, "default": self.model}

    def add_model(self, model: str) -> dict[str, Any]:
        """Add a model id (`gpt-4o`, `ollama:qwen2.5-coder:32b`) to the picker. A custom id is
        kept in the prefs. A matrix model that was removed is only unhidden, because storing it
        too would shadow later matrix updates."""
        from harness.llm.providers.matrix import MATRIX

        model = (model or "").strip()
        if not model:
            return {"ok": False, "error": "empty model"}
        hidden = [hidden_id for hidden_id in self._prefs.get("hidden_models") or [] if hidden_id != model]
        if hidden:
            self._prefs["hidden_models"] = hidden
        else:
            self._prefs.pop("hidden_models", None)
        models = self._prefs.get("models")
        models = models if isinstance(models, list) else []
        if model not in models and model not in MATRIX:
            models.append(model)
        self._prefs["models"] = models
        self._save_prefs()
        return {"ok": True, **self.get_settings()}

    def remove_model(self, model: str) -> dict[str, Any]:
        """Remove a model id from the picker. A custom id is dropped; a matrix model is hidden
        by id, because the matrix is derived and not stored and a plain drop would bring it back
        on the next read."""
        from harness.llm.providers.matrix import MATRIX

        models = self._prefs.get("models")
        models = models if isinstance(models, list) else []
        self._prefs["models"] = [kept for kept in models if kept != model]
        if model in MATRIX:
            hidden = self._prefs.get("hidden_models") or []
            if model not in hidden:
                self._prefs["hidden_models"] = [*hidden, model]
        self._save_prefs()
        return {"ok": True, **self.get_settings()}
