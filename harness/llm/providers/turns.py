"""The values a provider hands back: calls, usage, a finished turn, stream pieces, capabilities."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

#: Private settings key carrying the session id to providers that route by cache prefix. The
#: engine injects it once per session and it travels in the ordinary `**settings` channel; each
#: provider translates or DROPS it, so it is never sent under this name (a provider that forwarded
#: it verbatim would send an unknown field). OpenAI's Chat Completions and Responses APIs know it
#: as `prompt_cache_key`: the same key routes a session's requests to one backend, which is what
#: lets the byte-stable prefix actually hit the cache. Compatible endpoints (Ollama, gateways)
#: never see it; only stock OpenAI gets the translation.
CACHE_AFFINITY = "cache_affinity"


def session_headers(name: Optional[str], settings: dict[str, Any], standin: str) -> dict[str, str]:
    """The routing header a gateway demands (OpenCode Go's `x-opencode-session`), valued with the
    session id from `settings`, or `standin` for a call that has none (a background pass). Empty
    when the provider was built without one, so no other route starts sending it."""
    if not name:
        return {}
    affinity = settings.get(CACHE_AFFINITY)
    return {name: str(affinity) if affinity else standin}


@dataclass
class ToolCall:
    """One tool call the model asked for, with its arguments parsed."""

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class TokenUsage:
    """Token counts of one model round-trip, normalized across vendors.

    `input` is fresh (uncached) prompt tokens only; cached prompt tokens go to
    `cache_read`/`cache_write`. A vendor that reports no cache split leaves those at 0. `output`
    includes thinking tokens where the vendor bills them as output (Gemini).
    """

    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0

    @property
    def context_tokens(self) -> int:
        """The prompt side in full: what occupied the context window."""
        return self.input + self.cache_read + self.cache_write

    @property
    def total(self) -> int:
        """Everything this round-trip sent and received, cached or not.

        This is what a spend ceiling counts, not `context_tokens`. A cached prompt costs less per
        token but the provider still read it, and the loop a budget exists to stop is the one that
        re-sends a large context every step, so its traffic sits in `cache_read`. Counting only
        fresh input would let the costliest loop be the one that never trips the ceiling.
        """
        return self.context_tokens + self.output

    def as_dict(self) -> dict[str, int]:
        return {
            "input": self.input,
            "output": self.output,
            "cache_read": self.cache_read,
            "cache_write": self.cache_write,
        }


@dataclass
class AssistantTurn:
    """One answer from the model: free text, tool calls, or both."""

    text: Optional[str] = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: Optional[str] = None
    raw: Any = field(default=None, repr=False, compare=False)
    # The model's thinking text (DeepSeek `reasoning_content`, Gemini thought summaries, ...).
    # Display only: kept on the assistant message as the `reasoning` sidecar and shown in the GUI,
    # but removed before every provider call, so it is never replayed as context.
    reasoning: Optional[str] = None
    # Provider-private sidecars kept on the canonical assistant message under underscore-prefixed
    # keys (`_gemini` holds thought signatures). The owning provider reads its own key when it
    # converts history; every other provider drops foreign underscore keys before its wire call.
    extras: dict[str, Any] = field(default_factory=dict)
    # Normalized token counts for this round-trip; None when the backend reported none (some
    # compatible servers). Never estimated.
    usage: Optional[TokenUsage] = None
    # Which machine answered, when the endpoint is an aggregator fanning one model across several
    # (OpenRouter names the `provider` on every chunk). Observation only: written to the session
    # event log, never kept on a message, never replayed. Without it there is only guessing: one
    # model id covered six upstreams with first-token times from 0.6s to 14s, one of which answered
    # with nothing at all, and none of that showed in a message.
    upstream: Optional[str] = None
    # Thinking blocks the request that produced this answer did not carry back, by why:
    # `stale` (their conversation had changed, dropped before sending), `api` and `other_model` (the
    # API dropped them: a changed prefix, another model's block), `retried` (a 400 refused them and the
    # request went again without thinking). Observation only, like `upstream`: written to the event
    # log, never kept on a message.
    thinking_dropped: Optional[dict[str, int]] = None

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)

    @property
    def is_empty(self) -> bool:
        """The answer carried nothing: no text, no thinking and no call.

        Thinking counts as content, since a turn that only thought still used time the person
        watched and re-rolling it would discard that. Whitespace does not count, because a lone
        newline reaches the transcript as an empty bubble.
        """
        return not (self.text or "").strip() and not self.reasoning and not self.tool_calls


@dataclass(frozen=True)
class ModelCapabilities:
    """What a model on a provider can do, so a request can degrade gracefully."""

    tools: bool = True
    vision: bool = False
    # Native PDF input (an OpenAI `file` part, an Anthropic document, Gemini inline data). A model
    # without it gets a local fallback: extracted text or page images.
    pdf: bool = False
    # Audio and video taken directly. This says "the adapter can send it", not "the model can
    # hear": a model that hears through an API this harness does not speak is False. A model
    # without it gets a sentence naming the saved file.
    audio: bool = False
    video: bool = False
    parallel_tool_calls: bool = True
    streaming: bool = True


@dataclass
class ToolCallDelta:
    """A fragment of a tool call while it is being written.

    A provider that streams tool calls sends the name once and then the arguments in pieces, so a
    surface can show which tool is being called well before it can show what with. `index` is the
    provider's own call index, which keeps two concurrent calls' fragments apart; `blocks.py`
    turns it into a block index. `id` and `name` stay empty until the provider has sent them.
    """

    index: int
    id: str = ""
    name: str = ""
    arguments_delta: str = ""


@dataclass
class StreamChunk:
    """One streamed piece: a text or reasoning delta, a tool-call fragment, and, last, the turn.

    Every field is optional and several can be set together, because a provider may batch a
    thought and a sentence into one wire frame.
    """

    text_delta: Optional[str] = None
    reasoning_delta: Optional[str] = None
    #: Set only by providers whose wire protocol streams tool calls. Where it is absent, the call
    #: still arrives whole in `turn`.
    tool_call_delta: Optional[ToolCallDelta] = None
    turn: Optional[AssistantTurn] = None
