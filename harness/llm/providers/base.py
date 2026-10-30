"""Provider-agnostic model access.

The runtime never imports a provider SDK. It talks to a `ProviderClient`: the OpenAI Responses
provider, the Chat Completions provider (the compatible world), and the native Anthropic, Gemini,
Bedrock and Vertex providers, all chosen by the registry and router. The values they return live in
`turns.py`; they are re-exported here because this is where callers have always imported them.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional

from .sanitize import sanitize_messages, sanitize_text
from .turns import (
    CACHE_AFFINITY,
    CACHE_SEGMENTS,
    AssistantTurn,
    ModelCapabilities,
    StreamChunk,
    TokenUsage,
    ToolCall,
    ToolCallDelta,
)

__all__ = [
    "CACHE_AFFINITY",
    "CACHE_SEGMENTS",
    "AssistantTurn",
    "ModelCapabilities",
    "ProviderClient",
    "StreamChunk",
    "TokenUsage",
    "ToolCall",
    "ToolCallDelta",
    "sanitize_messages",
    "sanitize_text",
]


Messages = list[dict[str, Any]]
Tools = Optional[list[dict[str, Any]]]


class ProviderClient(ABC):
    """One request, one answer, whoever the vendor is.

    A call blocks, on purpose: the turn engine runs it on a worker thread (`asyncio.to_thread`).
    There is no loop of turns in here; the engine runs the agent loop and asks for one step at a
    time.
    """

    @abstractmethod
    def complete(self, *, model: str, messages: Messages, tools: Tools = None, **settings: Any) -> AssistantTurn:
        """The model's next turn after `messages`, with `tools` on offer."""

    @abstractmethod
    def capabilities(self, model: str) -> ModelCapabilities:
        """What `model` can do on this provider."""

    def media_carry(self, model: str, mime: str) -> bool:
        """Whether this adapter can put a recording of this MIME type on the wire.

        `capabilities(...).audio/video` is what the catalogue says the model can do; this is the
        other half, what the harness can encode for this route. A model that hears over an API the
        adapter does not speak, or in a format the route rejects, is not sent bytes it would
        answer with a 400: the file is named in a sentence instead. The default is nothing.
        """
        return False

    def image_limits(self, model: str) -> tuple[int, "int | None"]:
        """The route's `(max decoded bytes, max long side)` for one image.

        The adapter owns this like it owns the capability flags. A provider that serves several
        vendors under one name (Bedrock, Vertex) decides per model, and a gateway that proxies
        Anthropic decides for the Claude models it forwards. The base answers the widest limits.
        `harness/llm/images.py` asks here before its own name heuristic and prefix table, and
        refuses with a sentence rather than send an image the route would reject mid-turn.

        The base answers with the module's name heuristics, not the widest entry: a declaration
        always wins in `limits_for`, so a base that answered `DEFAULT_LIMITS` hid every heuristic
        from the engine, which only ever asks through the router. Found 2026-10-16, session
        `50a81a82-280`: `opencode-go:deepseek-v4.1-flash` got no pixel ceiling, a 1000×11899
        screenshot went out as-is and every later request was refused with a 400.
        """
        from ..images import limits_for

        return limits_for(model)

    def reasoning_passback(self, model: str) -> Optional[str]:
        """The wire field this route wants reasoning echoed under, or None to drop it.

        Most endpoints reject or ignore an echoed chain of thought, while the thinking-mode
        families require it back on tool-call turns (a 400 otherwise). The engine asks per request,
        so switching model mid-session decides it again; `reasoning.py` holds the table.
        """
        return None

    def stream(self, *, model: str, messages: Messages, tools: Tools = None, **settings: Any):
        """`StreamChunk`s as the answer arrives. Here, for a provider that cannot stream, the whole
        turn comes as one last chunk; one that can overrides this."""
        whole = self.complete(model=model, messages=messages, tools=tools, **settings)
        yield StreamChunk(turn=whole)
