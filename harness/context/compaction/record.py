"""What one compaction is: the point it cut at, what stands in for the history, and how it went."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .parts import normalize_parts
from .plans import normalize_plan
from .tokens import COMPACTION_COOLDOWN_SECONDS, INEFFECTIVE_STREAK_LIMIT


@dataclass(frozen=True)
class CompactionNotice:
    """What one compaction attempt did, in numbers a caller can report.

    The policy used to return a bare sentence, so "compaction ran and saved nothing" and "compaction
    saved half the window" were the same event to every consumer, the person watching a session that
    keeps compacting included."""

    text: str
    # Which lever produced this: `summarized`, `pruned` or `trimmed`. They have different
    # consequences, since a prune can be undone by reading again and a summary cannot. Older sessions
    # may also carry `prepared`, a summary adopted from the removed soft-threshold park.
    reason: str
    saved_tokens: int = 0
    saved_pct: float = 0.0
    # Tokens the outbound view now measures at. 0 means not measured.
    now_tokens: int = 0
    # What the summarizer round trip cost, when one ran (0 for a prune). It is provider traffic the
    # turn paid for, and the engine's token budget counts it too.
    summarizer_tokens: int = 0
    # The model-written summary that now stands in for the compacted span (empty for a prune). It is
    # shown to the person so "context was compacted" can be inspected and is not a black box. It is
    # for display only and never re-enters the model's view.
    summary: str = ""

    @property
    def numbers(self) -> dict[str, Any]:
        """The payload a surface reads. It is kept apart from `text` so the sentence stays the model's
        and the numbers stay the reader's."""
        return {
            "reason": self.reason,
            "saved_tokens": self.saved_tokens,
            "saved_pct": round(self.saved_pct, 4),
            "now_tokens": self.now_tokens,
            "summarizer_tokens": self.summarizer_tokens,
            "summary": self.summary,
        }


