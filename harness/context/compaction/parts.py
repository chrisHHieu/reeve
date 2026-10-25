"""The parts of a compacted block: each cut's summary or note, written once.

Every compaction used to hand the summarizer the previous summary with the turns since and
take back one text, so whatever the first cut kept went through a model again at every cut
after it. On the store (2026-10-14), in the 7 sessions with three or more cuts, the median cut
lost 65% of the concrete names, paths, flags and numbers the block before it held, and of
the 49 such details the first summary of the deepest session held, 1 was still there 20 cuts
later. A fold did the same when `replace_notes` asked the model for one note standing in for
all of them (26 of 48 folds).

So a cut adds a part and leaves the earlier ones as they were written. The parts share one
budget; past it the harness combines two adjacent parts in a model call of its own, the pair
whose text has been rewritten the fewest times. That keeps merges binary-counter shaped: after
n cuts no detail has been rewritten more than about log2(n) times, where one running summary
rewrote the oldest n - 1 times.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from .tokens import SUMMARY_MAX_TOKENS, block_limits, estimate_tokens, redact_secrets, summary_call_tokens

#: The parts of a block together, as a multiple of what one summary is asked to be
#: (`block_limits`). One summary is the most a single cut may write; twice that leaves room
#: for the earlier parts beside it, and on the store the median summary was a third of its
#: target (10,674 characters against 8,192 tokens), so a block holds several before a merge.
PARTS_BUDGET_FACTOR = 2

#: Who wrote a part: the summarizer at a threshold cut, or the model's own note at a fold.
SUMMARY, NOTE = "summary", "note"

#: A merge used to drop what "the newer part says was finished and is no longer needed". Finished is
#: not forgotten: in the long session (2026-10-23, deepseek-v4.1-flash) the third merge of a run
#: dropped task one's "22 passed" because the newer part called task one finished, and the person's
#: next question was that number. Replayed on those two parts, the old wording kept it 0 of 6 times
#: and this one 6 of 6.
MERGE_INSTRUCTION = (
    "Two consecutive parts of an AI coding agent's memory of one session are below, the older "
    "first. Combine them into ONE part that replaces both. Keep every concrete name, path, "
    "command, flag, id, number and decision with its reason. Finished work stays as its "
    "outcome: what was done, the files it changed, and the check that proved it with its result "
    "line (for example \"22 passed\"), because the person may ask about it later and this part is "
    "the only place left that knows; only the steps that led there may be shortened. Where the "
    "parts disagree, the newer part is right. Keep the headings of the older part where it has "
    "them. Never quote a secret. Output only the combined part."
)


def part(text: str, author: str = SUMMARY, rewrites: int = 0) -> dict[str, Any]:
    return {"text": str(text or ""), "author": author, "rewrites": max(0, int(rewrites or 0))}


def normalize_parts(raw: Any) -> list[dict[str, Any]]:
    """Parts as saved, read back defensively: an entry with no text is not a part."""
    out = []
    for item in raw or []:
        if isinstance(item, dict) and str(item.get("text") or "").strip():
            author = NOTE if item.get("author") == NOTE else SUMMARY
            out.append(part(item["text"], author, item.get("rewrites") or 0))
    return out


def parts_of(state: Any) -> list[dict[str, Any]]:
    """The parts a state's block is made of. A state from before parts existed is one part,
    its summary, at no rewrites known."""
    if state is None:
        return []
    if state.parts:
        return [dict(p) for p in state.parts]
    return [part(state.summary_text)] if (state.summary_text or "").strip() else []


def render_parts(parts: list[dict[str, Any]]) -> str:
    """The text of the parts as the block carries it. One part is its own text, exactly what a
    block held before parts existed; more are numbered, oldest first."""
    if len(parts) <= 1:
        return parts[0]["text"] if parts else ""
    total = len(parts)
    return "\n\n".join(
        f"### Part {index} of {total}{' (oldest)' if index == 1 else ' (newest)' if index == total else ''}\n"
        + item["text"]
        for index, item in enumerate(parts, 1)
    )


def parts_tokens(parts: list[dict[str, Any]]) -> int:
    return estimate_tokens([{"role": "user", "content": render_parts(parts)}]) if parts else 0


def summary_target(trigger: Optional[int]) -> int:
    """What one summary is asked to be, in tokens."""
    return block_limits(trigger)[0] if trigger is not None else SUMMARY_MAX_TOKENS


def parts_budget(trigger: Optional[int]) -> int:
    return PARTS_BUDGET_FACTOR * summary_target(trigger)


def max_rewrites(parts: list[dict[str, Any]]) -> int:
    return max((int(p.get("rewrites") or 0) for p in parts), default=0)


def merge_pick(parts: list[dict[str, Any]]) -> Optional[int]:
    """The index `i` of the pair `(i, i + 1)` to combine: the one whose text has been rewritten
    the fewest times, the older pair on a tie. None with fewer than two parts."""
    if len(parts) < 2:
        return None
    return min(
        range(len(parts) - 1),
        key=lambda i: (max(parts[i]["rewrites"], parts[i + 1]["rewrites"]), i),
    )


def merge_parts(
    parts: list[dict[str, Any]],
    *,
    ask: Callable[[list[dict[str, Any]], int], str],
    budget: int,
    target_tokens: int,
    max_merges: int = 3,
) -> tuple[list[dict[str, Any]], int]:
    """`parts` brought under `budget` by combining pairs, and how many merges that took.

    `ask(messages, max_tokens)` is one model call returning its text. Never raises: a merge that
    fails leaves the parts as they are, over the budget, and the next cut tries again. Losing a
    cut because a merge failed would cost the history the merge was protecting."""
    out = [dict(p) for p in parts]
    merged = 0
    while merged < max_merges and len(out) > 1 and parts_tokens(out) > budget:
        index = merge_pick(out)
        older, newer = out[index], out[index + 1]
        words = max(300, int(target_tokens * 0.75))
        messages = [
            {"role": "system", "content": MERGE_INSTRUCTION},
            {
                "role": "user",
                "content": f"<older-part>\n{older['text']}\n</older-part>\n\n<newer-part>\n{newer['text']}\n"
                f"</newer-part>\n\nLength: under about {words} words.",
            },
        ]
        try:
            text = redact_secrets((ask(messages, summary_call_tokens(target_tokens)) or "").strip())
        except Exception:
            break
        if not text:
            break
        author = SUMMARY if SUMMARY in (older["author"], newer["author"]) else NOTE
        out[index : index + 2] = [part(text, author, max(older["rewrites"], newer["rewrites"]) + 1)]
        merged += 1
    return out, merged


__all__ = [
    "MERGE_INSTRUCTION",
    "NOTE",
    "PARTS_BUDGET_FACTOR",
    "SUMMARY",
    "max_rewrites",
    "merge_parts",
    "merge_pick",
    "normalize_parts",
    "part",
    "parts_budget",
    "parts_of",
    "parts_tokens",
    "render_parts",
    "summary_target",
]
