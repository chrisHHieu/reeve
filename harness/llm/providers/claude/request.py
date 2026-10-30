"""One Messages API request, built from the loop's messages, tools and settings."""

from __future__ import annotations

from typing import Any, Optional

from ..base import sanitize_messages
from .convert import CACHE_POINT, convert_messages, convert_tools
from .thinking import DEFAULT_MAX_TOKENS, apply_thinking

#: The settings the Messages API takes. Anything else the loop carries (a frequency penalty,
#: say) is dropped rather than sent to be refused.
_ACCEPTED = {"max_tokens", "temperature", "top_p", "top_k", "stop_sequences", "metadata", "thinking"}


def cache_marker(cache_ttl: Optional[str]) -> dict[str, Any]:
    """The `cache_control` marker. The API reads `5m` (the default) and `1h`; any other value
    means the default instead of a rejected request. A 1h write costs 2x the base input price
    against 1.25x for 5m, so it only pays when turns come more than five minutes apart, which
    is why it is opt-in."""
    marker: dict[str, Any] = {"type": "ephemeral"}
    if (cache_ttl or "").strip().lower() == "1h":
        marker["ttl"] = "1h"
    return marker


def add_cache_breakpoints(kwargs: dict[str, Any], cache_ttl: Optional[str] = None) -> None:
    """Ask for prompt caching with up to three breakpoints (the API takes four):

    - the last system block, which caches the tools and the system prompt together (tools
      render first);
    - the last part of a compacted block, when the conversation has one. A cut
      rewrites the conversation from the block on, but the parts before its new one are
      unchanged, and this is where an earlier request wrote them. Without it the request after
      a fold re-read only the system prompt and the tools;
    - the last content block of the final message, which caches the whole conversation so
      far. Each request then reads the previous turns from cache and writes only the new
      tail, because history only ever appends.

    This only touches the outgoing request: stored history never carries `cache_control`,
    and the marked block is a copy. A prefix under the model's cacheable minimum is quietly
    not cached.

    Every marker uses the same tier on purpose. The API needs a 1h entry to come before a 5m
    one, so a mixed pair is a 400 waiting for the next reordering.
    """
    marker = cache_marker(cache_ttl)
    system = kwargs.get("system")
    if isinstance(system, str) and system:
        kwargs["system"] = [{"type": "text", "text": system, "cache_control": marker}]
    messages = kwargs.get("messages") or []
    for message in messages:
        for index, block in enumerate(message.get("content") or []):
            if isinstance(block, dict) and block.get(CACHE_POINT):
                block = {key: value for key, value in block.items() if key != CACHE_POINT}
                message["content"][index] = {**block, "cache_control": marker}
    if messages:
        content = messages[-1].get("content")
        if isinstance(content, list) and content:
            content[-1] = {**content[-1], "cache_control": marker}


def build_request(
    *,
    model: str,
    messages: list[dict[str, Any]],
    tools: Optional[list[dict[str, Any]]],
    settings: dict[str, Any],
    thinking_budget: int = 0,
    cache_ttl: Optional[str] = None,
) -> dict[str, Any]:
    system, converted = convert_messages(messages)

    if "stop" in settings and "stop_sequences" not in settings:
        stop = settings["stop"]
        settings["stop_sequences"] = [stop] if isinstance(stop, str) else list(stop)
    accepted = {key: value for key, value in settings.items() if key in _ACCEPTED}
    apply_thinking(accepted, model, thinking_budget)
    accepted.setdefault("max_tokens", DEFAULT_MAX_TOKENS)

    kwargs: dict[str, Any] = {"model": model, "messages": converted, **accepted}
    if system:
        kwargs["system"] = system
    if tools:
        kwargs["tools"] = convert_tools(tools)
    add_cache_breakpoints(kwargs, cache_ttl)
    # A lone surrogate in a tool result or the system prompt (it comes from Windows text
    # paths) would fail at the transport as a UnicodeEncodeError. Replaced once, here, for the
    # streamed and the plain call alike.
    return sanitize_messages(kwargs)
