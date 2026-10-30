"""The compacted block and the outbound view a compaction produces."""

from __future__ import annotations

import logging
from typing import Any, Iterable, Optional

from harness.llm.providers.base import CACHE_SEGMENTS

from .parts import part_ends, render_parts
from .plans import render_standing_plan
from .record import CompactionState
from .summarizer import CONTINUATION_CONTRACT
from .tokens import IMAGE_OFFLOAD_KEEP, IMAGE_OFFLOAD_PLACEHOLDER, estimate_tokens, prune_tool_results

logger = logging.getLogger("harness.context.compaction")

_IMAGE_PART_TYPES = frozenset({"image_url", "input_image", "image"})


#: The block's first lines, the same for any number of parts: a cut that adds one must not
#: change what stands before the earlier ones.
BLOCK_OPENING = "\n".join([
    "<compacted-history>",
    "Earlier turns of this session were compacted. The parts below are your memory of them, oldest "
    "first, each written when its turns were cut: where two disagree, the later is right.",
    "",
    "",
])


def compacted_block(state: CompactionState) -> str:
    """The single outbound message standing in for everything before the boundary."""
    parts = [BLOCK_OPENING + state.summary_text]
    if state.working_state:
        parts += ["", state.working_state]
    if state.harness_record:
        parts += ["", state.harness_record]
    if state.approved_plan:
        parts += ["", "## Approved plan (the user approved this — keep executing it)", state.approved_plan]
        if state.plan_path:
            parts += [f"(the session's plan file copy is at {state.plan_path})"]
    elif state.plan_path:
        parts += [
            "",
            "## Working plan file",
            f"Your plan is at {state.plan_path}. Read it with `read_file` before "
            "continuing, and keep it current with `update_plan`.",
        ]
    plan = render_standing_plan(state.standing_plan)
    if plan:
        parts += ["", plan]
    if state.user_messages:
        parts += ["", "## User messages in the compacted span (verbatim, chronological; a long one keeps its two ends)"]
        if state.user_messages_dropped:
            parts += [
                f"({state.user_messages_dropped} earlier user messages omitted — "
                "their intent is covered by the summary above)"
            ]
        parts += [f"- {message}" for message in state.user_messages]
    parts += ["", CONTINUATION_CONTRACT, "</compacted-history>"]
    return "\n".join(parts)


def growth(state: CompactionState, span: list[dict[str, Any]], prior: Optional[CompactionState]) -> tuple[int, int]:
    """`(produced, replaced)` in estimator tokens: the block this state would send against the
    history it stands for, including the prior block it supersedes. The summarizer path uses it to
    raise on growth, and the fold path to decline quietly."""
    replaced = estimate_tokens(span)
    if prior is not None:
        replaced += estimate_tokens([{"role": "user", "content": compacted_block(prior)}])
    produced = estimate_tokens([{"role": "user", "content": compacted_block(state)}])
    return produced, replaced


# -- image offload ---------------------------------------------------------------------------
#
# These live beside the block and not with the projection, because the decision (which indexes are
# offloaded) is state that is saved, while the replacement is only how the projection renders it.


def _message_has_image(message: dict[str, Any]) -> bool:
    content = message.get("content")
    if not isinstance(content, list):
        return False
    return any(isinstance(part, dict) and part.get("type") in _IMAGE_PART_TYPES for part in content)


def offload_images(
    messages: list[dict[str, Any]], *, keep_recent: int = IMAGE_OFFLOAD_KEEP, prior: Iterable[int] = ()
) -> list[int]:
    """The canonical indexes whose image parts the outbound view should replace.

    The newest `keep_recent` images stay, since a picture the model is looking at right now is usually
    the subject of the current work, and everything older is offloaded. `prior`, the set already
    offloaded, is carried forward, so the decision only grows and the cached prefix never flips an
    image back on."""
    offloaded = {int(i) for i in prior if isinstance(i, (int, float))}
    found = [index for index, message in enumerate(messages) if _message_has_image(message)]
    keep = max(int(keep_recent or 0), 0)
    offloaded.update(found if keep == 0 else found[:-keep])
    return sorted(offloaded)


