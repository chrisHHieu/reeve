"""Runtime invariants over the session log — "model-visible means logged".

The conversation is kept AS an append-only event log, so the invariant
is structural: the model history is derived from the log, and a runtime check throws the
moment the two disagree. reeve keeps the message list and writes the log BESIDE it
(session_log.py), so the same invariant takes the audit shape: **everything the model is
about to see must have left a trace in the log** — every user turn, every assistant
message (including the partials a failed stream leaves behind), every tool result
(including denials, interruptions, and the approver flows), every injected prompt
(nudges, checkpoints, steering). The reverse direction is deliberately not checked: the
log is lossy by design (fields clip at 4k), a diagnostic view, never a replay source.

The bug class this catches is the one that motivated it: a new injection path appends to
`self.messages`, forgets to log, and the model starts reasoning over context no
log can explain — the same facts the GUI and automation see, missing from the
record they are read from. The audit's first pass over this code found four such paths
before it ever shipped: steering injections, the six approver flows' tool results,
interrupted tool results, and partial assistant messages.

Counts are how the per-class checks work (one assistant message per `assistant/message`
event), because content is the wrong key: the outbound view legitimately rewrites text
(recall stamps, folded results, the ephemeral system-context block) while identity is
preserved. Tool results are keyed by `call_id`, which survives every projection.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from .session_log import SessionLog
from .turns import ATTACHED_BY

logger = logging.getLogger(__name__)
#: `ATTACHED_BY` values with a log event of their own. Any OTHER tag is an attachment
#: (`turns.attachment_message`), tagged with the call id its content rode in on —
#: covered by that call's `tool/result` event.
_TAG_EVENTS = {
    "verify_nudge": "verify",
    # The red-check note and the check-loop note both ride the same
    # `verify/nudge` rows. The first shipped without this entry, so the audit read the
    # note as a call-id attachment and logged a violation for a history that was in fact
    # fully recorded (owner-visible as of the check-loop tests).
    "check_failed_nudge": "verify",
    "check_loop_nudge": "verify",
    # A recalled repair is rebuilt from its `repair/recalled` row.
    "repair_recall": "repair",
    # The request for a lesson rides the `repair/fixed` row of the repair that earned it
    # (`asked: true`).
    "repair_lesson": "lesson",
    "todo_nudge": "todo",
    # The fold hint is rebuilt from its `context/budget` row (`hint: "fold"`).
    "fold_hint": "budget",
    "checkpoint": "checkpoint",
    # The output-token continuation's instruction rides in as a `steering/message` event
    # (source "truncation-continue") and is folded away before the turn ends.
    "truncation-continue": "steering",
    # A lifecycle hook's context (harness/loop/hooks.py) enters the turn the same way, with
    # `source: "hook:<event>"` naming which hook said it.
    "hook": "steering",
}


class InvariantError(RuntimeError):
    """Raised only in strict mode: a model request went out over unlogged history."""


def _empty_index() -> dict[str, Any]:
    return {
        "header": 0,
        "user": 0,
        "steering": 0,
        "assistant": 0,
        "verify": 0,
        "repair": 0,
        "lesson": 0,
        "todo": 0,
        "budget": 0,
        "checkpoint": 0,
        "summary": 0,
        "tool_calls": set(),
        "tool_results": set(),
    }


def _index_events(index: dict[str, Any], events: list[dict[str, Any]]) -> None:
    for event in events:
        kind = event.get("type")
        if kind == "request/header":
            index["header"] += 1
        elif kind == "user/message":
            index["user"] += 1
        elif kind == "steering/message":
            index["steering"] += 1
        elif kind == "assistant/message":
            index["assistant"] += 1
        elif kind == "tool/call":
            if event.get("call_id"):
                index["tool_calls"].add(event["call_id"])
        elif kind == "tool/result":
            if event.get("call_id"):
                index["tool_results"].add(event["call_id"])
        elif kind == "verify/nudge":
            index["verify"] += 1
        elif kind == "repair/recalled":
            index["repair"] += 1
        elif kind == "repair/fixed" and event.get("asked"):
            index["lesson"] += 1
        elif kind == "todo/nudge":
            index["todo"] += 1
        elif kind == "context/budget":
            index["budget"] += 1
        elif kind == "turn/checkpoint":
            index["checkpoint"] += 1
        elif kind == "compaction/summary":
            index["summary"] += 1


def audit_messages(messages: list[dict[str, Any]], index: dict[str, Any]) -> list[str]:
    """Coverage check of one model request against the log index. Pure — takes what
    the model will see and what the log has recorded, returns readable violations."""
    violations: list[str] = []
    plain_user = 0
    assistant = 0
    for position, message in enumerate(messages):
        role = message.get("role")
        if role == "system":
            if not index["header"]:
                violations.append(
                    f"messages[{position}]: system prompt with no request/header event"
                )
        elif role == "notice":
            continue  # display-only; `_outbound_messages` drops it before the provider
        elif role == "tool":
            call_id = message.get("tool_call_id")
            if call_id and call_id not in index["tool_results"]:
                violations.append(
                    f"messages[{position}]: tool result for call {call_id!r} "
                    "with no tool/result event"
                )
        elif role == "assistant":
            assistant += 1
        elif role == "user":
            tag = message.get(ATTACHED_BY)
            if tag is None:
                plain_user += 1  # a typed turn or a steering injection — counted classes
            elif tag in _TAG_EVENTS:
                if not index[_TAG_EVENTS[tag]]:
                    violations.append(
                        f"messages[{position}]: {tag} prompt with no matching log event"
                    )
            elif tag not in index["tool_results"]:
                violations.append(
                    f"messages[{position}]: attachment for call {tag!r} "
                    "with no tool/result event"
                )
    if assistant > index["assistant"]:
        violations.append(
            f"{assistant - index['assistant']} assistant message(s) with no "
            "assistant/message event"
        )
    if plain_user > index["user"] + index["steering"]:
        violations.append(
            f"{plain_user - index['user'] - index['steering']} user message(s) with no "
            "user/message or steering/message event"
        )
    return violations


def audit_workflow_log(events: list[dict[str, Any]]) -> list[str]:
    """Well-formedness of the workflow vocabulary in one session log.

    The audit above asks whether the MESSAGES are covered by the log; this one asks the
    reverse for the workflow events, where the failure mode is a reader — the run-view
    node, a resume, a journal reader — seeing a run that cannot have happened:

      · a `run-end` or `note` for a root that never started;
      · a `run-end` written twice for one root;
      · a dispatch with no dispatch-start (a live call lost its beginning);
      · a REPLAYED dispatch WITH a dispatch-start (the log says both "replayed" and
        "it ran" — one of the two is lying);
      · a `run-end` whose own tallies disagree with the events it summarizes.

    A `run-start` with no `run-end` is NOT a violation: it is the recorded shape of an
    interrupted run (`open_workflow_runs` names those), and silence about a known
    interruption is the log working, not the log failing.
    """
    started: set[str] = set()
    ended: set[str] = set()
    starts_seen: set[str] = set()
    dispatched: dict[str, int] = {}
    replayed: dict[str, int] = {}
    violations: list[str] = []
    for event in events:
        kind = event.get("type")
        root = str(event.get("root_call_id") or "")
        if kind == "workflow/run-start":
            if not root:
                violations.append("workflow/run-start with no root_call_id")
                continue
            if root in ended:
                violations.append(f"workflow/run-start for {root} after its own run-end")
            started.add(root)
        elif kind == "workflow/note":
            if root and root not in started:
                violations.append(f"workflow/note for {root} with no run-start")
        elif kind == "tool/code-dispatch-start":
            call_id = str(event.get("call_id") or "")
            if call_id:
                starts_seen.add(call_id)
        elif kind == "tool/code-dispatch":
            if root not in started:
                continue  # a `run_code` sub-call: not workflow vocabulary
            call_id = str(event.get("call_id") or "")
            if root in ended:
                violations.append(
                    f"dispatch {call_id!r} for {root} after its run-end"
                )
                continue
            dispatched[root] = dispatched.get(root, 0) + 1
            if event.get("replayed") is True:
                replayed[root] = replayed.get(root, 0) + 1
                if call_id in starts_seen:
                    violations.append(
                        f"replayed dispatch {call_id!r} also has a dispatch-start — "
                        "a replayed call never ran"
                    )
            elif call_id not in starts_seen:
                violations.append(
                    f"dispatch {call_id!r} for {root} with no dispatch-start"
                )
        elif kind == "workflow/run-end":
            if root not in started:
                violations.append(f"workflow/run-end for {root} with no run-start")
                continue
            if root in ended:
                violations.append(f"workflow/run-end for {root} written twice")
            ended.add(root)
            agents = event.get("agents")
            if isinstance(agents, int) and agents != dispatched.get(root, 0):
                violations.append(
                    f"workflow/run-end for {root} reports {agents} agent(s) but the log "
                    f"holds {dispatched.get(root, 0)} dispatch(es)"
                )
            replay_count = event.get("replayed")
            if isinstance(replay_count, int) and replay_count != replayed.get(root, 0):
                violations.append(
                    f"workflow/run-end for {root} reports {replay_count} replay(es) but "
                    f"the log holds {replayed.get(root, 0)}"
                )
    return violations


def open_workflow_runs(events: list[dict[str, Any]]) -> list[str]:
    """Root call ids with a `run-start` and no `run-end`, in the order they opened.

    Not a violation — the recorded shape of a run that was interrupted (timeout, Stop, a
    killed process). Named separately so a reader can say "this run never finished"
    without the audit calling the log corrupt.
    """
    order: list[str] = []
    ended: set[str] = set()
    for event in events:
        root = str(event.get("root_call_id") or "")
        if not root:
            continue
        if event.get("type") == "workflow/run-start" and root not in order:
            order.append(root)
        elif event.get("type") == "workflow/run-end":
            ended.add(root)
    return [root for root in order if root not in ended]


class ModelVisibilityAudit:
    """One engine's running coverage check: an index of the log's events, advanced
    incrementally so a request audits only what changed since the last one."""

    def __init__(self) -> None:
        self._log_ref: Optional[SessionLog] = None
        self._seq = -1  # last event seq folded into the index
        self._index = _empty_index()

    def check(
        self,
        log: SessionLog,
        messages: list[dict[str, Any]],
        *,
        compaction_active: bool = False,
    ) -> list[str]:
        """Advance the index over events appended since the last check, then audit
        `messages`. An unusable log audits as vacuously clean — there is nothing to
        check against, and the failure to write is the log's own report, not ours."""
        if not log.enabled:
            return []
        if log is not self._log_ref:
            # A fresh SessionLog under the same engine (attach on session start) resets
            # the watermark: the index is defined over one file, whatever preceded it.
            self._log_ref = log
            self._seq = -1
        new_events = log.read() if self._seq < 0 else log.read(after=self._seq)
        if self._seq < 0:
            self._index = _empty_index()
        _index_events(self._index, new_events)
        if new_events:
            self._seq = max(
                (int(e["seq"]) for e in new_events if isinstance(e.get("seq"), int)),
                default=self._seq,
            )
        violations = audit_messages(messages, self._index)
        if compaction_active and not self._index["summary"]:
            violations.append(
                "compacted block in the outbound view with no compaction/summary event"
            )
        return violations
