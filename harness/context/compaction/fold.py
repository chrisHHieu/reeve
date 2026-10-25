"""A fold: a compaction the MODEL authors.

Context Language Models (arXiv 2609.37725) move the POLICY of context management into the
model — when to shrink, and what to keep — and leave the mechanism with the harness. Their
mechanism is the context mirrored to a file the model edits with Bash. This harness cannot
take that literally: a request must be explainable from the log (0001), `/undo` only ever adds
(0021), and the canonical history is never rewritten (the outbound view is a projection).

So the model's edit is the one this harness already knows how to make durable: everything
older than the recent tail becomes one block. The harness compaction asks a summarizer to
write that block when a threshold says so. A fold has the model write it — it knows what it
still needs — at the moment it chooses.

What the model does NOT author, because code can guarantee it and a note cannot: the user's
own messages (kept verbatim, and the request being worked on kept nearly whole), the files and
commands the span touched, the standing task list, the approved plan. A thin note costs detail;
it cannot cost the request.

Pure: messages in, a `CompactionState` out, or `FoldRefused` with the sentence the model reads.
"""

from __future__ import annotations

import time
from typing import Any, Optional

from harness.safety.redaction import redact_secrets
from harness.session.turns import is_user_turn
from .block import growth
from .boundary import pick_boundary
from .extraction import cap_user_messages, extract_user_messages, extract_working_state
from .parts import NOTE, part, parts_of, render_parts
from .plans import clip_approved_plan, normalize_plan
from .record import CompactionState
from .tokens import _USER_MESSAGES_MAX, block_limits

#: Below this a note is a label, not a memory: "done, moving on" cannot stand in for the steps
#: it replaces. The refusal says so and the model writes a real one.
FOLD_MIN_NOTE_CHARS = 120
#: How much of the request being worked on survives verbatim when the fold reaches into the
#: current turn. Every other user message is clipped to a line or two (`_USER_MESSAGE_CLIP`);
#: this one is the task.
ACTIVE_REQUEST_CLIP = 4_000
#: What one note may hold, in characters (about 4,000 tokens). A session's notes together
#: are held to the block's parts budget (`parts.py`), which the harness keeps by
#: combining the two least-rewritten parts. Before that they were held to this figure and, past it,
#: asked the model for one note replacing all of them: 26 of 48 folds on the store did, and
#: each was a rewrite of everything the earlier folds had kept.
NOTE_MAX_CHARS = 16_000
#: What heads the model's note inside the block, so a later reader (the model, the
#: summarizer that merges it, a person) can tell whose words these are.
NOTE_HEADING = "## Your note (you wrote this when you folded the context)"


class FoldRefused(Exception):
    """A fold that was not applied. `str(exc)` is the sentence for the model."""


def build_fold_state(
    messages: list[dict[str, Any]],
    *,
    note: str,
    keep_tokens: int,
    prior: Optional[CompactionState] = None,
    trigger: Optional[int] = None,
    standing_plan: Optional[list[dict[str, str]]] = None,
    approved_plan: Optional[str] = None,
    plan_path: Optional[str] = None,
    model: str = "",
) -> CompactionState:
    """The state a fold would adopt: the span older than the kept tail, replaced by `note`.

    The boundary is the summarizer path's own (`pick_boundary`), so a fold and a compaction
    keep the same tail and can follow each other in either order. The note is a new part of the
    block; the earlier parts stay as they were written. This stays model-free, so the
    parts may come back over their budget: the engine combines two of them (`merge_parts`).
    """
    text = redact_secrets(str(note or "").strip())
    if len(text) < FOLD_MIN_NOTE_CHARS:
        raise FoldRefused(
            f"the note is {len(text)} characters. It is all you will have of what is folded: "
            "say what was decided and why, the exact names, paths and numbers you still "
            "need, what is done and verified, and what is left."
        )
    boundary = pick_boundary(messages, keep_tokens=keep_tokens)
    if boundary is None or (prior is not None and boundary <= prior.boundary_index):
        raise FoldRefused(
            "there is nothing to fold yet: everything older than your most recent steps is "
            "already folded."
        )
    span_start = prior.boundary_index if prior is not None else 0
    span = messages[span_start:boundary]

    section = f"{NOTE_HEADING}\n{text}"
    if len(section) > NOTE_MAX_CHARS:
        raise FoldRefused(
            f"the note is {len(text):,} characters and a note is held to {NOTE_MAX_CHARS:,}. "
            "Keep what later work needs: search_history still finds what was said, and a file "
            "or page can be read again."
        )
    parts = [*parts_of(prior), part(section, NOTE)]

    _, user_limit = block_limits(trigger) if trigger is not None else (0, _USER_MESSAGES_MAX)
    users, dropped = cap_user_messages(
        (list(prior.user_messages) if prior is not None else []) + _user_messages(messages, span, boundary),
        prior_dropped=prior.user_messages_dropped if prior is not None else 0,
        limit=user_limit,
    )
    start = 1 if messages and messages[0].get("role") == "system" else 0
    state = CompactionState(
        boundary_index=boundary,
        summary_text=render_parts(parts),
        parts=parts,
        # Over everything before the boundary, not the new span alone: the canonical history
        # is all still here, and a note is not asked to remember which files were written.
        working_state=extract_working_state(messages[start:boundary]),
        pruned_upto=prior.pruned_upto if prior is not None else 0,
        user_messages=users,
        user_messages_dropped=dropped,
        created_at=time.time(),
        model_used=model,
        standing_plan=normalize_plan(standing_plan),
        approved_plan=clip_approved_plan(
            approved_plan
            if approved_plan is not None
            else (prior.approved_plan if prior is not None else "")
        ),
        plan_path=(
            plan_path if plan_path is not None else (prior.plan_path if prior is not None else "")
        ),
    )
    produced, replaced = growth(state, span, prior)
    if produced >= replaced:
        raise FoldRefused(
            f"the fold would not shrink the context: the note and what is kept with it come "
            f"to ~{produced:,} tokens, replacing ~{replaced:,}. Fold when more has "
            "accumulated, or write a shorter note."
        )
    return state


def _user_messages(
    messages: list[dict[str, Any]], span: list[dict[str, Any]], boundary: int
) -> list[str]:
    """The span's user messages, with the request still being worked on kept nearly whole.

    When no user turn sits at or after the boundary, the newest one in the span is the task
    this fold happens in the middle of. Clipped like the rest it would be two lines of a
    request the model is about to stop being able to read.
    """
    active = not any(is_user_turn(message) for message in messages[boundary:])
    newest = next((i for i in range(len(span) - 1, -1, -1) if is_user_turn(span[i])), None)
    if not active or newest is None:
        return extract_user_messages(span)
    return extract_user_messages(span[:newest]) + extract_user_messages(
        [span[newest]], clip=ACTIVE_REQUEST_CLIP
    ) + extract_user_messages(span[newest + 1:])


__all__ = [
    "ACTIVE_REQUEST_CLIP",
    "FOLD_MIN_NOTE_CHARS",
    "NOTE_HEADING",
    "NOTE_MAX_CHARS",
    "FoldRefused",
    "build_fold_state",
]
