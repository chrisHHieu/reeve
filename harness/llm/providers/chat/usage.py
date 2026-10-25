"""Token counts from a chat-completions `usage` object."""

from __future__ import annotations

from typing import Any, Optional

from ..base import TokenUsage


def cache_read_tokens(usage: Any) -> int:
    """The cached share of the prompt, under whichever name the endpoint uses.

    OpenAI nests it as `prompt_tokens_details.cached_tokens`; DeepSeek reports the same number
    flat as `prompt_cache_hit_tokens`. Reading only the first counted every DeepSeek hit as
    fresh input, and compaction's cache-aware summarizer replay is gated on this number, so it
    was switched off there too. An absent field falls through to the next, but a reported 0 is
    taken as the answer.
    """
    cached = getattr(getattr(usage, "prompt_tokens_details", None), "cached_tokens", None)
    if cached is None:
        cached = getattr(usage, "prompt_cache_hit_tokens", None)
    return int(cached or 0)


def usage_from(usage: Any) -> Optional[TokenUsage]:
    """`prompt_tokens` includes the cached tokens, so they are taken out of `input` and
    counted as `cache_read`. This API has no separate count for cache writes."""
    if usage is None:
        return None
    cached = cache_read_tokens(usage)
    return TokenUsage(
        input=max(int(getattr(usage, "prompt_tokens", 0) or 0) - cached, 0),
        output=int(getattr(usage, "completion_tokens", 0) or 0),
        cache_read=cached,
    )
