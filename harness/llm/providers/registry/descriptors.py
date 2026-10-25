"""The providers Reeve can talk to, in the order Settings lists them."""

from __future__ import annotations

from ..opencode_go import DEFAULT_BASE_URL as OPENCODE_GO_BASE_URL
from .builders import (
    build_anthropic,
    build_anthropic_subscription,
    build_bedrock,
    build_codex,
    build_gemini,
    build_ollama,
    build_openai,
    build_opencode_go,
    build_vertex,
    chat_vendor,
    responses_vendor,
)
from .types import DEFAULT_OLLAMA_URL, ProviderDescriptor, ProviderField


def _api_key(label: str, placeholder: str = "") -> ProviderField:
    return ProviderField("api_key", label, secret=True, placeholder=placeholder)


def _custom_endpoint(placeholder: str, help: str) -> ProviderField:
    return ProviderField(
        "base_url", "Custom endpoint (optional)", required=False, placeholder=placeholder, help=help
    )


def _optional(key: str, label: str, *, secret: bool = False, **more) -> ProviderField:
    return ProviderField(key, label, secret=secret, required=False, **more)


_BEDROCK_AUTH = (
    {
        "value": "api_key",
        "label": "Bedrock API key",
        "tag": "Easiest",
        "desc": "A single key generated on the Bedrock console — no AWS CLI or IAM setup needed.",
    },
    {
        "value": "profile",
        "label": "AWS profile",
        "desc": "Uses a named profile from ~/.aws — works with `aws configure` and `aws sso login`.",
    },
    {
        "value": "iam",
        "label": "IAM keys",
        "desc": "An IAM access key pair. For temporary STS credentials, include the session token.",
    },
)

_VERTEX_AUTH = (
    {
        "value": "adc",
        "label": "Google Cloud login",
        "tag": "Recommended",
        "desc": "Uses your machine's Google Cloud identity (Application "
        "Default Credentials). Nothing to paste — sign in once in a terminal:",
        "command": "gcloud auth application-default login",
    },
    {
        "value": "service_account",
        "label": "Service account",
        "desc": "A service-account key — the usual path on shared or headless machines.",
    },
    {
        "value": "api_key",
        "label": "API key",
        "desc": "A long-lived key from the Google Cloud console's API Keys page. "
        "Reaches Gemini models only — Claude and open-weight need Google "
        "Cloud login or a service account.",
    },
)

_OPENAI = ProviderDescriptor(
    name="openai",
    title="OpenAI",
    needs_key=True,
    fields=[
        _api_key("OpenAI API key", "sk-…"),
        _custom_endpoint(
            "https://…/openai/v1",
            "For Azure OpenAI, vLLM, or any OpenAI-compliant server. Leave blank for api.openai.com.",
        ),
    ],
    build=build_openai,
    env_key="OPENAI_API_KEY",
)

_CODEX = ProviderDescriptor(
    name="openai-codex",
    title="ChatGPT subscription",
    needs_key=False,
    fields=[],
    build=build_codex,
    blurb="Sign in with your ChatGPT plan and run OpenAI models through your "
    "subscription — no API key. Tokens stay on this machine.",
    auth="oauth",
)

# Extended thinking is on by default and there is no field for it; the profile key stays as a
# hidden override (0 turns it off).
_ANTHROPIC = ProviderDescriptor(
    name="anthropic",
    title="Claude (Anthropic)",
    needs_key=True,
    fields=[
        _api_key("Anthropic API key", "sk-ant-…"),
        _custom_endpoint(
            "https://…",
            "For a proxy or gateway that speaks the Anthropic Messages API "
            "(LiteLLM, a self-hosted router). Leave blank for api.anthropic.com.",
        ),
    ],
    build=build_anthropic,
    env_key="ANTHROPIC_API_KEY",
)

_ANTHROPIC_SUBSCRIPTION = ProviderDescriptor(
    name="anthropic-subscription",
    title="Claude subscription (Pro / Max)",
    needs_key=False,
    fields=[],
    build=build_anthropic_subscription,
    blurb="Sign in with your Claude Pro or Max plan and run Claude models through your "
    "subscription — no API key. Tokens stay on this machine.",
    auth="oauth",
)

_GEMINI = ProviderDescriptor(
    name="gemini",
    title="Gemini (Google)",
    needs_key=True,
    fields=[
        _api_key("Gemini API key", "AIza…"),
        _custom_endpoint(
            "https://…",
            "For a proxy or gateway that speaks the Generative Language API. "
            "Leave blank for generativelanguage.googleapis.com.",
        ),
    ],
    build=build_gemini,
    env_key="GEMINI_API_KEY",
)

