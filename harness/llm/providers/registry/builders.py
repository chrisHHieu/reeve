"""How each provider's client is built from its stored profile (a dict of the form's fields).

Keys are looked up when a call is made and not here, so a client can be built before a key
exists. Vendors reached through a compatible API read their key from their own profile or
environment variable and never from the OpenAI one, so a configured OpenAI key is never sent
to another vendor's endpoint.
"""

from __future__ import annotations

import os
from typing import Any, Callable, Optional

from ..base import ProviderClient
from ..bedrock import BedrockProvider
from ..chat import OpenAIProvider
from ..claude import AnthropicProvider
from ..gemini import GeminiProvider
from ..opencode_go import DEFAULT_BASE_URL as OPENCODE_GO_BASE_URL, OpenCodeGoProvider
from ..responses import OpenAIResponsesProvider
from ..vertex import VertexProvider
from .types import ProviderDescriptor, ProviderField, normalize_ollama_url

Builder = Callable[[dict[str, Any], Any], ProviderClient]


def _text(profile: Optional[dict[str, Any]], key: str) -> Optional[str]:
    """A profile value, trimmed; None when it is absent or blank."""
    return ((profile or {}).get(key) or "").strip() or None


def carries_media(build: Builder) -> Builder:
    """Mark a builder whose client can put audio and video on the wire.

    `modalities.input_modalities` reads the mark, so the model picker lists "audio" and "video"
    only where a file attached to that model reaches it. A catalogue describes the model; this
    says whether the route in this app can carry the part (Gemini's inline data, and the chat
    wire's `input_audio` and `video_url`). The Messages, Responses and Bedrock routes cannot.
    """
    build.carries_media = True
    return build


def build_openai(profile: dict[str, Any], secrets: Any) -> ProviderClient:
    # Stock OpenAI speaks the Responses API, the only wire that allows reasoning together
    # with tools on GPT-5.6 and later. A custom endpoint (Azure OpenAI, vLLM, any compatible
    # gateway) stays on Chat Completions, which is what those servers implement.
    base_url = _text(profile, "base_url")
    if base_url:
        return OpenAIProvider(secrets=secrets, base_url=base_url)
    return OpenAIResponsesProvider(secrets=secrets)


def build_codex(profile: dict[str, Any], secrets: Any) -> ProviderClient:
    # The credential is the OAuth token set in the provider's own profile, read and refreshed by
    # the token store at call time; there is no key.
    from ..codex import CodexProvider

    return CodexProvider(secrets=secrets)


def build_anthropic_subscription(profile: dict[str, Any], secrets: Any) -> ProviderClient:
    # OAuth subscription bearer tokens from the secret-store profile.
    from harness.config.config import load_config
    from ..claude.thinking import DEFAULT_THINKING_BUDGET
    from ..claude_subscription import AnthropicSubscriptionProvider

    try:
        thinking_budget = int(str((profile or {}).get("thinking_budget") or "").strip())
    except ValueError:
        thinking_budget = DEFAULT_THINKING_BUDGET
    return AnthropicSubscriptionProvider(
        secrets=secrets,
        thinking_budget=thinking_budget,
        cache_ttl=_text(profile, "cache_ttl"),
        context_editing=getattr(load_config(), "context_editing", None),
        base_url=_text(profile, "base_url"),
    )


def build_anthropic(profile: dict[str, Any], secrets: Any) -> ProviderClient:
    # Hidden profile overrides:
    #   thinking_budget: absent or invalid means the default (on), an explicit 0 turns it off.
    #   cache_ttl: "1h" moves both cache breakpoints off the 5-minute default; anything else
    #       keeps it.
    # `context_editing` comes from the global `[context_editing]` table and is on by default,
    # because the native endpoint is the one wire that serves the beta end to end. A client
    # injected by Vertex, Bedrock or OpenCode Go stays out of it.
    from harness.config.config import load_config

    from ..claude.thinking import DEFAULT_THINKING_BUDGET

    try:
        thinking_budget = int(str((profile or {}).get("thinking_budget") or "").strip())
    except ValueError:
        thinking_budget = DEFAULT_THINKING_BUDGET
    return AnthropicProvider(
        api_key=_text(profile, "api_key"),
        secrets=secrets,
        thinking_budget=thinking_budget,
        cache_ttl=_text(profile, "cache_ttl"),
        # No workspace is given, so this reads the global layer, the only one allowed to set it.
        context_editing=getattr(load_config(), "context_editing", None),
        base_url=_text(profile, "base_url"),
    )


@carries_media
def build_gemini(profile: dict[str, Any], secrets: Any) -> ProviderClient:
    return GeminiProvider(
        api_key=_text(profile, "api_key"), secrets=secrets, base_url=_text(profile, "base_url")
    )


def build_bedrock(profile: dict[str, Any], secrets: Any) -> ProviderClient:
    # boto3 and AnthropicBedrock resolve the credentials at call time: explicit keys, then a
    # named profile, then the ambient chain (environment, ~/.aws default, an instance role).
    return BedrockProvider(
        region=_text(profile, "region"),
        auth_method=_text(profile, "auth_method"),
        bedrock_api_key=_text(profile, "bedrock_api_key"),
        profile_name=_text(profile, "aws_profile"),
        access_key_id=_text(profile, "aws_access_key_id"),
        secret_access_key=_text(profile, "aws_secret_access_key"),
        session_token=_text(profile, "aws_session_token"),
    )


