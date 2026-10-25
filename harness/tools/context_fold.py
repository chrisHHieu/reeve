"""`fold_context` — the model replaces the older part of its own context with a note.

The tool only carries the request to the engine that owns the context (`engine.fold_context`):
what a fold is, what it keeps and when it is refused live in `harness/context/compaction/fold.py` and
`harness/loop/context_fold.py`. Session-bound: it acts on the context of the session that
holds it, so a subagent's copied registry must never carry its parent's.
"""

from __future__ import annotations

import json
from typing import Any, Callable

import aisuite as ai

from . import cards
from .contract import ToolContract, ToolPresentation, attach_contract, clip

#: The tool's name, in one place: the engine's hint names it and checks the registry for it.
FOLD_CONTEXT = "fold_context"

_FOLD_SCHEMA = {
    "type": "function",
    "function": {
        "name": FOLD_CONTEXT,
        "description": (
            "Replace the older part of YOUR OWN context with a note you write, so the rest "
            "of the work runs in a small context. Everything older than your most recent "
            "steps is folded: file contents you read, command output, earlier attempts, "
            "your earlier reasoning. From your next step you see your note instead. "
            "Kept for you without asking: the user's messages (the request you are working "
            "on in full, a long earlier one by its two ends), your task list, the approved "
            "plan, and the list of files and commands you touched. Nothing is deleted: files "
            "are still on disk and search_history still finds the original turns. A file "
            "you read before the fold must be read again before you overwrite it whole. "
            "Fold when a piece of work is finished and its details are no longer needed, or "
            "when the context note says it is getting large. Do not fold in the middle of "
            "an edit you have not verified. It needs no step of its own: call it in the same "
            "step as the calls that start the next piece of work, and what they return is kept. "
            "The note is your memory of what is folded. Write what you still need: what was "
            "decided and why, exact names, paths, line numbers and values, what is done and "
            "verified, what failed and should not be retried, and what is left to do. Your "
            "earlier notes and summaries stay above it as they were written, so do not repeat "
            "them; once they grow large, the harness combines the two oldest."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "note": {
                    "type": "string",
                    "description": "What you need to keep from the part being folded.",
                },
            },
            "required": ["note"],
        },
    },
}


def context_fold_tools(fold: Callable[..., dict[str, Any]]) -> list:
    """`fold(note)` is the engine's method; it answers with a dict that either carries
    `error` (nothing changed) or the numbers of what was folded."""

    def fold_context(note: str = "") -> dict:
        """Replace the older part of your context with a note you write."""
        return fold(str(note or ""))

    wrapped = ai.tool(
        fold_context,
        metadata=ai.ToolMetadata(category="planning", risk_level="low", capabilities=["context"]),
    )
    wrapped.__tool_schema__ = _FOLD_SCHEMA
    wrapped.__tool_session_bound__ = True
    attach_contract(wrapped, FOLD_CONTRACT)
    return [wrapped]


# -- what the model and a person read back ---------------------------------------------


def _fold_render(arguments: dict[str, Any], value: Any) -> str:
    if not isinstance(value, dict):
        return json.dumps(value, default=str)
    error = str(value.get("error") or "").strip()
    if error:
        return f"Error: not folded — {error}"
    return (
        f"Folded {value.get('folded_messages', 0)} older messages into your note; the last "
        f"{value.get('kept_messages', 0)} stay as they are. Context: "
        f"~{int(value.get('before_tokens') or 0):,} tokens before, "
        f"~{int(value.get('after_tokens') or 0):,} from your next step. search_history still "
        "finds what you and the user said in them, but not tool output: read a file or page "
        "again from its source."
    )


def _fold_present(arguments: dict[str, Any], value: Any = None, card_meta: Any = None) -> ToolPresentation:
    done = isinstance(value, dict) and not value.get("error") and "after_tokens" in value
    detail = clip(str(arguments.get("note") or ""), 160)
    if not done:
        return ToolPresentation(
            summary="Fold its own context into a note",
            detail=detail,
            kind="other",
            card=cards.generic_call("Fold context", kind="other", raw_input=arguments.get("note")),
        )
    # A fold is a step of the turn, and its row is the one place a surface tells it: the
    # card carries what it did in numbers. The transcript used to show the compaction
    # divider for it as well, which cut the turn in two at the fold.
    folded = int(value.get("folded_messages") or 0)
    return ToolPresentation(
        summary=f"Folded {folded} earlier message{'' if folded == 1 else 's'} into its own note",
        detail=detail,
        kind="other",
        card=cards.fold(**{name: max(0, int(value.get(name) or 0)) for name in cards.FOLD_COUNTS}),
    )


FOLD_CONTRACT = ToolContract(
    render=_fold_render,
    present_call=lambda arguments: _fold_present(arguments),
    present_result=_fold_present,
)

__all__ = ["FOLD_CONTEXT", "FOLD_CONTRACT", "context_fold_tools"]
