"""What a stored message is when it is SENT.

The canonical history keeps more than a provider ever receives: the model's thinking, the
card a tool result is drawn with, timings, usage, the bytes a turn-start stamp added. Two
things need to know which part travels — the projection that builds a request
(`loop/outbound._outbound_messages`) and the estimate of what a request costs
(`compaction.estimate_tokens`). They used to keep that knowledge separately, and only the
first one had it: the estimator priced the stored row, sidecars and all, and every number
built on it described text no provider was sent. Real store, 2026-10-04: of 20 compactions,
the harness's own price of the view agreed with the provider's within 20% once; one session
carried an image twice (`content` and `api_content`) and priced its base64 as prose.

One definition, here, read by both.
"""

from __future__ import annotations

from typing import Any, Optional

from .providers.reasoning import REASONING_PLACEHOLDER
from harness.session.turns import ATTACHED_BY

#: Sidecar recording the last FULL live-context block frozen into a user message:
#: `{"stable": <digest of the non-clock parts>}`. Persisted with the message so a restarted
#: engine resumes the append-only baseline instead of re-stating the whole block.
CONTEXT_EPOCH = "_context_epoch"

#: Sidecar naming the instruction files and path-scoped rules a turn-start stamp put into
#: this message: `{"nested": [<directory>, …], "rules": [<rule key>, …]}`. What is still
#: stated in the request is then a reading of the thread — the messages a cut or a staged
#: undo has not taken out of the view — and not a set the engine keeps beside it.
CONVENTIONS = "_conventions"

#: Keys a stored message carries that no provider is sent. `_presentation` is the one the
#: model must not merely skip but never receive: it duplicates result text in a different
#: shape, so leaking it would spend context on the same bytes twice AND hand the model a
#: vocabulary meant for drawing. Add a sidecar here and both the request and its price
#: follow; there is no second list.
SIDECARS = (
    "source",  # connector cards
    "_display",  # e.g. filter-hidden counts
    # What a person attached, one row per file as the transcript draws it. The model
    # reads the attachments themselves in `content`.
    "_attached",
    # The slash command a message was sent with, as the transcript draws it. The model
    # reads the expanded command in `content`.
    "_command",
    "_presentation",  # the tool's card: a diff, a terminal's output, a file window
    # What the design check after a write found, as the row under the write draws it.
    # The model read the same findings as text in `content`.
    "_design_check",
    # The call only looked (its tool is a read, or a shell command that only reads), so a reopened
    # session folds it into "Explored" as the live one did.
    "_looks",
    "ts",  # append-time timestamps
    "reasoning",  # thinking text (echoed back only where the route requires it, below)
    "usage",  # token counts
    "timing",
    # Marks a user message as an in-turn `steer` splice: the transcript draws it as a note
    # inside the running turn, the provider must see the plain message.
    "_steer",
    # Marks a tool result whose call did not do what was asked, by the rule the log's `is_error`
    # uses: a reopened session draws the row as failed, as the live one was. The model reads the
    # failure in `content` already.
    "_failed",
    # The bytes actually sent for this message, written once per turn by the turn-start
    # stamp. Substituted INTO `content` below, then dropped: a provider would reject the
    # unknown key, and the transcript keeps the person's clean text.
    "api_content",
    CONTEXT_EPOCH,
    CONVENTIONS,
    ATTACHED_BY,
)


def wire_message(message: Any, *, echo: Optional[str] = None) -> Optional[Any]:
    """`message` as a provider receives it, or None for one that is never sent.

    A `notice` (an error, an interruption, a model-switch marker) is display-only and is
    dropped whole. `echo` names the field a thinking-mode route requires the model's own
    reasoning back under (`providers/reasoning.py`); None drops the reasoning, which is
    what every other route wants.

    Never mutates: the argument is canonical history, and this is a reader. A message that
    is already in wire form comes back as the same object.
    """
    if not isinstance(message, dict):
        return message
    if message.get("role") == "notice":
        return None
    out = message
    # `api_content` goes INTO content before the strip removes the key, not after: an
    # earlier arrangement did it later and silently got nothing.
    if message.get("api_content"):
        out = {**out, "content": message["api_content"]}
    # Read BEFORE the strip: `reasoning` is one of the sidecars removed below.
    reasoning = message.get("reasoning")
    if any(key in out for key in SIDECARS):
        out = {key: value for key, value in out.items() if key not in SIDECARS}
    if echo and out.get("role") == "assistant":
        if reasoning:
            out = {**out, echo: reasoning}
        elif out.get("tool_calls"):
            # A non-empty placeholder: an empty string is its own 400 on the non-official
            # DeepSeek-compatible relays (PI #223).
            out = {**out, echo: REASONING_PLACEHOLDER}
    return out


def wire_messages(messages: Any, *, echo: Optional[str] = None) -> list[Any]:
    """Every message of `messages` that is sent, in wire form."""
    out = []
    for message in messages or ():
        sent = wire_message(message, echo=echo)
        if sent is not None:
            out.append(sent)
    return out


__all__ = ["CONTEXT_EPOCH", "SIDECARS", "wire_message", "wire_messages"]
