"""What the turn engine tells a surface (the app, the TUI, an IDE) as a turn runs.

For what the model sees, one event per message or per tool call. A tool whose output comes out
over time (a build, a long command) also sends `tool_output_delta` chunks (`harness/tools/
output.py`); those are only for showing, since the model reads the call's RESULT, so a surface that
missed some has missed nothing.

The answer streams in blocks (`providers/blocks.py`): `assistant_delta`, `reasoning_delta` and
`tool_call_delta` each say which block they extend, so a surface can lay one step's thinking, words
and calls out in the order they came.

The values are the wire: the app and the logs read them, so a value never changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class EventType(str, Enum):
    TURN_START = "turn_start"
    # The three deltas carry `index`, the block they extend, numbered as blocks arrive by
    # providers/blocks.py. With two buckets nobody could tell whether the thinking came before
    # the sentence or after; with the index a surface shows the step as it happened.
    ASSISTANT_DELTA = "assistant_delta"
    REASONING_DELTA = "reasoning_delta"  # the model's thinking: shown, never sent back to it
    TOOL_CALL_DELTA = "tool_call_delta"  # a call still being written, not yet run
    ASSISTANT_MESSAGE = "assistant_message"
    TOOL_PROPOSED = "tool_proposed"
    PERMISSION_REQUIRED = "permission_required"
    TOOL_REQUESTED = "tool_requested"  # the agent asks for a command-line tool it lacks
    QUESTION_REQUESTED = "question_requested"  # the agent asks the person, free text or choices
    PLAN_PROPOSED = "plan_proposed"  # a plan put to the person, leaving plan mode
    PLAN_UPDATED = "plan_updated"  # the plan file was written (update_plan)
    TOOL_STARTED = "tool_started"
    # Output of a tool still running (build logs, test output, a dev server's first lines). For
    # showing only and never sent to the model, which reads the call's result instead.
    TOOL_OUTPUT_DELTA = "tool_output_delta"
    TOOL_FINISHED = "tool_finished"
    ITERATION_END = "iteration_end"
    TURN_END = "turn_end"
    ERROR = "error"
    INTERRUPTED = "interrupted"
    RETRYING = "retrying"  # a stream broke off mid-answer and the same request goes again
    # The model failed in a way another route can answer, and the turn goes on with the next
    # fallback configured. The person reads the `model_switch` notice in the
    # transcript; this carries the facts for a surface that draws the switch itself.
    FALLBACK = "model_fallback"
    COMPACTING = "compacting"  # a compaction began; surfaces show it while it lasts
    COMPACTED = "compacted"  # the history sent was compacted, by summary or by trim


#: A step ended, so what it cost is in the session log: a reply from the model (its usage is on the
#: `assistant/message` row) or a tool's result. The Metrics page hears of it then rather than at
#: the end of the turn, so a long run shows its tokens as they go
#: (`SessionManager.note_metrics_progress`).
STEP_EVENTS = frozenset({EventType.ASSISTANT_MESSAGE, EventType.TOOL_FINISHED})

#: Events after which the conversation's thread must be on disk.
#:
#: They are named here, beside the events, because two places run turns: the websocket turn and
#: the background one (waking itself, a channel's message, a scheduled run), and only the first
#: knew the rule. The background turn saved once at its end, so a restart in the middle lost every
#: step of exactly the runs nobody watches and that last longest.
#:
#: `TOOL_FINISHED` is here because `ConversationStore.save` appends only what it has not written
#: (a checkpoint is a line and a row update, not the thread written again), so there is no reason
#: to lose eleven finished tools while the twelfth runs.
CHECKPOINT_EVENTS = frozenset(
    {
        # The person's message has arrived. A new conversation gets its row now, not when a
        # client connects, so one opened and never used stays out of Recents.
        EventType.TURN_START,
        # Waiting on a person for as long as they take; the process may restart before they answer.
        EventType.PERMISSION_REQUIRED,
        EventType.PLAN_PROPOSED,
        EventType.TOOL_FINISHED,
        EventType.ITERATION_END,
    }
)


@dataclass
class Event:
    type: EventType
    data: dict[str, Any] = field(default_factory=dict)
