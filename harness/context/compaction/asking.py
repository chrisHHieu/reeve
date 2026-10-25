"""One summarizer-shaped round trip, and the merge of a block's parts that rides on it.

A threshold cut (`summarizer.build_state`) and a fold (`loop/context_fold.py`) both bring the parts
of a block inside their budget through `settle_parts`.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from .parts import PARTS_BUDGET_FACTOR, merge_parts
from .tokens import redact_secrets


def _streamed_turn(chunks: Any) -> Any:
    """The final turn of a provider stream (`StreamChunk.turn`), its deltas discarded, since nobody
    watches a summary being written. A stream that ends without one says so and does not pass on an
    empty answer."""
    final = None
    for chunk in chunks:
        if getattr(chunk, "turn", None) is not None:
            final = chunk.turn
    if final is None:
        raise RuntimeError("summarizer stream ended without a final turn")
    return final


def ask_text(
    provider: Any,
    model: str,
    messages: list[dict[str, Any]],
    *,
    max_tokens: int,
    tools: Optional[list[dict[str, Any]]] = None,
    usage_sink: Optional[Callable[[Any], None]] = None,
) -> str:
    """One summarizer-shaped round trip: the reply's text, redacted, or an exception that says why
    there is none."""
    # Streamed, and read whole. The transport's read timeout (providers/timeouts.py, 180 s) bounds
    # silence on the wire, which is what it is for. A call that is not streamed sends no byte until the
    # model has finished, so the same timeout capped the whole summary instead. A reasoning model
    # summarizing a long context took longer (glm-5.3-flash: 356 s for one that landed,
    # `APITimeoutError` after 578 s for one that did not, 2026-10-02), and a `/compact` failed for
    # taking time. A stream sends reasoning and text as they come, so a summarizer that is working is
    # never silent for long, and one that has stopped still fails on the same timeout.
    stream = getattr(provider, "stream", None)
    if callable(stream):
        turn = _streamed_turn(stream(model=model, messages=messages, tools=tools, max_tokens=max_tokens))
    else:  # a bare client with no stream (every ProviderClient has one; a duck-typed stand-in may not)
        turn = provider.complete(model=model, messages=messages, tools=tools, max_tokens=max_tokens)
    if usage_sink is not None:
        usage_sink(getattr(turn, "usage", None))
    text = (getattr(turn, "text", None) or "").strip()
    if not text:
        # It names why the summary is empty. A reasoning model that spent its whole output budget
        # thinking, a reply made only of tool calls, and a provider that returned nothing at all look
        # the same behind a bare "empty summary" (session 3d9041d1-0e6 failed twice with the bare
        # message and the reason had to be guessed).
        usage = getattr(turn, "usage", None)
        detail = (
            f"finish_reason={getattr(turn, 'finish_reason', None)}, "
            f"tool_calls={len(getattr(turn, 'tool_calls', None) or ())}, "
            f"reasoning_chars={len(getattr(turn, 'reasoning', None) or '')}"
            + (f", tokens={getattr(usage, 'total', 0)}" if usage is not None else "")
        )
        raise RuntimeError(f"summarizer returned an empty summary ({detail})")
    # The prompt asks for no secrets and this net enforces it. A summarizer that paraphrases a key it
    # was told to redact is exactly the failure it exists to catch.
    return redact_secrets(text)


def settle_parts(
    parts: list[dict[str, Any]],
    *,
    provider: Any,
    model: str,
    summary_tokens: int,
    output_limit: Optional[int] = None,
    usage_sink: Optional[Callable[[Any], None]] = None,
) -> tuple[list[dict[str, Any]], int]:
    """`parts` inside their budget (`PARTS_BUDGET_FACTOR` × one summary), combined through
    `provider` when they are not, and how many merges that took. Never raises."""

    def ask(messages: list[dict[str, Any]], tokens: int) -> str:
        if output_limit and output_limit > 0:
            tokens = min(tokens, int(output_limit))
        return ask_text(provider, model, messages, max_tokens=tokens, usage_sink=usage_sink)

    return merge_parts(
        parts, ask=ask, budget=PARTS_BUDGET_FACTOR * summary_tokens, target_tokens=summary_tokens
    )


__all__ = ["ask_text", "settle_parts"]
