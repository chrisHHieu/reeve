"""What a tool shows a person (split from `contract.py` when it crossed the
architecture ceiling). The dataclass is unchanged; `contract.py`
re-exports it, so every existing import keeps working."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Optional

#: Presentational category, for the icon/treatment a surface picks. Not a risk class —
#: `read`, `search` and `fetch` are all RiskClass.READ but read very differently on a
#: card. Risk stays in `harness.safety.risk`; this is only how the call LOOKS.
ToolKind = Literal[
    "read", "write", "delete", "search", "execute", "fetch", "send", "other"
]

_KINDS = frozenset(
    ("read", "write", "delete", "search", "execute", "fetch", "send", "other")
)

#: The kinds of a call that only looks: a surface folds a run of them into one "Explored" line, and
#: the engine and the transcript route both read it from here.
LOOKING_KINDS = frozenset(("read", "search"))



from .cards import validate as validate_card


@dataclass(frozen=True, slots=True)
class ToolPresentation:
    """One tool call, in the user's language.

    `summary` is a complete sentence about the world, not about the runtime: "Gửi tin
    nhắn vào #sales-team", never "gọi send_message". It is required and non-empty — a
    blank card is worse than no card, because it looks like the runtime knows something
    and is withholding it.
    """

    summary: str
    detail: str = ""
    kind: ToolKind = "other"
    #: The delegated run behind this call, when there is one (`subagent`). It rides the
    #: PRESENTATION rather than the result preview because a preview is the first 300
    #: characters of the result JSON: a `run_id` placed after an 8k report never survived
    #: it, and the row then had no way to offer that run's transcript (owner report
    #: 2026-09-16). The presentation is delivered whole on both paths — the live event and
    #: the replayed `_presentation` sidecar.
    run_id: Optional[str] = None
    #: How many steps that run took, for the row's `· N steps`. Presentational, so it
    #: belongs here and not in the report the model reads.
    run_steps: Optional[int] = None
    #: A file this call produced or preserved that a person may want to open — the
    #: complete output behind a truncated one, a report just written. Surfaces decide
    #: whether to offer it; nothing is obliged to.
    #:
    #: It exists because the fact could not survive any other route. A tool result
    #: reaches a surface as a 300-character preview of its JSON, and a spilled log's path
    #: sits at the END of a dict whose `output` key is already 20,000 characters long —
    #: so the model was told where the full log was and the person watching never was.
    link: Optional[str] = None
    #: The structured half of the card — a terminal command and its output, a diff, a
    #: file window, search hits, web results. Built by `harness.tools.cards`, which owns
    #: the vocabulary; validated here so a malformed card is a logged defect rather than
    #: a broken row. Optional: a tool with only a sentence to say keeps saying it, and
    #: nothing else about this dataclass changes.
    #:
    #: It is a separate field rather than more columns on this dataclass because a card is
    #: a DIFFERENT KIND of thing from a sentence: `summary` is prose a person reads, the
    #: card is data a surface branches on. Folding stdout and line numbers into typed
    #: fields here would make every tool's presentation carry every other tool's shape.
    card: Optional[dict[str, Any]] = None

    def __post_init__(self) -> None:
        # Fail loud at construction. Presenters are invoked inside `safe_*`, so a raise
        # here degrades to the pre-contract rendering AND leaves a logged defect —
        # strict contract, safe runtime.
        if not isinstance(self.summary, str) or not self.summary.strip():
            raise ValueError("ToolPresentation.summary must be a non-empty string")
        if self.kind not in _KINDS:
            raise ValueError(
                f"ToolPresentation.kind {self.kind!r} is not one of {sorted(_KINDS)}"
            )
        if self.link is not None and not str(self.link).strip():
            # A blank link renders a button that opens nothing.
            raise ValueError("ToolPresentation.link must be a path or None")
        if self.card is not None:
            validate_card(self.card)

    def as_dict(self) -> dict[str, Any]:
        """The surface-facing payload. `detail` is omitted when empty so a card can
        test presence instead of truthiness."""
        out: dict[str, Any] = {"summary": self.summary, "kind": self.kind}
        if self.detail:
            out["detail"] = self.detail
        if self.link:
            out["link"] = self.link
        if self.card:
            out["card"] = self.card
        # The delegated-run reference (`runRef.ts`). These two were declared but never
        # serialized, so a `task` row's `↗ Open session` and `· N steps` could only come
        # from the result preview's first 300 characters — which is exactly the path the
        # fields were added to replace. `run_steps` is omitted when zero/None: no steps
        # yet is not a fact to print.
        if self.run_id:
            out["run_id"] = self.run_id
        if self.run_steps:
            out["run_steps"] = self.run_steps
        return out


#: The reach classes a tool can declare (a side-effect scope, narrowed to what this
#: harness distinguishes). "none" is the one that matters most: a call with no side effect
#: is the only kind allowed to overlap other calls.
