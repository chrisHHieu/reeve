"""Per-built-in subagent model overrides — a tiny JSON store beside config.toml.

Settings ▸ Subagents lets a person point a BUILT-IN subagent (`general`, `explore`) at a
different model without writing a markdown override: an override file would replace the
whole shipped definition, while this store changes exactly the one field the built-in
allows. The value lives in ``<state-dir>/subagent_models.json`` (the same root as
``config.toml``/``secrets.json``, via ``secrets.state_dir``), not in TOML — the GUI never
rewrites ``config.toml``.

``resolve_agents`` merges the store over the built-ins it constructs and SKIPS any name
the config defines: a TOML table or a markdown file owns its agent's ``model`` outright,
and a globally-stored pin must not shadow it.

Format: ``{"general": "provider/model"}``. A missing or empty value means "inherit the
session's model" and is not stored. The file is written atomically with the SecretStore's
user-only protections, and a torn or foreign file degrades to "no overrides" — a broken
settings file must never cost a session.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

from harness.llm.providers.registry import descriptor_configured, get_descriptor, provider_descriptors, provider_names
from harness.llm.providers.variants import bare_model, provider_of
from harness.safety.secrets import state_dir, write_private_text

_FILE_NAME = "subagent_models.json"


def model_overrides_path() -> Path:
    """`<state dir>/subagent_models.json` — the one canonical location."""
    return state_dir() / _FILE_NAME


def load_model_overrides() -> dict[str, str]:
    """Every stored override as `{name: model}`, or `{}` when absent/unreadable.

    Non-string and blank values are dropped rather than surfaced: the file is edited by
    the server only, so anything else in it is foreign and must not reach a ruleset.
    """
    path = model_overrides_path()
    if not path.is_file():
        return {}
    try:
        data: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, str] = {}
    for name, model in data.items():
        if not isinstance(model, str):
            continue
        clean = model.strip()
        if clean:
            out[str(name)] = clean
    return out


def set_model_override(name: str, model: Optional[str]) -> dict[str, str]:
    """Store (or clear, with `model` None/empty) one built-in's model; returns the store.

    Clearing writes the file back without the key, so "inherit" round-trips as an absence
    instead of a sentinel the resolver would have to special-case.
    """
    store = load_model_overrides()
    clean = str(model or "").strip()
    if clean:
        store[str(name)] = clean
    else:
        store.pop(str(name), None)
    write_private_text(
        model_overrides_path(), json.dumps(store, indent=2, sort_keys=True) + "\n"
    )
    return store


def resolve_route(base: str, provider: str = "", model: str = "") -> tuple[str, str]:
    """`(route, problem)` for one child's LLM target with an optional per-call override.

    The delegation options (`provider` and `model` on the call) made concrete: `provider`
    and `model` are independent — either may be given alone. `provider` alone keeps the
    base model's id and moves it to another provider; a BARE `model` keeps the base
    provider; a `model` that is already `provider:model` is used as written, and naming a
    DIFFERENT provider alongside it is a problem rather than a silent preference. No
    override returns `base` untouched (which may be ""), so every existing caller is
    byte-identical.
    """
    base = str(base or "").strip()
    provider = str(provider or "").strip()
    model = str(model or "").strip()
    if not provider and not model:
        return base, ""
    if ":" in model:
        prefix = model.split(":", 1)[0]
        if provider and prefix != provider:
            return "", (
                f'model "{model}" already names provider "{prefix}" — drop `provider` or '
                "pass a bare model id"
            )
        return model, ""
    if provider and model:
        return f"{provider}:{model}", ""
    if provider:
        if not base:
            return "", "`provider` needs a base model to reroute — pass `model` too"
        return f"{provider}:{bare_model(base)}", ""
    if ":" in base:
        return f"{provider_of(base)}:{model}", ""
    return model, ""


def route_problem(provider: str, secrets: Any = None) -> str:
    """Why a NAMED provider cannot carry a child, or "" when it can.

    The two refusals the discovery tool renders: an UNREGISTERED id (a typo) names the
    registered providers; one with no usable credential names the configured ones. The
    MODEL id is deliberately not checked — catalog membership is advisory,
    and a custom gateway may accept an id no list carries.
    """
    name = str(provider or "").strip()
    if not name:
        return ""
    descriptor = get_descriptor(name)
    if descriptor is None:
        available = ", ".join(provider_names()) or "(none)"
        return f'LLM provider "{name}" is not registered; available providers: {available}'
    if not descriptor_configured(descriptor, provider_profile(secrets, name)):
        configured = [
            d.name
            for d in provider_descriptors()
            if descriptor_configured(d, provider_profile(secrets, d.name))
        ]
        return (
            f'LLM provider "{name}" is not configured for this session; configured '
            f'providers: {", ".join(configured) or "(none)"}'
        )
    return ""


def provider_profile(secrets: Any, name: str) -> dict[str, Any]:
    """One provider's stored profile, or {} — an unreadable store is "not configured"."""
    if secrets is None:
        return {}
    try:
        profile = secrets.get(f"provider:{name}")
    except Exception:  # noqa: BLE001 — a store that cannot answer answers nothing
        return {}
    return profile if isinstance(profile, dict) else {}


def models_for_provider(name: str, secrets: Any = None) -> dict[str, Any]:
    """One provider's advertised models, fetched with the session's credential.

    The same live fetch the provider's setup page uses (`live_models.list_provider_models`,
    cached and failure-tolerant). Imported here, at call time, so this module stays a leaf
    and a test can answer with a stub."""
    from harness.llm.providers.live_models import list_provider_models

    profile = provider_profile(secrets, name)
    descriptor = get_descriptor(name)
    env_key = getattr(descriptor, "env_key", None) if descriptor else None
    key = str(
        profile.get("api_key") or (os.environ.get(env_key) if env_key else "") or ""
    )
    return list_provider_models(
        name,
        api_key=key,
        base_url=profile.get("base_url"),
        fields=profile,
    )


__all__ = [
    "load_model_overrides",
    "model_overrides_path",
    "models_for_provider",
    "provider_profile",
    "resolve_route",
    "route_problem",
    "set_model_override",
]