def replace_offloaded_images(messages: list[dict[str, Any]], indices: Iterable[int]) -> list[dict[str, Any]]:
    """`messages` with each indexed message's image parts swapped for a text placeholder. Only the
    messages that actually change are copied."""
    out = list(messages)
    for index in indices:
        try:
            index = int(index)
        except (TypeError, ValueError):
            continue
        if not (0 <= index < len(out)):
            continue
        message = out[index]
        content = message.get("content")
        if not isinstance(content, list):
            continue
        replaced = [
            {"type": "text", "text": IMAGE_OFFLOAD_PLACEHOLDER}
            if isinstance(part, dict) and part.get("type") in _IMAGE_PART_TYPES
            else part
            for part in content
        ]
        if replaced != content:
            out[index] = {**message, "content": replaced}
    return out


def apply_to_outbound(
    messages: list[dict[str, Any]], state: Optional[CompactionState], *, spill: Optional[Any] = None
) -> list[dict[str, Any]]:
    """The outbound view: `[system?]`, the compacted block as a user message, and the verbatim tail,
    with aged tool results in that tail reduced to their two ends and offloaded images replaced by a
    placeholder.

    There are two reductions, deliberately separate. Pruning is cheap, needs no model, and can be
    undone by reading the file again, while summarizing costs a round trip and cannot. A state may
    carry either or both, and `boundary_index == 0` is a session that has only ever been pruned.

    `spill` (a one-argument sink) gives the prune tier a way to recover large results that carry no
    locator. Leave it out where the call must stay free of side effects (measurement, tests). The
    canonical history is untouched, and provider-private sidecars in the summarized span vanish with
    it, since replay chains legally restart after a compaction point."""
    if state is None:
        return messages
    pruned = prune_tool_results(messages, state.pruned_upto, spill=spill, rederive=state.prune_rederivable)
    if state.prune_arguments:
        from .arguments import fold_write_arguments

        pruned = fold_write_arguments(pruned, state.pruned_upto)
    if state.offloaded_images:
        pruned = replace_offloaded_images(pruned, state.offloaded_images)
    boundary = state.boundary_index
    if boundary <= 0 or boundary >= len(pruned):
        return pruned
    if pruned[boundary].get("role") == "tool":
        # The tail must not open with a tool result. It answers a call the summarized block now
        # represents, so the model would read a reply to a question it can no longer see, and hosted
        # chat templates reject the sequence outright. Selection cannot produce this, because
        # `turn_starts` takes only `user` and `assistant` roles, so reaching here means a boundary was
        # written by something other than that path. It is warned and not raised because this runs
        # on every provider feed: refusing to send the request would turn a visible defect into a dead
        # session. The state is otherwise used as it is, which is what `boundary_index` promised and
        # what `tests/test_compaction_recall.py` pins.
        logger.warning(
            "compaction boundary %d lands on a tool result; the outbound tail will open "
            "with an orphaned tool reply",
            boundary,
        )
    head: list[dict[str, Any]] = []
    if pruned and pruned[0].get("role") == "system":
        head.append(pruned[0])
    block: dict[str, Any] = {"role": "user", "content": compacted_block(state)}
    segments = block_segments(state)
    if segments:
        block[CACHE_SEGMENTS] = segments
    head.append(block)
    return head + pruned[boundary:]


def block_segments(state: CompactionState) -> list[int]:
    """Where each part ends in `compacted_block(state)`, for a provider that caches by explicit
    breakpoints. Empty when the block's text is not the current rendering of its parts:
    a state saved before parts were rendered this way keeps the text it was sent with, and offsets
    computed from its parts would cut it in the wrong places."""
    if not state.parts or state.summary_text != render_parts(state.parts):
        return []
    return [len(BLOCK_OPENING) + end for end in part_ends(state.parts)]