# One authentication method at a time: people who use AWS are advanced users, and a direct
# choice beats a pile of "(optional)" fields with a hidden order of precedence.
_BEDROCK = ProviderDescriptor(
    name="bedrock",
    title="AWS Bedrock",
    needs_key=True,
    fields=[
        ProviderField(
            "region",
            "AWS region",
            placeholder="us-east-1",
            help="The region your Bedrock model access is enabled in.",
        ),
        # The default stands in for a missing value, and the builder tolerates its absence.
        _optional("auth_method", "Connect with", default="api_key", choices=_BEDROCK_AUTH),
        _optional(
            "bedrock_api_key", "Bedrock API key", secret=True,
            placeholder="ABSK…", show_when={"auth_method": "api_key"},
        ),
        _optional(
            "aws_profile", "AWS profile", placeholder="default", show_when={"auth_method": "profile"},
            help="Leave blank to use your default AWS credentials (env vars or ~/.aws).",
        ),
        _optional(
            "aws_access_key_id", "Access key ID",
            placeholder="AKIA…", show_when={"auth_method": "iam"},
        ),
        _optional("aws_secret_access_key", "Secret access key", secret=True, show_when={"auth_method": "iam"}),
        _optional(
            "aws_session_token", "Session token (STS only, optional)", secret=True,
            show_when={"auth_method": "iam"},
        ),
    ],
    build=build_bedrock,
    blurb="Runs models inside your own AWS account. Claude uses Anthropic's native "
    "Bedrock path; every other model goes through the Converse API.",
)

_VERTEX = ProviderDescriptor(
    name="vertex",
    title="Vertex AI (Google Cloud)",
    needs_key=True,
    fields=[
        ProviderField("project", "GCP project ID", placeholder="my-project-123"),
        ProviderField(
            "location",
            "Location",
            placeholder="global",
            help="Use `global` for the newest Gemini and Claude models. Some models "
            "are regional — Model Garden lists each (Claude also: us-east5 / "
            "europe-west1; Qwen3 Coder: us-south1).",
        ),
        _optional("auth_method", "Connect with", default="adc", choices=_VERTEX_AUTH),
        _optional(
            "service_account_json", "Service-account JSON", secret=True,
            show_when={"auth_method": "service_account"},
            help="Paste the JSON key, or a path to the file.",
        ),
        _optional(
            "vertex_api_key", "Vertex API key", secret=True,
            placeholder="AQ.…", show_when={"auth_method": "api_key"},
        ),
    ],
    build=build_vertex,
    blurb="Runs models inside your own Google Cloud project. Gemini and Claude use "
    "their native APIs; open-weight models go through the Vertex MaaS endpoint.",
)

# Ark is two providers on purpose. BytePlus pay-as-you-go and Volcengine Agent Plan differ in
# region, endpoint, credentials and model catalogue, and one profile covering both would let a
# model be routed to the wrong service.
_ARK = [
    responses_vendor(
        "ark",
        "BytePlus Ark",
        base_url="https://ark.ap-southeast.bytepluses.com/api/v3",
        env_key="ARK_API_KEY",
        endpoint_help="BytePlus Ark's Asia Pacific endpoint. This provider is separate from Volcengine Ark Agent Plan.",
        reasoning_summary=False,
    ),
    responses_vendor(
        "ark-agent-plan-cn",
        "Volcengine Ark Agent Plan",
        base_url="https://ark.cn-beijing.volces.com/api/plan/v3",
        env_key="ARK_AGENT_PLAN_CN_API_KEY",
        endpoint_help="Volcengine Ark Agent Plan's China (Beijing) endpoint. It requires an Agent Plan API key.",
    ),
]

