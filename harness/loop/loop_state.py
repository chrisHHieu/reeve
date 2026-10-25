"""The two small records the turn loop hands between its phases.

`TurnEngine._loop_body` used to be one 650-line function whose locals (the retry budget, the empty-
completion signature, the text streamed so far) were visible to every branch. They are the loop's
real state, so they are named here and passed to the phase that needs them; the loop itself reads as
the order of the phases.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

from harness.llm.providers.base import AssistantTurn
from harness.llm.providers.blocks import BlockAssembler


@dataclass
class LoopState:
    """What survives from one iteration of the turn loop to the next."""

    iterations: int = 0
    #: Retries for the model call in flight. It lives out here because the retry is spent by going
    #: around the loop, and a counter declared per iteration would be reset by the very jump that
    #: consumed it: the budget would never deplete, and a permanently broken stream would quietly run
    #: to the iteration cap instead. Cleared after every completed stream, so an iteration that
    #: recovered leaves the next one whole.
    attempts: int = 0
    #: What the last empty completion looked like, for the deterministic-empty rule. Cleared with
    #: `attempts`: a completed stream starts the next call whole.
    last_empty_signature: Optional[tuple] = None
    #: What a phase decided: "go" runs another iteration, "end" means the turn is over (the phase
    #: has already emitted its terminal event). Every phase that can end the turn sets it.
    next: str = "go"


@dataclass
class StepState:
    """One model call: what has streamed so far, and when it went out."""

    streamed: list[str] = field(default_factory=list)
    streamed_reasoning: list[str] = field(default_factory=list)
    #: One per step: block indexes are a step's private numbering, and a fresh assembler is how the
    #: next step starts counting from zero again.
    blocks: BlockAssembler = field(default_factory=BlockAssembler)
    #: Only the loop knows when the request went out and when the first token came back: by the time
    #: a message is appended, both are gone and no consumer can recover them. Reasoning counts as a
    #: first token: on a thinking model the wait that matters ends there.
    dispatched_at: float = field(default_factory=time.time)
    first_token_at: Optional[float] = None
    turn: Optional[AssistantTurn] = None

    def partial_turn(self) -> AssistantTurn:
        """What the user watched arrive: text and thinking, NO tool calls (any half-formed call would
        either orphan or execute against the stop)."""
        return AssistantTurn(
            text="".join(self.streamed) or None,
            reasoning="".join(self.streamed_reasoning) or None,
        )
