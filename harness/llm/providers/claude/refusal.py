"""Refusals: the fallback a model is given, and what a refusal that gets through turns into.

Fable and Mythos 5 run safety classifiers that can decline a request that is only near
something sensitive: HTTP 200, `stop_reason: "refusal"`, empty or partial content. The
recommended posture is the server-side fallback, where the API serves the declined request
again on Opus 4.8 inside the same call. It needs a beta header and the beta endpoint.
"""

from __future__ import annotations

from typing import Any

BETA = "server-side-fallback-2026-06-01"
FALLBACK_MODEL = "claude-opus-4-8"

#: The Messages API's `stop_reason` in the finish-reason words the loop uses.
FINISH_REASONS = {
    "end_turn": "stop",
    "tool_use": "tool_calls",
    "max_tokens": "length",
    "stop_sequence": "stop",
    "refusal": "stop",
    "pause_turn": "stop",
}


def needs_fallback(model: str) -> bool:
    return model.startswith(("claude-fable", "claude-mythos"))


def finish_reason(stop_reason: Any) -> Any:
    return FINISH_REASONS.get(stop_reason, stop_reason)


def raise_if_refused(stop_reason: Any, source: Any) -> None:
    """A refusal that outlived the fallback becomes an ordinary provider error, so the turn
    ends with an error notice and a Retry button rather than a blank reply."""
    if stop_reason != "refusal":
        return
    category = getattr(getattr(source, "stop_details", None), "category", None)
    detail = f" (category: {category})" if category else ""
    raise RuntimeError(
        f"Claude's safety filter declined this request{detail}"
        " — try rephrasing, or switch model and press Retry."
    )