# OpenAI-compatible vendors are listed as providers of their own, so nobody has to know the
# trick of pointing the OpenAI slot at another endpoint. Each keeps its own key profile, with
# its endpoint prefilled and editable.
#
# The two coding plans are subscription endpoints, not cheaper URLs for the pay-as-you-go
# `zai`: separate key, separate model set. They stay separate providers so one plan's key is
# never sent to another's API, and the catalogue projection (`catalog.py`) recognises them by
# name.
_COMPATIBLE = [
    chat_vendor(
        "zai",
        "Z AI (GLM)",
        base_url="https://api.z.ai/api/paas/v4",
        env_key="ZAI_API_KEY",
        endpoint_help="Prefilled with Z AI's international endpoint. China mainland: https://open.bigmodel.cn/api/paas/v4",
    ),
    chat_vendor(
        "zhipuai-coding-plan",
        "Zhipu AI Coding Plan",
        base_url="https://open.bigmodel.cn/api/coding/paas/v4",
        env_key="ZHIPU_API_KEY",
        vendor="Zhipu AI",
        endpoint_help="Prefilled with Zhipu AI's Coding Plan endpoint (open.bigmodel.cn). "
        "The international Z.AI Coding Plan uses https://api.z.ai/api/coding/paas/v4 — "
        "the separate 'Z.AI Coding Plan' provider. "
        "Pay-as-you-go keys belong to the separate 'Z AI (GLM)' provider.",
    ),
    chat_vendor(
        "zai-coding-plan",
        "Z.AI Coding Plan",
        base_url="https://api.z.ai/api/coding/paas/v4",
        env_key="ZHIPU_API_KEY",
        vendor="Z.AI",
        endpoint_help="Prefilled with Z.AI's international Coding Plan endpoint (api.z.ai). "
        "The China mainland plan uses https://open.bigmodel.cn/api/coding/paas/v4 — "
        "the separate 'Zhipu AI Coding Plan' provider. "
        "Pay-as-you-go keys belong to the separate 'Z AI (GLM)' provider.",
    ),
    chat_vendor("deepseek", "DeepSeek", base_url="https://api.deepseek.com", env_key="DEEPSEEK_API_KEY"),
    chat_vendor(
        "kimi",
        "Kimi (Moonshot AI)",
        base_url="https://api.moonshot.ai/v1",
        env_key="MOONSHOT_API_KEY",
        endpoint_help="Prefilled with Moonshot's international endpoint. China mainland: https://api.moonshot.cn/v1",
    ),
    chat_vendor("minimax", "MiniMax", base_url="https://api.minimax.io/v1", env_key="MINIMAX_API_KEY"),
    chat_vendor(
        "qwen",
        "Qwen (Alibaba)",
        base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        env_key="DASHSCOPE_API_KEY",
        endpoint_help="Prefilled with Alibaba Model Studio's international endpoint. China (Beijing): https://dashscope.aliyuncs.com/compatible-mode/v1",
    ),
    chat_vendor("xai", "xAI (Grok)", base_url="https://api.x.ai/v1", env_key="XAI_API_KEY"),
    chat_vendor("mistral", "Mistral", base_url="https://api.mistral.ai/v1", env_key="MISTRAL_API_KEY"),
    chat_vendor(
        "meta",
        "Meta (Muse Spark)",
        base_url="https://api.meta.ai/v1",
        env_key="META_API_KEY",
        endpoint_help="Prefilled with the Meta Model API endpoint (public preview, US-only as of 2026-07).",
    ),
    # Resellers: many labs' models behind one key, under their own model namespaces (the
    # curated ids and labels are in `matrix.py`). Groq is not listed yet: it waits until the
    # current provider surface has been tested.
    chat_vendor("together", "Together AI", base_url="https://api.together.xyz/v1", env_key="TOGETHER_API_KEY"),
    chat_vendor(
        "fireworks", "Fireworks AI", base_url="https://api.fireworks.ai/inference/v1", env_key="FIREWORKS_API_KEY"
    ),
    chat_vendor("openrouter", "OpenRouter", base_url="https://openrouter.ai/api/v1", env_key="OPENROUTER_API_KEY"),
]

# OpenCode Go is a low-cost gateway with an OpenAI-compatible root and one key from
# https://opencode.ai/auth. It demands an `x-opencode-session` routing header, which its
# builder arranges. Its models come over four wires (chat, Anthropic Messages, Responses,
# Gemini), each at its own endpoint; `OpenCodeGoProvider` sends each model through the wire and
# endpoint the catalogue names. A saved base on opencode.ai (the old `/zen/go/v1` included) is
# routed the same way; a base on another host is a proxy and is used as given.
_OPENCODE_GO = ProviderDescriptor(
    name="opencode-go",
    title="OpenCode Go",
    needs_key=True,
    fields=[
        ProviderField("api_key", "OpenCode Go API key", secret=True),
        ProviderField(
            "base_url",
            "Endpoint",
            required=False,
            default=OPENCODE_GO_BASE_URL,
            placeholder=OPENCODE_GO_BASE_URL,
            help="OpenCode Go's gateway (https://opencode.ai/docs/go). Get a key at https://opencode.ai/auth.",
        ),
    ],
    build=build_opencode_go,
    env_key="OPENCODE_API_KEY",
    blurb="Uses OpenCode Go's OpenAI-compatible API — the endpoint is prefilled, just add your key.",
)

_OLLAMA = ProviderDescriptor(
    name="ollama",
    title="Ollama (local models)",
    needs_key=False,
    fields=[
        _optional(
            "base_url", "Ollama server URL", placeholder=DEFAULT_OLLAMA_URL,
            help="Where `ollama serve` is listening. The OpenAI-compatible /v1 path is added automatically.",
        ),
    ],
    build=build_ollama,
)

DESCRIPTORS: list[ProviderDescriptor] = [
    _OPENAI, _CODEX, _ANTHROPIC, _ANTHROPIC_SUBSCRIPTION, _GEMINI, _BEDROCK, _VERTEX,
    *_ARK,
    *_COMPATIBLE,
    _OPENCODE_GO,
    _OLLAMA,
]

BY_NAME = {descriptor.name: descriptor for descriptor in DESCRIPTORS}
