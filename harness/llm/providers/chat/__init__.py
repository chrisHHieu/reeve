"""The Chat Completions wire: OpenAI-compatible endpoints."""

from .credentials import resolve_api_key
from .provider import OpenAIProvider

__all__ = ["OpenAIProvider", "resolve_api_key"]
