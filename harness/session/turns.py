"""What counts as the PERSON speaking — one definition, because eight things ask.

`role == "user"` was a reliable landmark for as long as every user message came from a
user. It marks the turn boundary for the plan strip, the point durable resume stops
walking back, what counts as "the request", what compaction preserves
verbatim, what a session is titled after, and where an `ask_user` answer is merged.

The moment anything else needs to put content in front of the model that is not a tool
result — an image the agent asked to look at, being the first — that landmark quietly
becomes wrong in eight places at once, and each of them fails differently and silently:
resume finds no work to do, the plan strip clears mid-turn, an approval card is judged
against a picture instead of the request.

So the answer is not eight guards. It is one predicate, here, that every landmark reads —
and the next thing that needs to inject content only has to tag it.

Declared beside the tag it tests, for the same reason `CHECKPOINT_EVENTS` lives next to
the events: with several consumers and one rule, the rule must not live inside any one of
them.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

#: Set on a message the ENGINE put in front of the model on a tool's behalf, holding the
#: id of the call that asked for it. Its presence is the whole distinction: the message is
#: model-facing content, not a person taking a turn.
ATTACHED_BY = "_attached_by"


class TurnEndReason(str, Enum):
    """Why a turn ended — the full vocabulary for `turn/end {reason}` in the session
    log, the turn-end reason union, narrowed to what this engine can witness
    (packages/core/agent-loop: completed / max-tokens / aborted / error / blocked /
    interrupted). Surfaces and automation match these strings; a reason outside this
    set is a bug in the loop, not a new fact.

    Being `str` enums, the values compare equal to the bare strings every existing
    consumer already matches on (`max_iterations_exceeded`, `repetition_stopped`,
    `completed`), so old code keeps working while new code gets one nameable place.
    """

    COMPLETED = "completed"
    #: The answer was CUT — `finish_reason == "length"`. A different fact from a
    #: finished one, and automation treats it as such.
    COMPLETED_TRUNCATED = "completed_truncated"
    #: The turn CHANGED CODE and then ran nothing: the verification gate's one note was
    #: sent and ignored. A different fact from a verified finish for exactly the reason
    #: `completed_truncated` is a different fact from `completed` — "I changed the code"
    #: and "I changed the code and proved it" are different claims, and only the second
    #: is evidence. The label IS the gate's output: it does not block the finish, because
    #: a gate that can loop is worse than one that can be ignored once (see
    #: `engine.VERIFY_NUDGE_TEXT`), so the cheap path back to a plain `completed` is to
    #: run the check.
    COMPLETED_UNVERIFIED = "completed_unverified"
    #: The turn finished with the last check FAILING and no file changed since.
    #: A different fact from `completed_unverified` — that one never ran anything, this
    #: one ran the check and read a failure. Both are "not evidence", and only the
    #: `check_failed` flag on the payload (plus this reason) distinguishes them.
    COMPLETED_CHECK_FAILED = "completed_check_failed"
    #: The iteration cap or an ignored repeat guard ended the turn (checkpoint written).
    MAX_ITERATIONS = "max_iterations_exceeded"
    REPETITION_STOPPED = "repetition_stopped"
    #: A check kept failing with no file change between runs: the model was
    #: repeating a check that cannot pass unchanged, so the turn ends on the same
    #: checkpoint path as every other rail (a handoff is written; "continue" resumes).
    NO_PROGRESS = "no_progress_stopped"
    #: The turn spent its token budget (checkpoint written, same graceful path as the cap).
    #: The iteration cap bounds how MANY steps a turn takes; this bounds how big they were,
    #: because a step is not a fixed size — a hundred steps at a full context is a hundred
    #: times the traffic of a hundred small ones, and only the count was ever bounded.
    TOKEN_BUDGET = "token_budget_exceeded"
    #: The model/provider failed and every retry was spent.
    ERROR = "error"
    #: The user pressed Stop — or the process died mid-turn, in which case the close is
    #: `synthetic` (see `SessionLog.dangling_turns`): the boundary is repaired, the
    #: turn's real events are preserved untouched.
    INTERRUPTED = "interrupted"


def is_user_turn(message: Any) -> bool:
    """True when this message is the person actually saying something.

    Every landmark that used to test `role == "user"` should test this instead. A message
    the engine attached on a tool's behalf is addressed TO the model and carries no
    intent, so it must never be read as a request, a turn boundary, or a title.
    """
    if not isinstance(message, dict):
        return False
    return message.get("role") == "user" and ATTACHED_BY not in message


def attachment_message(call_id: str, parts: list[Any]) -> dict[str, Any]:
    """A message carrying content the model must SEE, from a tool that cannot return it.

    A tool result is a string (`_tool_result_message`), and no provider accepts an image
    inside one portably — OpenAI-shaped APIs require tool content to be text. The portable
    route is a user-role message right after the result, which is why the tag exists.
    """
    return {"role": "user", "content": list(parts), ATTACHED_BY: str(call_id)}
