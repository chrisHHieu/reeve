"""Model providers: the `ProviderClient` interface, each provider's client, the registry that knows
them all, and the router the engines hold."""

from .base import AssistantTurn, ModelCapabilities, ProviderClient, StreamChunk, ToolCall, ToolCallDelta
from .bedrock import BedrockProvider
from .blocks import REASONING, TEXT, TOOL_CALL, BlockAssembler, BlockDelta
from .capabilities import capabilities_for
from .chat import OpenAIProvider, resolve_api_key
from .claude import AnthropicProvider
from .codex import CodexProvider
from .gemini import GeminiProvider
from .registry import (
    ProviderDescriptor,
    ProviderField,
    build_provider_client,
    descriptor_configured,
    detect_provider,
    get_descriptor,
    provider_descriptors,
    provider_names,
    verify_provider_key,
)
from .responses import OpenAIResponsesProvider
from .router import ProviderRouter
from .vertex import VertexProvider

_INTERFACE = ["AssistantTurn", "ModelCapabilities", "ProviderClient", "StreamChunk", "ToolCall", "ToolCallDelta"]
_STREAM_BLOCKS = ["BlockAssembler", "BlockDelta", "REASONING", "TEXT", "TOOL_CALL"]
_CLIENTS = [
    "AnthropicProvider",
    "BedrockProvider",
    "CodexProvider",
    "GeminiProvider",
    "OpenAIProvider",
    "OpenAIResponsesProvider",
    "ProviderRouter",
    "VertexProvider",
    "capabilities_for",
    "resolve_api_key",
]
_REGISTRY = [
    "ProviderDescriptor",
    "ProviderField",
    "build_provider_client",
    "descriptor_configured",
    "detect_provider",
    "get_descriptor",
    "provider_descriptors",
    "provider_names",
    "verify_provider_key",
]

__all__ = _INTERFACE + _STREAM_BLOCKS + _CLIENTS + _REGISTRY