@carries_media  # the Gemini family only; `modalities` narrows it by the model's family segment
def build_vertex(profile: dict[str, Any], secrets: Any) -> ProviderClient:
    return VertexProvider(
        project=_text(profile, "project"),
        location=_text(profile, "location"),
        auth_method=_text(profile, "auth_method"),
        service_account_json=_text(profile, "service_account_json"),
        api_key=_text(profile, "vertex_api_key"),
    )


def build_ollama(profile: dict[str, Any], secrets: Any) -> ProviderClient:
    # Ollama's endpoint ignores the key, but the SDK insists on a non-empty one, so a
    # placeholder is passed.
    return OpenAIProvider(api_key="ollama", base_url=normalize_ollama_url((profile or {}).get("base_url")))


@carries_media  # the models on its chat wire; `modalities` narrows it by each model's wire
def build_opencode_go(profile: dict[str, Any], secrets: Any) -> ProviderClient:
    """OpenCode Go's gateway refuses a request without an `x-opencode-session` header ("400
    MissingSessionID") and serves its models over four wires. `OpenCodeGoProvider` attaches
    the header on every wire and sends each model through its own transport, at its own endpoint."""
    api_key = _text(profile, "api_key") or os.environ.get("OPENCODE_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("No OpenCode Go API key configured — add it in Settings ▸ Models.")
    return OpenCodeGoProvider(
        api_key=api_key,
        base_url=_text(profile, "base_url") or OPENCODE_GO_BASE_URL,
        headers={"User-Agent": "reeve", "x-opencode-client": "reeve"},
    )


def _vendor_key(profile: dict[str, Any], vendor: str, env_key: Optional[str]) -> str:
    key = _text(profile, "api_key") or (os.environ.get(env_key, "").strip() if env_key else "")
    if not key:
        # Built only when one of the vendor's models is chosen, so a missing key fails here
        # with the vendor's name in the message.
        raise RuntimeError(f"No {vendor} API key configured — add it in Settings ▸ Models.")
    return key


def chat_compatible(vendor: str, default_base_url: str, env_key: Optional[str] = None) -> Builder:
    """A builder for a vendor reached through its OpenAI-compatible Chat Completions API (Z AI,
    DeepSeek, Kimi, MiniMax, Qwen, xAI, Mistral)."""

    def build(profile: dict[str, Any], secrets: Any) -> ProviderClient:
        return OpenAIProvider(
            api_key=_vendor_key(profile, vendor, env_key),
            base_url=_text(profile, "base_url") or default_base_url,
        )

    return carries_media(build)


def responses_compatible(
    vendor: str, default_base_url: str, env_key: Optional[str] = None, *, reasoning_summary: bool = True
) -> Builder:
    """A builder for a vendor that implements the OpenAI Responses API."""

    def build(profile: dict[str, Any], secrets: Any) -> ProviderClient:
        return OpenAIResponsesProvider(
            api_key=_vendor_key(profile, vendor, env_key),
            base_url=_text(profile, "base_url") or default_base_url,
            reasoning_summary=reasoning_summary,
        )

    return build


def _endpoint_field(base_url: str, help: str) -> ProviderField:
    return ProviderField("base_url", "Endpoint", required=False, default=base_url, placeholder=base_url, help=help)


def chat_vendor(
    name: str,
    title: str,
    *,
    base_url: str,
    env_key: str,
    endpoint_help: str = "",
    vendor: str = "",
) -> ProviderDescriptor:
    """The descriptor of an OpenAI-compatible vendor: a key and a prefilled, editable endpoint.
    `vendor` replaces the company name taken from `title` when they differ ("Zhipu AI Coding
    Plan" is a plan of Zhipu AI)."""
    vendor = vendor or title.split(" (")[0]
    return ProviderDescriptor(
        name=name,
        title=title,
        needs_key=True,
        fields=[
            ProviderField("api_key", f"{vendor} API key", secret=True),
            _endpoint_field(
                base_url,
                endpoint_help
                or f"Prefilled with {vendor}'s official endpoint; edit only for a regional or proxy variant.",
            ),
        ],
        build=chat_compatible(vendor, base_url, env_key),
        env_key=env_key,
        blurb=f"Uses {vendor}'s OpenAI-compatible API — the endpoint is prefilled, just add your key.",
    )


def responses_vendor(
    name: str,
    title: str,
    *,
    base_url: str,
    env_key: str,
    endpoint_help: str = "",
    reasoning_summary: bool = True,
) -> ProviderDescriptor:
    """The descriptor of a vendor that exposes the OpenAI Responses API."""
    return ProviderDescriptor(
        name=name,
        title=title,
        needs_key=True,
        fields=[
            ProviderField("api_key", f"{title} API key", secret=True),
            _endpoint_field(base_url, endpoint_help or f"Prefilled with {title}'s official Responses endpoint."),
        ],
        build=responses_compatible(title, base_url, env_key, reasoning_summary=reasoning_summary),
        env_key=env_key,
        blurb=f"Uses {title}'s OpenAI-compatible Responses API — the endpoint is prefilled, just add your key.",
    )
