"""The providers Reeve can talk to: their descriptors, how their clients are built, and the
Test button that checks their credentials."""

from __future__ import annotations

import os
from typing import Any, Optional

from ..base import ProviderClient
from .descriptors import BY_NAME, DESCRIPTORS
from .types import DEFAULT_OLLAMA_URL, ProviderDescriptor, ProviderField, normalize_ollama_url
from .verify import verify_provider_key

__all__ = [
    "DEFAULT_OLLAMA_URL",
    "ProviderDescriptor",
    "ProviderField",
    "build_provider_client",
    "descriptor_configured",
    "detect_provider",
    "get_descriptor",
    "normalize_ollama_url",
    "provider_descriptors",
    "provider_names",
    "verify_provider_key",
]


def provider_descriptors() -> list[ProviderDescriptor]:
    return list(DESCRIPTORS)


def provider_names() -> list[str]:
    return [descriptor.name for descriptor in DESCRIPTORS]


def get_descriptor(name: str) -> Optional[ProviderDescriptor]:
    return BY_NAME.get(name)


def build_provider_client(name: str, profile: dict[str, Any], secrets: Any) -> ProviderClient:
    """The client for `name`, built from its stored profile. An unknown name gets the OpenAI one."""
    return (BY_NAME.get(name) or BY_NAME["openai"]).build(profile or {}, secrets)


def descriptor_configured(descriptor: ProviderDescriptor, profile: dict[str, Any]) -> bool:
    """Whether a provider can be used with this stored profile.

    A provider with one key needs a stored key or its environment variable. A cloud provider
    with several fields (Bedrock has no `api_key`) needs every required field, because its real
    credentials may be ambient (`~/.aws`, ADC).
    """
    profile = profile or {}
    from_env = bool(descriptor.env_key and os.environ.get(descriptor.env_key))
    if descriptor.auth == "oauth":
        # Stored tokens mean signed in; they live in the same profile.
        return bool(profile.get("tokens")) or from_env
    if not descriptor.needs_key:
        return True  # keyless, like Ollama: usable as it is
    if any(field.key == "api_key" for field in descriptor.fields):
        return bool(profile.get("api_key")) or from_env
    return all(profile.get(field.key) for field in descriptor.fields if field.required)


def detect_provider(api_key: str) -> Optional[str]:
    """A guess at the provider from the shape of its key, for onboarding's auto-detect. It
    returns a known provider name or None, and agrees with the app's own detection."""
    key = (api_key or "").strip()
    if key.startswith("sk-ant-"):
        return "anthropic"
    if key.startswith("sk-or-"):
        return "openrouter"
    if key.startswith("AIza"):
        return "gemini"
    if key.startswith(("sk-", "sk_")):
        return "openai"
    return None