@dataclass
class CompactionState:
    """One compaction point. `boundary_index` is an index into the canonical message list: messages
    before it are represented by the compacted block in the outbound view, and messages from it on
    are sent as they are. It is saved with the session, so a reload keeps the view."""

    boundary_index: int
    # The block's memory of the cut turns as it is sent: `render_parts(parts)`, or, for a state
    # written before parts existed, the one summary it held.
    summary_text: str
    working_state: str
    # The canonical index before which tool results are shortened in the outbound view. It moves
    # forward only: a result once pruned stays pruned, so the prefix a provider has cached changes at
    # most once per result and does not drift under it. 0 means nothing is pruned, which is also what
    # a state written before this field existed reads as.
    pruned_upto: int = 0
    # Whether the prune also folds the write payloads in the calls before `pruned_upto`.
    # It sits on the state and not on the engine, so a view rebuilt from it, on a resume or to price a
    # request, is the view that was sent. It only moves forward, like the point itself.
    prune_arguments: bool = False
    # Whether the prune also drops the aged output of calls that can be made again, keeping a line
    # that names the call (or the file the output was saved to): the faithful tier of a cut, which
    # never rewrites what it keeps. Forward-only like `prune_arguments`, and on the state for the
    # same reason: a view rebuilt from it is the view that was sent.
    prune_rederivable: bool = False
    # What the harness itself saw when the cut was made (`loop/witnessed.py`): the last check and
    # its verdict, what was edited since. Rendered after the summary; the model never writes it.
    harness_record: str = ""
    # The canonical message indexes whose image parts are replaced by a text placeholder in the
    # outbound view: images older than the newest `IMAGE_OFFLOAD_KEEP`, at the first compaction or
    # prune. It is a union that only grows, so an image once offloaded stays offloaded and the cached
    # prefix changes once per image and does not drift. It is empty on a state written before this
    # field existed, when no images were offloaded.
    offloaded_images: list[int] = field(default_factory=list)
    user_messages: list[str] = field(default_factory=list)
    # How many older user messages the `USER_MESSAGES_MAX` cap dropped across all compactions of this
    # session, which keeps the block's "N earlier omitted" honest.
    user_messages_dropped: int = 0
    created_at: float = 0.0
    model_used: str = ""
    # The task list as it stood when this compaction ran, put in by the engine from the live
    # `TodoList` and not mined out of the span. The span is the wrong source: a plan written before
    # the boundary and updated after it would put a stale list in the block, contradicting the
    # verbatim tail, and a block that contradicts the tail is worse than one that leaves the plan out.
    # The live list is the only copy that is true whichever side of the boundary the last write
    # landed on.
    standing_plan: list[dict[str, str]] = field(default_factory=list)
    # The last plan the person approved (`propose_plan`), bounded and verbatim. It lives here for the
    # reason the todo list does: the tool call that carried it is clipped to 200 characters inside the
    # summarized span, so without this a long approved plan disappears at the first compaction, and
    # the session goes on carrying out a plan it can no longer read. A section of the block gives the
    # same result as a plan file would, without a second artifact to keep in step.
    approved_plan: str = ""
    # Where the session's working plan file lives (`.reeve/plans/<session>.md` inside a root), when
    # the surface gave the engine one. The block points at it, so a session that compacts mid-planning
    # can read the plan back and not lose the draft, and so the approved copy above is the contract
    # while this is the working document.
    plan_path: str = ""
    # What `summary_text` is made of: one part per cut, a summary or the model's note,
    # each kept as it was written, oldest first; `rewrites` counts the merges its text went
    # through. Empty on a state from before parts existed (`parts_of` reads it as one part).
    parts: list[dict[str, Any]] = field(default_factory=list)
    # -- the record of its own attempts, for the cooldown ----------------------------------
    # When the policy last tried. `compaction_gate` reads it. The field existed as `created_at`
    # before, and nothing ever consulted it.
    last_attempt_at: float = 0.0
    attempts: int = 0
    # What the last attempt did: `summarized`, `pruned`, `failed` or `not_smaller`. `failed` is the
    # one the gate treats as a reason to wait. A summarizer that failed once may well fail again, and
    # there is no trim fallback any more, so the view stays exactly as it was.
    reason: str = ""
    saved_tokens: int = 0
    saved_pct: float = 0.0
    # Consecutive attempts that bought less than `MIN_SAVINGS_PCT`.
    ineffective_streak: int = 0
    # Pairs of parts the last attempt combined to stay inside the parts' budget.
    merged: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "boundary_index": self.boundary_index,
            "summary_text": self.summary_text,
            "working_state": self.working_state,
            "pruned_upto": self.pruned_upto,
            "prune_arguments": self.prune_arguments,
            "prune_rederivable": self.prune_rederivable,
            "harness_record": self.harness_record,
            "offloaded_images": list(self.offloaded_images),
            "user_messages": list(self.user_messages),
            "user_messages_dropped": self.user_messages_dropped,
            "created_at": self.created_at,
            "model_used": self.model_used,
            "standing_plan": [dict(item) for item in self.standing_plan],
            "approved_plan": self.approved_plan,
            "plan_path": self.plan_path,
            "parts": [dict(item) for item in self.parts],
            "merged": self.merged,
            "last_attempt_at": self.last_attempt_at,
            "attempts": self.attempts,
            "reason": self.reason,
            "saved_tokens": self.saved_tokens,
            "saved_pct": self.saved_pct,
            "ineffective_streak": self.ineffective_streak,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> Optional["CompactionState"]:
        if not isinstance(raw, dict) or "boundary_index" not in raw:
            return None
        return cls(
            boundary_index=int(raw.get("boundary_index", 0)),
            summary_text=str(raw.get("summary_text", "")),
            working_state=str(raw.get("working_state", "")),
            pruned_upto=int(raw.get("pruned_upto", 0) or 0),
            prune_arguments=bool(raw.get("prune_arguments", False)),
            prune_rederivable=bool(raw.get("prune_rederivable", False)),
            harness_record=str(raw.get("harness_record", "") or ""),
            offloaded_images=[int(i) for i in (raw.get("offloaded_images") or []) if isinstance(i, (int, float))],
            user_messages=[str(u) for u in raw.get("user_messages") or []],
            user_messages_dropped=int(raw.get("user_messages_dropped", 0)),
            created_at=float(raw.get("created_at", 0.0)),
            model_used=str(raw.get("model_used", "")),
            standing_plan=normalize_plan(raw.get("standing_plan")),
            approved_plan=str(raw.get("approved_plan", "") or ""),
            plan_path=str(raw.get("plan_path", "") or ""),
            parts=normalize_parts(raw.get("parts")),
            merged=int(raw.get("merged", 0) or 0),
            # The rest are all defaulted, so a state written by an earlier version reads back as a
            # policy that has not tried yet, which is the truth about it.
            last_attempt_at=float(raw.get("last_attempt_at", 0.0) or 0.0),
            attempts=int(raw.get("attempts", 0) or 0),
            reason=str(raw.get("reason", "")),
            saved_tokens=int(raw.get("saved_tokens", 0) or 0),
            saved_pct=float(raw.get("saved_pct", 0.0) or 0.0),
            ineffective_streak=int(raw.get("ineffective_streak", 0) or 0),
        )


def compaction_gate(state: Optional[CompactionState], *, now: float) -> tuple[bool, str]:
    """Whether the policy may try at all, and why not when it may not.

    `(True, "ok")` when nothing holds it back. The reasons are a closed vocabulary (`ok`,
    `cooldown`), so a caller must handle them by name and not by matching prose."""
    if state is None or not state.last_attempt_at:
        return True, "ok"
    since = now - state.last_attempt_at
    if state.reason == "failed" and since < COMPACTION_COOLDOWN_SECONDS:
        # A summarizer that failed once may well fail again on the next step, and each attempt is two
        # round trips (the try and its retry). One failure is enough to wait. A failure leaves the
        # view untouched, so waiting costs nothing but time.
        return False, "cooldown"
    if state.ineffective_streak >= INEFFECTIVE_STREAK_LIMIT and since < COMPACTION_COOLDOWN_SECONDS:
        return False, "cooldown"
    return True, "ok"
