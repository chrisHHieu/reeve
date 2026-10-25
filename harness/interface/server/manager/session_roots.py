"""Sessions as a tree: forks, child sessions, subagent runs and their lifecycle.

The session's directory roots live in `roots.py`; this mixin holds what a session does with
other sessions.
"""
from __future__ import annotations

import logging

import asyncio
import json
import shutil
import uuid
from .helpers import last_assistant_text
from .roots import RootsMixin
from harness.compose import build_engine
from harness.config.config import load_config
from harness.safety.permissions import Mode
from harness.session.conversations import title_from
from harness.session.session_log import SessionLog
from harness.session.sessions import SessionRecord
from pathlib import Path

import threading
import time
from typing import Any, Callable, Optional


from harness.tools.contract import clip
from harness.orchestration.child_changes import changed_files
from harness.tools.task import BACKGROUND_MOVED, BACKGROUND_STARTED, FOREGROUND_WAIT_SECONDS as _FOREGROUND_WAIT_SECONDS, QUEUED, changes_of, render_output, with_partial
from harness.tools.task_message import name_problem
from harness.tools.workflow import WORKFLOW_RUNNER, WORKFLOW_TAKES_NO_MESSAGES
from .subagent_messages import deliver_to_child, effort_problem, name_taken, parent_source, remember_name
from harness.tools.task_agents import DENY, AgentInfo, child_permission, disabled_tools, evaluate, resolve_agents, rule_decider, subagent_depth_limit, subagent_parallel_limit
from harness.tools.capture import capture_correction, capture_decider, capture_instruction, capture_tool
from harness.tools.json_schema import parse_structured, validate_output_schema
from harness.tools.task_runtime import CAP_PROMPT, child_rules
from harness.tools.todo import TODO_WRITE, todo_tools

logger = logging.getLogger("harness.manager")


#: The base prompt a child runs under when its agent definition declares none.
#: An agent's `prompt` REPLACES the base prompt, so `general`
#: and `explore` carry their own and this is only the fallback for a config-declared
#: agent that wrote none.
CHILD_BASE_PROMPT = (
    "You are a subagent working on one task delegated by another agent. You cannot see "
    "the conversation that spawned you — the task text is everything you know. Work "
    "autonomously, verify what you claim with your tools, and end with one final message "
    "holding your findings and anything the caller must know."
)

#: How long a foreground spawn waits for its child before handing it over to the background:
#: the `task` call's own rail, one minute under the budget its contract declares.
#: Past it the alternative is not "keep waiting" — the engine stops waiting at that budget
#: and the child would run on with nobody to receive its result — so the child becomes a
#: background job and its completion is injected like any other. Zero disables the hand-over.
#:
#: This was 120 s (adopted 2026-09-23), which turned a call the
#: model made BECAUSE it needed the answer into a notice that it had none. Real store,
#: 2026-10-04: 29 of ~120 foreground calls came back `running`, 14 of them the `verify`
#: subagent (14 of its 17 calls); in 11 the parent then touched the child's own files, in 9
#: it edited files while the child was still working, and 9 children finished within 25 s of
#: the notice. Waiting to the rail would have returned 27 of the 29 in the foreground.
FOREGROUND_WAIT_SECONDS = _FOREGROUND_WAIT_SECONDS
#: How long a foreground spawn past the cap waits for a slot: the call's rail less a minute,
#: so a child that gets its slot late still starts before the call is handed over.
SLOT_WAIT_SECONDS = max(0.0, FOREGROUND_WAIT_SECONDS - 60.0)
#: How long a background child may run before it is stopped (orchestration audit O7).
#: `[delegation] background_timeout_minutes` overrides it; 0 turns the clock off.
BACKGROUND_TIMEOUT_MINUTES = 30.0

#: Spawns allowed per parent turn (a hard cap, same date): a model that loops on
#: `task` burns the whole roster on one answer. Reset with the other per-turn guards.
MAX_SUBAGENTS_PER_TURN = 50

#: Tools a child must never hold, whatever its ruleset says. The
#: ruleset itself adds only the `todo_write`/`task` denies — mirrored by
#: `child_permission` — and its `todowrite`/`task` gates, so these are the interactive
#: doors plus the spawn tool itself: `ask_user` (a child has no person to ask),
#: `propose_plan` (nobody to approve one), `request_tool` (a child works where its parent
#: works and must not widen that), and
#: `task`, which `_never_tools` removes unless `subagent_depth` opens nesting for this
#: child (P5). `disabled_tools` cannot see them — the default ruleset is `*: allow`, so
#: nothing names them — which is why they are removed by name, on top of the ruleset. The
#: config's `primary_tools` is unioned in per
#: build.
_CHILD_NEVER_TOOLS = frozenset(
    {
        "task",
        "ask_user",
        "request_tool",
        "propose_plan",
    }
)

#: Tools that only mean something beside `task`.
TASK_COMPANIONS = ("workflow", "list_subagent_models", "message_task")

#: A `parent_session` chain is a few hops deep at most (`subagent_depth`); the cap only
#: stops a corrupt row that points at itself from walking forever (`trace_of`).
_TRACE_WALK_LIMIT = 32


def _with_looks(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The thread as a surface draws it: a tool result saved before calls carried `_looks` (ADR
    0204) gets it by the engine's rule, from the kind its stored presentation names and its tool's
    declared class, so an older session folds its reads as a new one does. A shell command that only
    read cannot be told after the fact and stays a row. The stored messages are not touched; the
    call a result answers is the newest one before it with its id."""
    from harness.safety.risk import RiskClass, declared_class
    from harness.tools.presentation import LOOKING_KINDS

    names: dict[str, str] = {}
    shown: list[dict[str, Any]] = []
    for message in messages:
        if message.get("role") == "assistant":
            for call in message.get("tool_calls") or []:
                names[str(call.get("id"))] = str((call.get("function") or {}).get("name") or "")
        elif message.get("role") == "tool" and "_looks" not in message:
            presented = message.get("_presentation")
            name = names.get(str(message.get("tool_call_id")), "")
            if (
                isinstance(presented, dict)
                and presented.get("kind") in LOOKING_KINDS
                and declared_class(name) is RiskClass.READ
            ):
                message = {**message, "_looks": True}
        shown.append(message)
    return shown


def _never_tools(config: Any, *, allow_task: bool = False) -> set[str]:
    """The names no child may hold: the fixed set above plus the config's `primary_tools`.

    `allow_task` (P5) drops `task` from the never-set for a child whose depth is below
    `subagent_depth` — nesting is bounded by the depth check
    rather than by hiding the tool. The child still only gets it if its OWN ruleset opts
    in (`child_permission` denies `task` unless the agent named it) and it is not in
    `primary_tools`, which is unioned after the discard on purpose: a tool the user
    reserved for the primary session stays reserved at every depth.
    """
    configured = getattr(config, "primary_tools", None) or []
    never = set(_CHILD_NEVER_TOOLS) | {
        str(name).strip() for name in configured if str(name).strip()
    }
    if allow_task:
        never.discard("task")
    return never


def _child_agent(agent: AgentInfo, *, subagents: bool = False) -> Any:
    """An `Agent`-shaped view of a subagent definition for `build_engine`.

    A child is NOT a persona, so this carries no persona traits: `build_engine` wires
    none of the persona-only surfaces (subagents, scheduling, messaging, connectors,
    team). Its `build_tools` is the FALLBACK registry — the file/search/git/shell/web set
    the thread harness composed — used only when the parent session has no live engine to
    copy (a direct `spawn_subagent` caller; a manager spawn from a running turn always
    seeds `build_engine` with `parent.registry.filtered(...)`).

    `subagents=True` (P5, `depth < subagent_depth`) lets `build_engine` register the
    `task` tool for this child too; the merged `child_permission` ruleset still decides
    whether the agent may actually call it.
    """
    from harness.modes.agents import Agent, AgentContext
    from harness.modes.catalog import expand

    def build_tools(context: AgentContext) -> list:
        return expand(["code_files", "search", "shell"], context)

    return Agent(
        name=agent.name,
        title=agent.name,
        system_prompt=agent.prompt or CHILD_BASE_PROMPT,
        tool_factory=build_tools,
        subagents=subagents,
        # The definition's own answer to "append AGENTS.md?" rides into `build_engine`,
        # which is where the project conventions are read. Default True for every agent
        # that did not write `inject_agents: false`.
        inject_agents=getattr(agent, "inject_agents", True),
    )


def _has_worktree_dir(root: Optional[Path], child_id: str) -> bool:
    """Whether a child's worktree folder is on disk: one stat, no git call per child."""
    from harness.orchestration.worktrees import CHILD_WORKTREE_DIR

    return root is not None and (root / CHILD_WORKTREE_DIR / f"subagent-{child_id}").is_dir()


def _structured_report(
    engine: Optional[Any], start: int, schema: Optional[dict[str, Any]]
) -> tuple[str, list[str]]:
    """`(report, errors)` for a finished in-process child turn.

    Without a schema this is just the last assistant text (errors empty). With one, a
    COMMITTED capture (the child called `capture_result`) is authoritative and becomes
    the report verbatim; otherwise the reply is parsed and validated as a fallback: a
    valid object becomes the report as compact JSON, and the errors drive the single
    corrective retry (and the failure if it survives)."""
    import json

    messages = getattr(engine, "messages", None) if engine is not None else None
    window = messages[start:] if messages else []
    text = last_assistant_text(window) or ""
    if not schema:
        return text, []
    captured = _captured_value(engine)
    if captured is not None:
        return json.dumps(captured, ensure_ascii=False), []
    value, errors = parse_structured(schema, text)
    if errors:
        return text, errors
    return json.dumps(value, ensure_ascii=False), []


def _captured_value(engine: Optional[Any]) -> Optional[Any]:
    """The object the child committed through `capture_result`, if it did."""
    sink = getattr(engine, "captured_output", None)
    if isinstance(sink, dict) and sink.get("value") is not None:
        return sink["value"]
    return None


def _usage_total(messages: list[dict[str, Any]]) -> int:
    """Tokens the child spent on the turn — the same `TokenUsage.total` fold the thread
    harness reports, so the parent's budget counts a fan-out identically either way."""
    from harness.llm.providers.base import TokenUsage

    total = 0
    for message in messages:
        usage = message.get("usage") if isinstance(message, dict) else None
        if isinstance(usage, dict):
            total += TokenUsage(
                **{
                    key: int(usage.get(key) or 0)
                    for key in ("input", "output", "cache_read", "cache_write")
                }
            ).total
    return total


def _child_duration_ms(engine: Optional[Any], start: int = 0) -> int:
    """The child's elapsed time: last assistant stamp − first user stamp.

    A task card's duration is computed from exactly this pair off the child session's own
    messages, which is why the manager reads
    it from the transcript instead of timing the tool call: the tool's wall clock also
    covers the engine build and the broker ask, and the card would claim work the child
    never did. `start` bounds the window to THIS turn (a resume has older history). 0 when
    the child produced no stamped pair — the row then shows no duration rather than a
    number that means nothing.
    """
    messages = getattr(engine, "messages", None) if engine is not None else None
    if not messages:
        return 0
    window = messages[start:] if start <= len(messages) else messages
    first = next(
        (
            m.get("ts")
            for m in window
            if isinstance(m, dict)
            and m.get("role") == "user"
            and isinstance(m.get("ts"), (int, float))
        ),
        None,
    )
    last = next(
        (
            m.get("ts")
            for m in reversed(window)
            if isinstance(m, dict)
            and m.get("role") == "assistant"
            and isinstance(m.get("ts"), (int, float))
        ),
        None,
    )
    if first is None or last is None or last < first:
        return 0
    return int((last - first) * 1000)


def _close_engine_quietly(engine: Any) -> None:
    """Release a replaced child engine's shell. A child's engine is rebuilt on every
    resume, so keeping the old one would strand its shell process — the thread harness
    closes its child executor after every run for the same reason."""
    closer = getattr(getattr(engine, "executor", None), "close", None)
    if callable(closer):
        try:
            closer()
        except Exception:
            pass
    _close_engine_terminals(engine)


def _close_engine_terminals(engine: Any) -> None:
    """End whatever terminals an owner still holds (a finished child turn, a deleted
    session): each is a live process tree, and the rule is that they die
    with the owner. Best-effort — teardown must never fail the thing it is cleaning up."""
    world = getattr(engine, "world", None)
    closer = getattr(world, "close_terminals", None)
    if callable(closer):
        try:
            closer()
        except Exception:  # noqa: BLE001
            pass


def _emit_metadata(
    callback: Optional[Callable[[dict[str, Any]], None]], metadata: dict[str, Any]
) -> None:
    """Call a child's metadata observer without ever letting it fail the child run.

    The observer's only job is a display frame for the parent's `task` row (the
    tool's metadata), so a raising collector or a dead emitter is a UI problem, never a
    turn problem — the same best-effort rule tool broadcasts already follow.
    """
    if callback is None:
        return
    try:
        callback(metadata)
    except Exception:
        pass


def _resolve_promotion(future: Any) -> None:
    """Resolve a parked promotion future ON its own loop (`call_soon_threadsafe`).

    A no-op when the future already settled (the child finished as the promote arrived) —
    the waiting spawn has then already returned the foreground result and nobody owns the
    future any more.
    """
    if not future.done():
        future.set_result(True)


def _short_tool_title(arguments: Any) -> str:
    """A one-line title for a child tool with no declared presenter: its arguments,
    clipped. Empty for a call with no arguments — the row then shows just the name."""
    if not isinstance(arguments, dict) or not arguments:
        return ""
    try:
        text = json.dumps(arguments, ensure_ascii=False, default=str)
    except Exception:
        text = str(arguments)
    return clip(text, 120)


def _fork_safe_prefix(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The longest prefix of a transcript that can be sent to a provider on its own.

    A transcript is persisted mid-step all the time (crash, engine eviction, a stop), and
    a prefix whose tail is an assistant message with UNANSWERED tool calls — or a tool
    result whose call was cut away — is a request no hosted provider accepts (the same
    orphan rule `_order_tool_responses` enforces on the live path). So the tail is walked
    back over display-only notices and any unanswered tool-call run; everything before is
    a legal history for a fresh session's first request.

    A tail of answered tool results is kept: that is an ordinary mid-loop state the model
    continues from.
    """
    cut = len(messages)
    while cut > 0 and messages[cut - 1].get("role") == "notice":
        cut -= 1
    while cut > 0:
        last = messages[cut - 1]
        if last.get("role") != "assistant" or not last.get("tool_calls"):
            break
        answered = {
            m.get("tool_call_id") for m in messages[:cut] if m.get("role") == "tool"
        }
        if all(tc.get("id") in answered for tc in last["tool_calls"]):
            break
        cut -= 1
        while cut > 0 and messages[cut - 1].get("role") == "notice":
            cut -= 1
    return messages[:cut]


#: What a forked child carries from the parent's transcript is text history, not media:
#: an engine attachment (a `read_image` picture, a fetched PDF) rides a sidecar-tagged user
#: message, and copying it into a child would re-send those bytes on EVERY child request.
#: The note keeps the fact ("an image existed here") without the payload; the child can
#: re-fetch or re-read what it needs.
_FORK_ATTACHMENT_NOTE = (
    "[an attachment from the parent conversation is not carried into this fork]"
)


def _fork_seed(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The history a fresh `task(inherit_context=true)` child starts from.

    `_fork_safe_prefix` makes the prefix provider-legal; this additionally replaces each
    engine attachment message with its text half plus a note (see
    `_FORK_ATTACHMENT_NOTE`) and strips the runtime sidecars the child must not inherit
    (`_display` is user-facing filter metadata the model must never see, `_attach` is the
    payload being dropped, `_card_meta` belongs to the parent's cards).
    """
    from harness.session.turns import ATTACHED_BY

    seed: list[dict[str, Any]] = []
    for message in _fork_safe_prefix(messages):
        if ATTACHED_BY in message:
            content = message.get("content")
            kept = (
                [
                    str(part.get("text"))
                    for part in content
                    if isinstance(part, dict)
                    and part.get("type") == "text"
                    and part.get("text")
                ]
                if isinstance(content, list)
                else []
            )
            seed.append(
                {
                    "role": "user",
                    "content": "\n".join([*kept, _FORK_ATTACHMENT_NOTE]),
                }
            )
            continue
        seed.append(
            {
                key: value
                for key, value in message.items()
                if key not in ("_display", "_attach", "_card_meta")
            }
        )
    return seed



def _sibling_overlaps(
    parent_engine: Optional[Any], child_id: str, files: list[dict[str, Any]]
) -> list[dict[str, str]]:
    """Paths this child changed that another child of the same parent turn changed too.

    Children share the parent's folder unless worktree isolation is on, and a
    later write to the same file silently replaces an earlier one (orchestration audit O4).
    Kept on the parent engine and reset with its fan-out counter each turn; a worktree
    child's paths live under its own tree, so they never collide with a shared-folder one.
    """
    if parent_engine is None:
        return []
    seen = getattr(parent_engine, "_children_changed", None)
    if not isinstance(seen, dict):
        seen = {}
        parent_engine._children_changed = seen
    mine = {str(f.get("path")) for f in files if f.get("path")}
    overlaps = [
        {"path": path, "task_id": other}
        for other, paths in seen.items()
        if other != child_id
        for path in sorted(mine & paths)
    ]
    seen[child_id] = set(seen.get(child_id) or set()) | mine
    return overlaps

class SessionRootsMixin(RootsMixin):
    def trace_of(self, session_id: str) -> str:
        """The task chain's id for this session.

        A chain begins at the session that STARTED the work and includes everything it
        spawns: subagent children (any depth), background runs, external CLI children,
        retries. It is named by that session's id rather than a second opaque uuid,
        because an id nobody can resolve points at nothing: a reader holding a child row
        can walk straight to the session whose transcript says why the chain exists.

        The walk climbs `parent_session` while the row is a `subagent` and stops at the
        first session that is user-visible — a root, or a FORK, whose `parent_session` is
        lineage rather than containment. Branching a conversation starts a new
        chain, so a fork's own descendants inherit the fork.
        """
        current = session_id
        for _ in range(_TRACE_WALK_LIMIT):
            record = self.session_store.load(current)
            if record is None or not getattr(record, "parent_session", None):
                break
            if (getattr(record, "kind", None) or "root") != "subagent":
                break
            current = record.parent_session
        return current

    def session_log(self, session_id: str) -> "SessionLog":
        """One session's event log, cached so its append counter is not re-read per event."""
        log = self._session_logs.get(session_id)
        if log is None:
            log = SessionLog(
                self.session_store.conv_dir / f"{session_id}.events.jsonl",
                trace=self.trace_of(session_id),
            )
            self._session_logs[session_id] = log
        return log

    def session_events(self, session_id: str, *, limit: Optional[int] = None) -> list[dict[str, Any]]:
        """The session's observed events, oldest first - what the metrics fold reads."""
        return self.session_log(session_id).read(limit=limit)

    def session_metrics(self, session_id: str) -> dict[str, Any]:
        """Token/latency/retry/tool efficiency folded from the session's event log.

        A fold, not a new recorder: every number comes from events the engine already
        writes, so asking for metrics can never change what a turn does. The session's
        own model is passed along so the report can carry a USD estimate (None when the
        price sheet does not know the model)."""
        from harness.observability.metrics import summarize

        record = self.session_store.load(session_id)
        model = str(getattr(record, "model", "") or "") if record else ""
        return summarize(self.session_events(session_id), model=model or None)

    def session_plan(self, session_id: str) -> dict[str, Any]:
        """The session's working plan file (`update_plan`), or an empty document.

        Read through the live engine when there is one — it already resolved the path
        against this session's roots — else re-derived with the same helper `build_engine`
        uses, so a session with no live engine still answers. A missing file is an empty
        plan, not an error: the tool may simply never have been called.
        """
        from harness.tools.plan_file import plan_path_for, read_plan

        record = self.session_store.load(session_id)
        if record is not None and record.environment_id:
            # The plan file lives inside a session root; a remote session's roots are on
            # the other host and `update_plan` is not registered for it (phase 2b-1).
            return {"path": None, "content": "", "exists": False, "chars": 0}
        engine = self._engines.get(session_id)
        path = getattr(engine, "plan_path", None) if engine is not None else None
        if path is None:
            path = plan_path_for(self.get_roots(session_id), session_id)
        content = read_plan(path)
        return {
            "path": str(path) if path is not None else None,
            "content": content,
            "exists": bool(path is not None and Path(path).is_file()),
            "chars": len(content),
        }

    def session_messages(self, session_id: str) -> list[dict[str, Any]]:
        # A live engine's in-memory thread is authoritative: mid-turn it's ahead of the
        # persisted record — which may not even exist yet for a scheduled run's first turn
        # (opening a "running" automation showed a blank session; owner report 2026-07-04).
        engine = self._engines.get(session_id)
        if engine is not None:
            return _with_looks(list(engine.messages))
        record = self.session_store.load(session_id)
        return _with_looks(record.messages) if record else []

    async def session_compact_now(self, session_id: str) -> dict[str, Any]:
        """Condense a session's history on demand (`/compact`).

        Refused mid-turn for the same reason tool undo is — the outbound view is being
        built right now — and the session's running mark is held across the summarizer
        call, so a message that arrives while it runs queues behind it instead of racing
        the compaction for the same history. Success is made durable so a reopen keeps
        the condensed view.
        """
        if self.is_running(session_id):
            return {
                "ok": False,
                "error": "the session is mid-turn - try again once it settles",
            }
        engine = self._engines.get(session_id)
        if engine is None:
            return {"ok": False, "error": "this session is not open"}
        if not hasattr(engine, "compact_now"):
            return {"ok": False, "error": "this surface cannot compact"}
        # Every view of the session hears that it is busy, not only the one that asked: the
        # running mark refuses a message sent meanwhile, so a view that does not know queues
        # nothing and the message is turned away (owner, 2026-10-13). `manual` tells a view
        # that this one ends on `compacted` or `compact_failed`, not on the next frame.
        await self.broadcast_session(session_id, {"type": "compacting", "data": {"manual": True}})
        self.mark_running(session_id)
        began = time.monotonic()
        try:
            result = await engine.compact_now()
        except Exception as error:  # noqa: BLE001 - the views must hear that it ended
            result = {"ok": False, "error": "the summarizer could not condense this history", "detail": str(error)[:300]}
        finally:
            self.mark_idle(session_id)
        seconds = round(time.monotonic() - began, 1)
        if not result.get("ok"):
            await self.broadcast_session(
                session_id,
                {
                    "type": "compact_failed",
                    "data": {
                        "error": result.get("error", ""),
                        "detail": result.get("detail", ""),
                        "nothing": result.get("reason") == "nothing",
                    },
                },
            )
            # The view that asked has been told through the broadcast; it must not say it twice.
            result = {**result, "announced": True}
        if result.get("ok"):
            self.save(session_id, engine)
            # Parity with the automatic path: a live viewer gets the marker now instead
            # of only on the next reload (the WS handler appends the notice line), and the
            # NUMBERS ride along — `now_tokens` is what moves the usage meter without
            # waiting for another turn, and `summary` is what the marker discloses.
            await self.broadcast_session(
                session_id,
                {
                    "type": "compacted",
                    "data": {
                        "text": result.get("text", ""),
                        "reason": result.get("reason"),
                        "saved_tokens": result.get("saved_tokens"),
                        "saved_pct": result.get("saved_pct"),
                        "now_tokens": result.get("now_tokens"),
                        "summarizer_tokens": result.get("summarizer_tokens"),
                        "summary": result.get("summary", ""),
                        "seconds": seconds,
                    },
                },
            )
        return result

    def fork_session(self, session_id: str) -> dict[str, Any]:
        """Branch a session into a new one seeded with its history.

        The new session begins with the longest prefix of the parent's transcript that a
        provider will accept on its own (`_fork_safe_prefix`), inherits the route, mode,
        workspace, thinking level and project bindings, and records its lineage. The
        parent is never touched.

        What it deliberately does NOT inherit: the session-scoped "Always allow"
        approvals (authority the user granted in one conversation is not authority in
        another — the same rule that keeps grants session-scoped in the first place) and
        the compaction projection (its boundary indexes index the PARENT's message list;
        the child is smaller and re-compacts on its own if it ever needs to).
        """
        if self.is_running(session_id):
            return {
                "ok": False,
                "error": "the session is mid-turn — fork it once the turn settles",
            }
        parent = self.session_store.load(session_id)
        if parent is None:
            return {"ok": False, "error": "this session is not known"}
        messages = _fork_safe_prefix(parent.messages)
        if not messages:
            return {"ok": False, "error": "nothing to carry over yet — send a message first"}

        new_id = uuid.uuid4().hex[:12]
        parent_title = parent.title or title_from(parent.messages) or "session"
        title = f"{parent_title[:100]} · fork"
        self.session_store.save(
            SessionRecord(
                session_id=new_id,
                workspace=parent.workspace,
                model=parent.model,
                mode=parent.mode,
                messages=messages,
                title=title,
                agent=parent.agent or "code",
                extra_roots=list(parent.extra_roots or []),
                grants={},  # not inherited: consent does not fork with the transcript
                compaction={},  # boundary indexes belong to the parent's list
                bindings=dict(parent.bindings or {}),
                thinking=parent.thinking,
                plan=parent.plan,
                # A fork of a remote session stays remote: `parent.workspace` is a POSIX
                # path on the other host, and a fork that lost the binding would resolve
                # it locally (or refuse the session start).
                environment_id=parent.environment_id,
                parent_session=session_id,
                seed_length=len(messages),
            )
        )
        return {
            "ok": True,
            "session_id": new_id,
            "title": title,
            "parent_session": session_id,
            "messages": len(messages),
        }

    def create_child_session(
        self,
        parent_id: str,
        description: str,
        agent: str,
        model: str,
        *,
        seed_messages: Optional[list[dict[str, Any]]] = None,
        isolation: str = "",
        agent_info: Optional[AgentInfo] = None,
    ) -> str:
        """Create the session row for a `task`-spawned subagent, and return its id.

        The shape is a session row with a parent, a title, an agent and a model
        (step A): the child is an ORDINARY session row that every roots
        listing hides. `parent_session` alone cannot be the marker — `fork_session`
        writes lineage into the same column and a fork stays a visible root — so
        `kind="subagent"` is what distinguishes containment from ancestry. The title
        is `"<description> (@<agent> subagent)"`.

        The row inherits the parent's folder, mode and added roots (a child works where
        its parent works), but NOT its grants or compaction view — consent does not
        descend, and the parent's boundary indexes index the parent's message list.

        `seed_messages` (a fork of the parent's history) starts the child
        from a copy of the parent's transcript instead of an empty one; `seed_length`
        records how much of the child's history was inherited so a later boundary can tell
        the fork point. The default stays an empty child for every existing caller.
        """
        parent = self.session_store.load(parent_id)
        session_id = uuid.uuid4().hex[:12]
        seed = list(seed_messages) if seed_messages else []
        # Worktree isolation: an opt-in per-child git worktree, so parallel
        # children cannot fight over the same files. `None` (the default) keeps the
        # parent's folder, byte-identical to before.
        base_workspace = (
            (parent.workspace if parent and parent.workspace else None)
            or self._provision_scratch(session_id)
        )
        worktree = self._child_worktree_for(parent, session_id, isolation, agent_info)
        self.session_store.save(
            SessionRecord(
                session_id=session_id,
                workspace=(worktree["path"] if worktree else base_workspace),
                model=model or (parent.model if parent else self.model),
                mode=parent.mode if parent else self.mode.value,
                messages=seed,
                title=f"{description} (@{agent} subagent)",
                agent=agent or (parent.agent if parent else "code"),
                extra_roots=list(parent.extra_roots or []) if parent else [],
                grants={},
                compaction={},
                # The plan overlay rides the mode: a child is bound by the same
                # rehearsal the parent is.
                plan=parent.plan if parent else False,
                # The child works where its parent works: a remote parent's binding is
                # part of the location, and without it the POSIX workspace would be
                # resolved on the controller.
                environment_id=parent.environment_id if parent else None,
                parent_session=parent_id,
                kind="subagent",
                seed_length=len(seed),
            )
        )
        return session_id

    def _child_worktree_for(
        self,
        parent: Optional[SessionRecord],
        child_id: str,
        isolation: str = "",
        agent: Optional[AgentInfo] = None,
    ) -> Optional[dict[str, Any]]:
        """A per-child git worktree, when the delegation is isolated: the call
        asked, its agent's definition does, or `[delegation] worktree_isolation` is on
        (`wants_worktree`).

        Off by default. Only a local child of a git workspace can have one: a remote
        child works on the other host, and a non-repo has nothing to isolate. Every
        failure path falls back to the parent's workspace — isolation that cannot be set
        up must not cost the spawn. When the CALL or the definition asked, the reason is
        kept for the result (`_worktree_unavailable`): the caller planned on a separate
        copy and must learn the child wrote into the shared folder instead.
        """
        asked = isolation == "worktree" or getattr(agent, "isolation", "") == "worktree"
        if parent is None or not parent.workspace or parent.environment_id:
            if asked:
                self._worktree_unavailable[child_id] = (
                    "a remote session has no local worktree" if parent and parent.environment_id
                    else "the session has no folder"
                )
            return None
        if not self.wants_worktree(parent, isolation, agent):
            return None
        from harness.orchestration.worktrees import create_child_worktree

        created = create_child_worktree(parent.workspace, child_id)
        if created.get("error"):
            logger.debug(
                "worktree isolation skipped for %s: %s", child_id, created["error"]
            )
            if asked:
                self._worktree_unavailable[child_id] = str(created["error"])
            return None
        self._child_worktrees[child_id] = created
        logger.info(
            "worktree isolation: %s works in %s (%s)",
            child_id,
            created["path"],
            created["branch"],
        )
        return created

    def _release_child_worktree(self, session_id: str) -> None:
        """Release a finished child's worktree — only when it holds no work.

        Called from the shared idle moment (`mark_idle`), so it covers every child path
        (foreground, background, external CLI). A worktree with uncommitted changes or
        with commits is KEPT and the entry stays, so a later idle retries after the
        person has taken the work; proof of emptiness is the only licence to remove.
        """
        info = self._child_worktrees.get(session_id)
        if not info:
            return
        from harness.orchestration.worktrees import remove_child_worktree

        result = remove_child_worktree(info)
        if result.get("removed"):
            self._child_worktrees.pop(session_id, None)
            logger.info("worktree isolation: released %s", info.get("path"))
        else:
            logger.info(
                "worktree isolation: kept %s (%s)",
                info.get("path"),
                result.get("reason"),
            )

    def _resolve_child_remote(
        self, child_id: str, child: Optional[SessionRecord]
    ) -> tuple[Optional[dict[str, Any]], Optional[Any], Optional[str], bool]:
        """Resolve a child's inherited SSH binding for `build_engine`.

        Returns `(profile, runner, password, is_remote)`. Local children answer
        `(None, None, None, False)` — the byte-identical old path. A binding whose
        profile vanished raises the manager's typed error, exactly as `get_engine` does
        for a root session, so a child cannot silently fall back to the controller's
        disk. `password` is the profile's remembered secret, loaded through the OS
        credential store (password auth); None means key-only.
        """
        environment_id = getattr(child, "environment_id", None) if child else None
        if not environment_id:
            return None, None, None, False
        store = self.remote_environments
        if store is None:
            raise ValueError(
                f"child session {child_id} is bound to remote environment "
                f"{environment_id!r}, but this host has no remote-environment store"
            )
        try:
            profile = store.get(environment_id)
        except KeyError:
            raise ValueError(
                f"child session {child_id} is bound to remote environment "
                f"{environment_id!r}, which no longer exists"
            ) from None
        password = store.load_password(environment_id)
        return profile, getattr(store, "_runner", None), password, True

    def children_of(self, parent_id: str) -> list[str]:
        """The ids of `parent_id`'s child sessions, in creation order.

        A FORK is deliberately absent: it shares the `parent_session` column as lineage
        but its `kind` is "root", so it is an independent session, not a descendant."""
        return self.session_store.children_of(parent_id)

    def is_child_session(self, session_id: str) -> bool:
        """True when `session_id` names a `task`-spawned child session.

        A child is not a workspace conversation: title generation is skipped for
        `parentID` sessions, and none of the `mark_idle` lifecycle hooks (autotitle, goal
        rounds, promotion rebuild) has anything to do for one. One cheap store probe, because every hook on that shared
        idle moment asks the same question."""
        return self.session_store.is_child(session_id)

    def children_view(self, parent_id: str) -> list[dict[str, Any]]:
        """The direct `kind="subagent"` children of a session, for the API (P3).

        The listing is the reduced `{session_id, title, agent, status}` shape the sidebar
        nests and the child
        view walks. `status` comes from the manager's running set — the same fact a
        `session_status` frame carries — so a client that never heard the child's
        `session_created` (a reloaded window) still draws the row correctly.
        """
        out: list[dict[str, Any]] = []
        children = self.children_of(parent_id)
        worktree_root = self._worktree_root(parent_id) if children else None
        for child_id in children:
            record = self.session_store.load(child_id)
            if record is None:
                continue  # deleted between the link query and the row read
            out.append(
                {
                    "session_id": record.session_id,
                    "title": record.title or "",
                    "agent": record.agent or "",
                    "status": "busy" if self.is_running(child_id) else "idle",
                    # Its own copy still exists: the client asks what it holds.
                    **({"worktree": True} if _has_worktree_dir(worktree_root, child_id) else {}),
                }
            )
        return out

    def _subagent_metadata_emitter(self, session_id: str) -> Callable[[dict], None]:
        """The parent-session frame emitter a spawned child's live metadata rides
        (step C).

        The `task` tool runs on a worker thread, while the frame must go out through the
        server loop's broadcaster, so it captures the loop and hands the work over:
        the loop is captured where the engine is built (`get_engine` runs inside the
        turn's coroutine; a bare manager in tests falls back to None) and the worker
        thread hands the coroutine over with `run_coroutine_threadsafe`. Best-effort
        throughout — a frame that cannot be delivered must not fail a child run.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        def emit(frame: dict) -> None:
            if loop is None or not loop.is_running():
                return
            try:
                asyncio.run_coroutine_threadsafe(
                    self.broadcast_session(session_id, frame), loop
                )
            except RuntimeError:
                pass

        return emit

    async def spawn_subagent(
        self,
        *,
        parent_id: str,
        description: str,
        prompt: str,
        agent_name: str,
        background: bool = False,
        task_id: Optional[str] = None,
        inherit_context: bool = False,
        output_schema: Optional[dict[str, Any]] = None,
        origin: str = "",
        provider: str = "",
        model: str = "",
        on_metadata: Optional[Callable[[dict[str, Any]], None]] = None,
        agent_info: Optional[AgentInfo] = None,
        isolation: str = "",
        name: str = "",
        effort: str = "",
        held_slot: str = "",
    ) -> dict[str, Any]:
        """Spawn (or resume) a `task`-spawned subagent as a real child session.

        The child is created with `create_child_session`, its engine is
        built by the same `build_engine` path a session uses — the agent definition's
        prompt REPLACES the base prompt, and its model/temperature/steps apply — and its
        tools are filtered by the merged ruleset (`child_permission` + the agent's own
        rules). The turn is then driven by `_drive_turn`, the same driver
        `deliver_to_session` uses, so the child's messages persist in ITS OWN
        conversation, its events broadcast under its own session id, and checkpoints
        apply exactly as they do to a user turn.

        `task_id` resumes a previous child: the conversation is loaded from the store and
        the engine rebuilt with it, so context survives a process restart — unlike the
        thread harness's ≤3 in-memory engines. A `task_id` whose child is STILL AT WORK is
        not resumed: the prompt is given to it (`deliver_to_child`) — into the
        turn it is in, or as the next turn of its background job — and the one result the
        caller was promised covers it.

        `name` is what a fresh child is called by later (`message_task`); one name
        per session. `effort` is a fresh in-process child's thinking level, one its model
        offers; it is kept on the child row, so a resume thinks the same way.

        `inherit_context` seeds a FRESH child with the parent's transcript (`_fork_seed`
        keeps the text history and drops attachment payloads). It has no effect on a resumed
        child, which already carries its own context.

        `output_schema` (object-rooted JSON Schema subset) makes the child's final answer
        a VALIDATED object: the instruction is appended to the prompt, the reply is
        parsed and checked, one corrective retry is allowed, and a still-invalid answer
        fails the delegation. In-process agents only — an external CLI child would pay a
        whole second run to correct JSON, so it is refused up front.

        `on_metadata` (step C) is the `task` tool's best-effort observer for the
        parent's running row — the frame shape P2 settled on:

        - creation: `{"session_id": child_id, "parent_session_id": parent_id,
          "title": description, "toolcalls": 0, "started_at": <epoch s>, "model":
          <the child row's model>, "background": <bool>}`, plus `"job_id": child_id` on a
          background spawn;
        - in line for a child slot, before any child exists: `{"waiting": {"running",
          "limit"}}`, with `"background": true` when the call was queued and returned;
          the creation frame ends it;
        - a child tool proposal: `{"session_id", "tool", "title", "toolcalls", "phase"}`;
        - what the child is doing between its tools: `{"session_id", "phase":
          "thinking" | "writing"}`, once per change, and after each of its steps
          `{"session_id", "tokens": <its spend so far>}`;
        - a child retry: `{"session_id", "retry": {"attempt", "message"}}` while the
          provider call goes again, and a `"retry": null` frame once the same request
          produces something (the `↳ Retrying (attempt N) · msg` row);
        - settle, once at the end of the turn: `{"session_id", "toolcalls", "ended_at":
          <epoch s>, "duration_ms": <child's last assistant − first user, ms>}`. The
          duration rides this frame and not the tool result because the settled row has no
          other carrier for it, and it is measured from the child's own transcript rather
          than the tool call's wall clock, which also covers the engine build and the spawn
          ask.

        The tool wraps each frame into the parent's `tool_metadata` frame. The child's life
        cycle is also announced on the app-wide stream, so every client (not only the
        parent's viewers) can list it: `session_created` with `{session_id,
        parent_session, title, agent, model, kind: "subagent"}`, and `session_status` with
        `{session_id, status}` busy on claim / idle on finish. Parent sessions keep today's
        behaviour — they emit no `session_status` — so the child events are purely additive.

        `background=True` (step D) returns IMMEDIATELY with the running
        envelope while the child's turn stays in the manager's registry
        (`_background_tasks`) driven as a detached task. When it ends, the finished
        envelope is injected into the parent as a synthetic message
        (`_inject_background_result`) and the
        registry entry is cleared. A user Stop walks the parent/child links
        (`cancel_session`), so a cancelled child ends `interrupted` and injects nothing.

        `provider`/`model` (either may be given alone) choose the
        child's LLM target for a FRESH spawn: `provider` moves the agent's model id to
        another registered+configured provider, a bare `model` keeps the agent's
        provider, and a full `provider:model` is used as written. The resolved route is
        written to the child row, so a later resume continues on the model the child
        actually ran. A resumed spawn refuses an override outright.

        Returns `{"task_id", "status", "report", "tokens"}` for a foreground spawn — the
        child's last assistant text and the turn's token spend, so the parent's budget
        folds a fan-out in. A child that failed carries `status="error: …"`; a refused
        spawn carries `"error"`. A background spawn returns `{"task_id", "status":
        "running", "report": <running envelope>}` and its completion arrives as the
        injected envelope, not as a return value.

        A foreground spawn can be promoted WHILE it runs (`promote_subagent`, P6): it then
        returns the same running shape a background spawn returns and its completion takes
        the same injection path.

        `agent_info` is a definition the CALLER built (a background workflow's runner, ADR
        0110) instead of one `resolve_agents` knows by name. Nothing the model calls can
        pass it, so a resumed `task_id` of such a child is refused as an unknown type.

        `isolation="worktree"` gives a FRESH child its own git worktree, whatever
        the session's global switch says; a resumed child keeps the folder it has.

        A foreground call that finds every slot taken is QUEUED: it returns
        `{"task_id": "", "status": "queued", "report": <queued envelope>}` at once, and
        `_queue_spawn` starts it as a background child when a slot frees, its result injected
        like any background child's. `held_slot` is that queued call's slot, reserved while it
        waited; nothing else passes it.
        """
        child = self.session_store.load(task_id) if task_id else None
        if task_id and (child is None or child.kind != "subagent"):
            return {"error": f"Unknown task_id: {task_id}"}
        if child is not None:
            parent_id = child.parent_session or parent_id
            agent_name = (child.agent or "").strip() or agent_name
            if agent_name == WORKFLOW_RUNNER:
                # A workflow run reads nothing (`workflow_runs.py`): a message to it was answered
                # "delivered" and never read (a real run, 2026-10-13). Its stop is the sync door's.
                return {"error": WORKFLOW_TAKES_NO_MESSAGES.format(run=child.session_id)}
            if name or effort:
                return {
                    "error": (
                        "name and effort only apply to a new task — a resumed task keeps its "
                        "name and the way it thinks."
                    )
                }
            # A child still at work takes the prompt where it is. Checked first: it
            # starts nothing, so neither the fan-out cap nor a concurrency slot applies.
            sent = await deliver_to_child(
                self,
                parent_id=parent_id,
                child_id=child.session_id,
                prompt=prompt,
                description=description,
                on_metadata=on_metadata,
                parent_engine=self._engines.get(parent_id),
            )
            if sent is not None:
                return sent
        elif name:
            problem = name_problem(name)
            taken = "" if problem else name_taken(self, parent_id, name)
            if problem or taken:
                return {
                    "error": problem
                    or (
                        f'this session already has a subagent called "{name}" ({taken}): pick '
                        "another name, or send it a message with message_task"
                    )
                }

        parent = self.session_store.load(parent_id)
        # The parent is the RUNNING turn (the `task` tool invoked us from inside it), so
        # its live engine IS the session's toolset: extra_tools (MCP/connector) already
        # merged. A spawn with no live parent engine (direct callers, tests) falls back
        # to the fixed set `_child_agent` declares.
        parent_engine = self._engines.get(parent_id)
        # Fan-out cap (a hard limit, adopted 2026-09-23): a model that spawns
        # children in a loop burns the whole roster on one turn. Counted per parent TURN
        # and reset by `main_loop` with the other per-turn guard state; direct callers
        # (teams, bridges) with no live parent engine keep today's behavior.
        if parent_engine is not None:
            spawned = int(getattr(parent_engine, "_subagents_spawned", 0) or 0)
            if spawned >= MAX_SUBAGENTS_PER_TURN:
                return {
                    "error": (
                        f"subagent fan-out limit reached ({MAX_SUBAGENTS_PER_TURN} spawns "
                        "in one turn). Use the results you already have, or ask the user "
                        "before spawning more."
                    )
                }
        ws = (child.workspace if child is not None else None) or (
            parent.workspace if parent is not None else None
        )
        # A remote-bound spawn has no LOCAL workspace: `ws` is a POSIX path on the other
        # host, so project config/trust must not be read from the controller's disk with
        # it. The global config layer is the honest read here.
        remote_bound = bool(
            (child is not None and child.environment_id)
            or (parent is not None and parent.environment_id)
        )
        config = load_config(
            None if remote_bound else ws,
            workspace_trusted=bool(
                not remote_bound and ws and self.workspace_trust.is_trusted(ws)
            ),
        )
        agent = agent_info if (agent_info is not None and child is None) else resolve_agents(config).get(agent_name)
        if agent is None:
            return {"error": f"Unknown agent type: {agent_name} is not a valid agent type"}
        if getattr(agent, "disabled", False):
            # The Settings ▸ Subagents switch (`disabled_subagents.json`). The `task`
            # tool refuses this too; this copy covers direct callers (teams, bridges).
            return {
                "error": (
                    f"{agent.name} is turned off in Settings ▸ Subagents — turn it back "
                    "on there to delegate to it."
                )
            }

        # A per-call LLM route (`provider` and `model` on the call). Only a FRESH
        # child can take one — a resumed session keeps the model its context was built
        # by — and the provider must be registered and configured before any child row
        # exists, so a typo leaves nothing behind.
        override_provider = str(provider or "").strip()
        override_model = str(model or "").strip()
        spawn_model = ""
        if override_provider or override_model:
            if not self.subagent_model_selection():
                return {
                    "error": (
                        "model selection is turned off in Settings ▸ Subagents — this "
                        "child runs on the model its agent type is configured with."
                    )
                }
            if child is not None:
                return {
                    "error": (
                        "provider/model only apply to a new task — a resumed task keeps "
                        "the model it started on; start a fresh task to switch."
                    )
                }
            cli = str(getattr(agent, "provider", "") or "")
            if cli:
                # A CLI child IS its own provider: a BARE model id from its account's
                # own catalog reaches the CLI's `--model`; a provider prefix is
                # meaningless there.
                if override_provider:
                    return {
                        "error": (
                            f"{agent.name} runs on the {cli} CLI's own provider — pass "
                            "a bare `model` instead, or neither."
                        )
                    }
                if ":" in override_model:
                    return {
                        "error": (
                            f"the {cli} CLI takes a bare model id from its own account "
                            "catalog — drop the provider prefix."
                        )
                    }
                spawn_model = override_model
            else:
                from harness.orchestration.subagent_external import PROVIDER_BINARIES
                from harness.orchestration.subagent_models import resolve_route, route_problem

                if override_provider in PROVIDER_BINARIES:
                    target = (
                        f'subagent_type="{override_provider}"'
                        + (f', model="{override_model}"' if override_model else "")
                    )
                    return {
                        "error": (
                            f'"{override_provider}" is a CLI subagent (an agent type), '
                            f"not an LLM provider — call task({target}) instead."
                        )
                    }
                problem = route_problem(override_provider, self.secrets)
                if problem:
                    return {"error": problem}
                spawn_model, problem = resolve_route(
                    agent.model or (parent.model if parent else self.model),
                    override_provider,
                    override_model,
                )
                if problem:
                    return {"error": problem}

        # A thinking level for a fresh child, checked against the model it will run
        # on before any row exists: the same route the creation below writes.
        if effort and child is None:
            problem = effort_problem(
                effort,
                spawn_model or agent.model or (parent.model if parent else self.model) or "",
                cli=bool(getattr(agent, "provider", "")),
            )
            if problem:
                return {"error": problem}

        # An external CLI child is refused BEFORE a child session exists when the CLI's
        # sign-in has lapsed — the cheap probe (a credential file, `codex login status`)
        # turns a run-time exit code into one clear sentence, and no empty child row is
        # left behind. Probe failures are values; an unexpected one lets the run decide.
        if getattr(agent, "provider", ""):
            from harness.orchestration.subagent_external import preflight

            problem = await asyncio.to_thread(preflight, agent.provider)
            if problem:
                return {"error": problem}

        # `output_schema` is validated here too (the tool already did) and confined to
        # in-process agents: correcting invalid JSON in an external CLI child would mean
        # paying a whole second CLI run, so it is refused up front instead.
        if output_schema is not None:
            problem = validate_output_schema(output_schema)
            if problem:
                return {"error": problem}
            if getattr(agent, "provider", ""):
                return {
                    "error": (
                        "output_schema is not supported for external CLI subagents "
                        f"({agent.provider}) — use an in-process agent type"
                    )
                }

        # Per-parent concurrency cap (config `subagent_max_parallel`, 0 = uncapped). The
        # thread harness had one from the start; the managed path spawned unbounded.
        # Checked before any child row exists so a refused spawn leaves nothing behind;
        # the slot itself is taken at the claim below and released when the child settles.
        #
        # A call that asked for its result WAITS for a slot: it was going to wait
        # for the child anyway, and the refusal told a model to "wait for one to finish"
        # with nothing to wait on (15 refusals in one fan-out of 19, 2026-09-19). A
        # background call is still refused: its caller chose not to wait. The slot is
        # reserved the moment it is free, so two waiting calls never take the same one. A
        # follow-up into a child that is already running holds its slot already.
        parallel_limit = subagent_parallel_limit(config)
        slot = ""
        queued = 0.0
        holds_slot = child is not None and child.session_id in self._live_children.get(parent_id, ())
        if held_slot:
            # A queued call's own slot, reserved while it waited (`_queue_spawn`).
            slot = held_slot
        elif parallel_limit > 0 and not holds_slot:
            running_now = len(self._live_children.get(parent_id, ()))
            if not background and running_now >= parallel_limit and ":code:" not in origin and self.session_depth(parent_id) == 0:
                # Every slot is taken: the call is queued and returns. It
                # used to wait for a slot up to its own rail, and the four children holding the
                # slots ran past that rail, went to the background still holding them, and the
                # three waiting calls failed after fourteen minutes with nothing written (owner,
                # 2026-10-21). A program's call and a subagent's own call still wait: they need
                # the answer as their return value.
                _emit_metadata(
                    on_metadata,
                    {"waiting": {"running": running_now, "limit": parallel_limit}, "background": True},
                )
                self._queue_spawn(
                    parent_id,
                    parallel_limit,
                    description,
                    {
                        "parent_id": parent_id,
                        "description": description,
                        "prompt": prompt,
                        "agent_name": agent_name,
                        "task_id": task_id,
                        "inherit_context": inherit_context,
                        "output_schema": output_schema,
                        "origin": origin,
                        "provider": provider,
                        "model": model,
                        "on_metadata": on_metadata,
                        "agent_info": agent_info,
                        "isolation": isolation,
                        "name": name,
                        "effort": effort,
                    },
                )
                return {
                    "task_id": "",
                    "status": "queued",
                    "report": render_output(
                        session_id="",
                        state="queued",
                        summary="Task queued",
                        text=QUEUED.format(running=running_now, limit=parallel_limit),
                    ),
                }
            if not background and running_now >= parallel_limit:
                # The row is told it is in line, or it reads "Running" with no child behind it:
                # two of six in one fan-out showed that while they waited (owner, 2026-10-21).
                # The child's first frame, with its session id, ends the wait on the row.
                _emit_metadata(on_metadata, {"waiting": {"running": running_now, "limit": parallel_limit}})
            queued_from = time.monotonic()
            slot = await self._take_child_slot(parent_id, parallel_limit, wait=not background)
            queued = time.monotonic() - queued_from
            if not slot:
                running_children = len(self._live_children.get(parent_id, ()))
                waited = (
                    f" and none finished in {int(queued)} s"
                    if not background
                    else " — wait for one to finish, or leave `background` off and this call waits its turn"
                )
                return {
                    "error": (
                        f"{running_children} subagents are already running for this "
                        f'session and "subagent_max_parallel" is {parallel_limit}{waited}.'
                    )
                }

        claimed = False
        try:
            if child is None:
                # `inherit_context` starts the child from the parent's history instead of an
                # empty transcript. Only a FRESH child can inherit; a resumed one already carries
                # its own context.
                seed = _fork_seed(parent.messages) if (inherit_context and parent is not None) else []
                # A CLI child's model is the CLI's OWN: the override, else the definition's
                # pin, else "" (the CLI's default). It must NEVER inherit the parent's LLM
                # model id — that value would be handed to the CLI's `--model` and name a
                # model the CLI's account does not serve (owner-report 2026-09-21 matrix).
                cli_child = bool(getattr(agent, "provider", ""))
                child_id = self.create_child_session(
                    parent_id,
                    description,
                    agent_name,
                    spawn_model
                    or agent.model
                    or ("" if cli_child else (parent.model if parent else self.model)),
                    seed_messages=seed or None,
                    isolation=isolation,
                    agent_info=agent,
                )
                child = self.session_store.load(child_id)
                if name:
                    remember_name(self, parent_id, name, child_id)
                if effort and child is not None:
                    # The row's own level, read by every turn of this child (`_run_child_turn`).
                    child.thinking = effort
                    self.session_store.save(child)
                if cli_child:
                    # `create_child_session` falls back to the parent's model when the caller
                    # passes none — right for an in-process child, wrong for a CLI child (the
                    # row must not claim an LLM id from another provider). Correct it here.
                    desired = spawn_model or agent.model or ""
                    if child is not None and child.model != desired:
                        child.model = desired
                        self.session_store.save(child)
                # The creation frame's model: the child row's, the definition's pin, and —
                # only for an in-process child — the session's own as the last fallback. A
                # CLI child stops at "" (the CLI's default) rather than borrowing an LLM id
                # from another provider namespace.
                announced_model = (child.model if child else "") or agent.model or (
                    "" if cli_child else self.model
                )
                # A child session is announced on the APP-WIDE stream, not only to the
                # parent's viewers (step C): every client lists sessions, so a
                # sibling window must be able to show it the moment it exists. Best-effort: an
                # announcement must never fail the spawn it announces.
                await self._announce_session_event(
                    {
                        "type": "session_created",
                        "data": {
                            "session_id": child_id,
                            "parent_session": parent_id,
                            "title": child.title if child else "",
                            "agent": child.agent if child else agent_name,
                            "model": child.model if child else "",
                            "kind": "subagent",
                        },
                    }
                )
                _emit_metadata(
                    on_metadata,
                    {
                        "session_id": child_id,
                        "parent_session_id": parent_id,
                        "title": description,
                        "toolcalls": 0,
                        # P2: the row measures the child's elapsed from the first frame's
                        # stamp; `ended_at`/`duration_ms` arrive on the settle frame.
                        "started_at": time.time(),
                        # A CLI child reports its own model, never the parent's LLM id.
                        "model": announced_model,
                        "background": bool(background),
                        # `job_id` is the child session id for a detached run, carried for
                        # clients that address the job.
                        **({"job_id": child_id} if background else {}),
                    },
                )
            else:
                child_id = child.session_id

            if not self.try_mark_running(child_id):
                return {
                    "task_id": child_id,
                    "status": "error: this task is already running",
                    "report": "",
                    "tokens": 0,
                }
            # The concurrency slot is taken only once the child is actually claimed, and is
            # released when its turn settles (`_run_child_turn`/`_run_external_child_turn`
            # both release in their finally). Keyed by parent: the cap is per session.
            slots = self._live_children.setdefault(parent_id, set())
            slots.discard(slot)
            slots.add(child_id)
            claimed = True
        finally:
            if slot and not claimed:
                # Refused or failed between the reservation and the claim: give it back.
                self._release_child_slot(parent_id, slot)
        # Counted HERE, once a child is claimed (orchestration audit O2): a refused spawn —
        # an unknown or disabled agent, a bad route, the concurrency cap — used to count, so
        # a model that mistyped an agent name twice lost delegation for the rest of the turn.
        if parent_engine is not None:
            parent_engine._subagents_spawned = (
                int(getattr(parent_engine, "_subagents_spawned", 0) or 0) + 1
            )
        if origin:
            # Which tool call (a workflow run answers `<root>:code:<n>`) spawned this
            # child, so `cancel_call_children` can stop a dead run's agents.
            self._child_call_origins[child_id] = origin.split(":code:", 1)[0]
        await self._announce_session_event(
            {
                "type": "session_status",
                "data": {"session_id": child_id, "status": "busy"},
            }
        )

        if background:
            return self._start_background_child(
                parent_id=parent_id,
                child_id=child_id,
                description=description,
                prompt=prompt,
                agent=agent,
                output_schema=output_schema,
                on_metadata=on_metadata,
                parent_engine=parent_engine,
                route_pinned=bool(spawn_model),
            )
        return await self._run_foreground_child(
            parent_id=parent_id,
            child_id=child_id,
            description=description,
            prompt=prompt,
            agent=agent,
            output_schema=output_schema,
            on_metadata=on_metadata,
            parent_engine=parent_engine,
            route_pinned=bool(spawn_model),
            # Orchestration audit O1: a program's call (`<root>:code:<n>`, a workflow's
            # `agent()`) and a subagent's own spawn both need the child's ANSWER as the
            # return value. Backgrounded, they got the "running in the background" notice
            # as the answer, and the real report later opened an unasked parent turn.
            can_background=":code:" not in origin and self.session_depth(parent_id) == 0,
        )

    async def _run_foreground_child(
        self,
        *,
        parent_id: str,
        child_id: str,
        description: str,
        prompt: str,
        agent: AgentInfo,
        output_schema: Optional[dict[str, Any]] = None,
        on_metadata: Optional[Callable[[dict[str, Any]], None]] = None,
        parent_engine: Optional[Any] = None,
        route_pinned: bool = False,
        can_background: bool = True,
    ) -> dict[str, Any]:
        """Run a foreground child, promotable while it works (P6).

        `can_background=False` runs it to completion: no hand-over and no promotion, for a
        caller that has nowhere to receive a completion notice (O1).

        A foreground child has no job registry entry, so the hand-over is an explicit race:
        promotion flips the job's metadata to `background: true`, the waiting tool returns the
        running envelope, and the child starts notifying the parent on completion. The
        child's turn is one
        task on this loop and the spawn waits on it against a per-child promotion future.
        A promotion resolves the future first, the spawn returns `state="running"` with
        `BACKGROUND_MOVED` — the notice for a caller that asked to wait and has no result
        yet, not the one a background spawn gets — and the SAME completion writer injects
        the finished envelope into the parent. The child's turn itself is untouched — same
        engine, same transcript, same settle frames.

        A child that finishes before any promotion resolves the turn first and returns the
        ordinary foreground result; the pending promotion waiter is cancelled. Promotion
        covers the DIRECT children of the given session only — nested grandchildren are out
        of scope, matching the parent-session filter on the job's metadata.
        """
        loop = asyncio.get_running_loop()
        promotion: "asyncio.Future[bool]" = loop.create_future()
        if can_background:
            self._promotable[child_id] = {
                "parent_id": parent_id,
                "future": promotion,
                "loop": loop,
            }
        turn = loop.create_task(
            self._run_child_turn(
                child_id=child_id,
                parent_id=parent_id,
                prompt=prompt,
                agent=agent,
                output_schema=output_schema,
                on_metadata=on_metadata,
                parent_engine=parent_engine,
                route_pinned=route_pinned,
            )
        )
        # The hand-over: a foreground call is WAITED FOR — the caller chose it
        # because its next step needs the answer — up to the call's own rail. A child still
        # working there is handed to the background instead of being abandoned by the
        # engine's budget: the spawn returns the running envelope and the same completion
        # writer finishes the job. A route-pinned child is handed over like any other (it
        # used to be exempt from the 120 s timer, which at the rail would only mean an
        # orphan). No inactivity kill: an idle-kill would end long
        # compiles — the parent judges from a completion it can now receive. A call
        # that waited for a slot is never handed over: only a program's or a subagent's call
        # still waits, and neither can receive a completion.
        auto_seconds = max(1.0, FOREGROUND_WAIT_SECONDS)
        timer: Optional[asyncio.Task] = None
        if auto_seconds and auto_seconds > 0 and can_background:
            timer = loop.create_task(asyncio.sleep(auto_seconds))
        race = {turn, promotion} | ({timer} if timer is not None else set())
        try:
            done, pending = await asyncio.wait(race, return_when=asyncio.FIRST_COMPLETED)
        except asyncio.CancelledError:
            # The spawn was torn down (parent turn abort): the child goes with it, which is
            # what awaiting the turn directly used to do.
            turn.cancel()
            if timer is not None:
                timer.cancel()
            raise
        finally:
            self._promotable.pop(child_id, None)

        if turn in done:
            for waiting in pending:
                waiting.cancel()
            return turn.result()
        if timer is not None and not timer.done():
            timer.cancel()
        auto = timer is not None and timer in done

        # Promoted (a person) or handed over at the rail (the timer). The child's task stays
        # alive; the registry entries make it a background job to every other lifecycle
        # path (Stop's loop handoff, the completion writer's cleanup), and the promotion
        # frame is the metadata update that carries the GUI's `(background)` marker and ends
        # its promote affordance.
        self._note_background_child(child_id, parent_id)
        self._background_tasks[child_id] = turn
        self._background_loops[child_id] = loop
        if auto:
            logger.info(
                "child %s handed over to the background after %.0fs; the parent's tool call returns",
                child_id,
                auto_seconds,
            )
        _emit_metadata(
            on_metadata,
            {
                "session_id": child_id,
                "background": True,
                "job_id": child_id,
                **({"auto": True} if auto else {}),
            },
        )
        loop.create_task(
            self._settle_background(
                parent_id=parent_id,
                child_id=child_id,
                description=description,
                agent=agent,
                run=turn,
            )
        )
        return {
            "task_id": child_id,
            "status": "running",
            "report": render_output(
                session_id=child_id,
                state="running",
                summary="Task moved to the background",
                text=BACKGROUND_MOVED,
            ),
        }

    async def _run_external_child_turn(
        self,
        *,
        child_id: str,
        parent_id: str,
        prompt: str,
        agent: AgentInfo,
        on_metadata: Optional[Callable[[dict[str, Any]], None]] = None,
    ) -> dict[str, Any]:
        """Run one external CLI child (codex / claude-code) and persist its exchange.

        The CLI runs on a worker thread (it blocks for minutes); Stop reaches it through
        `_external_stops`, which `cancel_session` sets for the whole descendant walk. The
        child's transcript keeps the prompt and the final answer, so the session view
        still shows what was delegated and what came back.
        """
        from harness.orchestration.subagent_external import DEFAULT_TIMEOUT, progress_text, run_external

        start = time.time()
        stop = threading.Event()
        self._external_stops[child_id] = stop
        if child_id in self._cancel_requested:
            self._cancel_requested.discard(child_id)
            stop.set()
        child = self.session_store.load(child_id)
        workspace = (child.workspace if child else None) or None
        # The child ROW's model is what this run must use: it carries the per-call
        # override a bare model id (its account's catalog) wrote at creation.
        model = (child.model if child is not None else "") or agent.model or ""
        # Everything before this turn's exchange is the child's history; the live
        # transcript is always `base + [prompt] + [assistant]`, rewritten in place.
        base = list(child.messages) if child is not None else []

        def _persist(assistant_text: str) -> None:
            record = self.session_store.load(child_id)
            if record is None:
                return
            record.messages = [
                *base,
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": assistant_text},
            ]
            self.session_store.save(record)

        # The prompt lands BEFORE the CLI starts: opening the child mid-run shows what
        # was delegated instead of an empty session (the CLI itself has no engine whose
        # events could stream, so the persisted transcript IS the live view here).
        _persist(f"{agent.provider} is working…")
        loop = asyncio.get_running_loop()
        progress = {"text": "", "done": False}
        last_save = {"at": 0.0}

        def _persist_progress() -> None:
            if progress["done"]:
                return
            _persist(progress["text"])

        def _on_event(event: dict[str, Any]) -> None:
            """One streamed CLI event → the child's live progress text (debounced save).

            Runs on the reader thread, so the write is marshalled back to the manager
            loop; the record is the same shape the final save writes, which is why the
            view never flickers between two transcripts.
            """
            if progress["done"]:
                return
            updated = progress_text(agent.provider, event, progress["text"])
            if updated == progress["text"]:
                return
            progress["text"] = updated[-8000:]
            now = time.monotonic()
            if now - last_save["at"] >= 0.7:
                last_save["at"] = now
                loop.call_soon_threadsafe(_persist_progress)

        _emit_metadata(
            on_metadata,
            {
                "session_id": child_id,
                "tool": agent.provider,
                "title": f"{agent.provider} · working",
                "toolcalls": 0,
                "phase": "start",
                "started_at": start,
                **({"model": model} if model else {}),
            },
        )

        def _started(argv: list[str]) -> None:
            _emit_metadata(
                on_metadata,
                {
                    "session_id": child_id,
                    "tool": agent.provider,
                    "title": " ".join(argv[:3]),
                    "phase": "start",
                },
            )

        try:
            result = await asyncio.to_thread(
                run_external,
                agent.provider,
                prompt=prompt,
                workspace=workspace,
                model=model,
                permission_mode=agent.provider_permission,
                timeout=DEFAULT_TIMEOUT,
                stop_event=stop,
                on_started=_started,
                on_event=_on_event,
            )
        except Exception as exc:  # a runner crash is a child result, not a parent crash
            result = {"status": f"error: {exc}", "report": "", "tokens": 0}
        finally:
            self._external_stops.pop(child_id, None)
            self.mark_idle(child_id)
            self._release_child_slot(parent_id, child_id)

        # The final answer replaces the live progress text; any progress write still in
        # flight sees `done` and drops itself.
        progress["done"] = True
        _persist(str(result.get("report") or ""))
        _emit_metadata(
            on_metadata,
            {
                "session_id": child_id,
                "toolcalls": 0,
                "ended_at": time.time(),
                "duration_ms": int((time.time() - start) * 1000),
            },
        )
        await self._announce_session_event(
            {
                "type": "session_status",
                "data": {"session_id": child_id, "status": "idle"},
            }
        )
        return {
            "task_id": child_id,
            "status": str(result.get("status") or "completed"),
            "report": str(result.get("report") or ""),
            "tokens": int(result.get("tokens") or 0),
        }

    def _release_child_slot(self, parent_id: Optional[str], child_id: str) -> None:
        """Give back the per-parent concurrency slot a claimed child took."""
        self._child_call_origins.pop(child_id, None)
        if parent_id is None:
            return
        slots = self._live_children.get(parent_id)
        if not slots:
            return
        slots.discard(child_id)
        if not slots:
            self._live_children.pop(parent_id, None)
        if self._child_slot_freed is not None:
            self._child_slot_freed.set()

    async def _take_child_slot(
        self, parent_id: str, limit: int, *, wait: bool, deadline_s: Optional[float] = None
    ) -> str:
        """Reserve one of `parent_id`'s child slots and return its token, or "" when none
        is free: at once without `wait`, else after `deadline_s` (`SLOT_WAIT_SECONDS` when not
        given; `math.inf` waits until one frees, the queue's wait).

        The check and the reservation happen in one step of the loop, so of several calls
        woken by one freed slot exactly one takes it and the rest wait on."""
        if self._child_slot_freed is None:
            self._child_slot_freed = asyncio.Event()
        deadline = time.monotonic() + (SLOT_WAIT_SECONDS if deadline_s is None else deadline_s)
        while len(self._live_children.get(parent_id, ())) >= limit:
            remaining = deadline - time.monotonic()
            if not wait or remaining <= 0:
                return ""
            self._child_slot_freed.clear()
            try:
                await asyncio.wait_for(
                    self._child_slot_freed.wait(), None if remaining == float("inf") else remaining
                )
            except asyncio.TimeoutError:
                return ""
        self._slot_seq = getattr(self, "_slot_seq", 0) + 1
        token = f"queued:{self._slot_seq}"
        self._live_children.setdefault(parent_id, set()).add(token)
        return token

    def _queue_spawn(self, parent_id: str, limit: int, description: str, spawn: dict[str, Any]) -> None:
        """Start a queued call as a background child once one of `parent_id`'s slots frees (ADR
        0128 amendment). It waits for as long as that takes: the children holding the slots may
        run for hours, and a wait with a limit is the fourteen-minute failure this replaced. Stop
        on the parent cancels it (`_cancel_session_tree`), and the slot it reserved is given
        back however it ends. A spawn refused after the wait (the agent was disabled meanwhile)
        reaches the parent as a failed background task, so the queued call is never silent."""

        async def run() -> None:
            slot = ""
            try:
                slot = await self._take_child_slot(parent_id, limit, wait=True, deadline_s=float("inf"))
                out = await self.spawn_subagent(**spawn, background=True, held_slot=slot)
                if out.get("error") and not out.get("task_id"):
                    await self._inject_background_result(
                        parent_id=parent_id,
                        child_id="",
                        description=description,
                        result={"status": f"error: {out['error']}", "report": ""},
                    )
            finally:
                # A claimed child swapped the reservation for its own id; anything else is ours.
                if slot and slot in self._live_children.get(parent_id, ()):
                    self._release_child_slot(parent_id, slot)

        entry = (asyncio.ensure_future(run()), description)
        queue = self._queued_spawns.setdefault(parent_id, [])
        queue.append(entry)
        entry[0].add_done_callback(lambda _done: entry in queue and queue.remove(entry))

    def cancel_call_children(self, root_call_id: str) -> list[str]:
        """Stop every running child spawned by one tool call.

        A workflow run's `agent()` children are ordinary `task` sessions: if the run dies
        (Stop, an engine timeout), its in-flight children would otherwise keep working
        with nobody left to receive the result. Each is stopped through the same
        interrupt tree the Stop button pulls. Returns the interrupted session ids.
        """
        stopped: list[str] = []
        for child_id, origin in list(self._child_call_origins.items()):
            if origin == root_call_id:
                self._cancel_session_tree(child_id, stopped)
        return stopped

    async def _run_child_turn(
        self,
        *,
        child_id: str,
        parent_id: Optional[str],
        prompt: str,
        agent: AgentInfo,
        output_schema: Optional[dict[str, Any]] = None,
        on_metadata: Optional[Callable[[dict[str, Any]], None]] = None,
        #: The parent session's live engine, when there is one (the `task` call runs
        #: inside the parent's turn, so normally there is). Its registry is the session's
        #: toolset the child inherits and its thinking level is the variant
        #: a model-less agent forwards. None = the fixed fallback.
        parent_engine: Optional[Any] = None,
        #: True when THIS run is the spawn that wrote an explicit route override onto the
        #: child row: the row's model then wins over the agent's, and the config-changed
        #: rewrite below is skipped (the override is the session's decision, not a config
        #: default that drifted).
        route_pinned: bool = False,
    ) -> dict[str, Any]:
        """Build the child session's engine and drive ONE turn on it.

        The body shared by a foreground `spawn_subagent` (which awaits it) and the
        detached background task (step D), so the two paths cannot disagree
        about what a child is: same `build_engine`, same ruleset filtering, same
        `_drive_turn`, same `session_status` announcements. The caller owns the claim
        (`try_mark_running`) and the `busy` announcement; this method owns the engine,
        the save/checkpoint plumbing, the `idle` announcement and the result shape.

        `parent_id` is the session the ask frames route through (P1): a child's tool ask
        is parked on the manager's permission broker with `session_id=child_id` and this
        parent, so the dock lands on the viewed ancestor that spawned it.
        """
        # An external provider (codex / claude-code) never builds an engine: the child
        # IS its CLI process. Kept on the same manager path so child sessions, live
        # metadata, Stop and completion injection work exactly like in-process children.
        if agent.provider:
            return await self._run_external_child_turn(
                child_id=child_id,
                parent_id=parent_id,
                prompt=prompt,
                agent=agent,
                on_metadata=on_metadata,
            )
        # The turn's transcript is reloaded here rather than passed in: on a resume this
        # is the child's own store (the durable substitute for the thread harness's
        # ≤3 in-memory engines), and a fresh child reads as no history at all.
        child = self.session_store.load(child_id)
        # A child of a remote session inherits the binding (create_child_session), so its
        # world must be built over the same profile/host as the parent's — otherwise the
        # POSIX workspace would be resolved on this machine.
        child_remote_profile, child_remote_runner, child_remote_password, remote_child = (
            self._resolve_child_remote(child_id, child)
        )
        config = load_config(
            None if remote_child else (child.workspace if child and child.workspace else None),
            workspace_trusted=bool(
                not remote_child
                and child
                and child.workspace
                and self.workspace_trust.is_trusted(child.workspace)
            ),
        )
        messages: Optional[list[dict[str, Any]]] = (
            (list(child.messages) or None) if child is not None else None
        )

        # One counter per child turn: what the parent's row renders as `↳ N toolcalls`.
        tool_calls = 0
        # Whether a RETRYING frame is standing on the parent's row (P2): the next thing
        # the child produces clears it.
        retry_active = False
        # What the row last said the child was doing between tools, and the spend it last
        # reported: a frame is sent when one of them CHANGES.
        live_phase = ""
        tokens_told = 0

        def _watch(kind: str, data: dict[str, Any]) -> None:
            """One child engine event → the parent row's live metadata (C).

            `tool_proposed` is the child engine's "this call is about to run" moment —
            the same event that opens a row in the child's own view — so it is what moves
            the parent row's `↳ <tool>` line. The title is the tool's own
            `presentation.summary` when the contract declared one (grep: «Search for
            “cache”»); only a presenter-less tool falls back to its clipped arguments,
            and a call with neither falls back to its bare name.

            `retrying` (P2) is the engine's mid-stream retry notice; it rides the same
            row as `↳ Retrying (attempt N) · <message>` and is
            cleared by the next tool proposal or assistant message.
            """
            nonlocal tool_calls, retry_active, live_phase, tokens_told
            if on_metadata is None:
                return
            payload = data or {}
            if kind == "retrying":
                retry_active = True
                _emit_metadata(
                    on_metadata,
                    {
                        "session_id": child_id,
                        "retry": {
                            "attempt": int(payload.get("attempt") or 0),
                            "message": str(payload.get("error") or ""),
                        },
                    },
                )
                return
            if retry_active and kind in ("tool_proposed", "assistant_message"):
                # The same request is going again and produced something: the retry state
                # is over. A `null` tells the client to drop the line rather than leaving
                # the last attempt's message standing for the rest of the run.
                retry_active = False
                _emit_metadata(on_metadata, {"session_id": child_id, "retry": None})
            if kind in ("reasoning_delta", "assistant_delta"):
                # BETWEEN its tools a child is thinking or writing, and that is most of its
                # time: the row said `↳ grep …` about a call that ended a minute ago.
                # One frame per change of phase, never one per delta.
                phase = "thinking" if kind == "reasoning_delta" else "writing"
                if phase != live_phase:
                    live_phase = phase
                    _emit_metadata(on_metadata, {"session_id": child_id, "phase": phase})
                return
            if kind == "tool_output_delta":
                # A workflow's `phase()` line is what a background run is doing (0110):
                # the row shows the phase, as it shows a child's current tool.
                text = str(payload.get("text") or "")
                if text.startswith("▸ "):
                    _emit_metadata(
                        on_metadata,
                        {"session_id": child_id, "tool": "workflow", "title": text[2:].strip()},
                    )
                return
            if kind == "assistant_message" and engine is not None:
                # A step's spend is on its message by now: the row shows what the child
                # has cost SO FAR, the same fold its result reports at the end.
                spent = _usage_total(engine.messages[start:])
                if spent != tokens_told:
                    tokens_told = spent
                    _emit_metadata(on_metadata, {"session_id": child_id, "tokens": spent})
            if kind != "tool_proposed":
                return
            live_phase = "tool"
            shown = payload.get("presentation") or {}
            tool_calls += 1
            _emit_metadata(
                on_metadata,
                {
                    "session_id": child_id,
                    "tool": str(payload.get("name") or ""),
                    "title": str(
                        shown.get("summary")
                        or _short_tool_title(payload.get("arguments"))
                    ),
                    "toolcalls": tool_calls,
                    "phase": "start",
                },
            )

        # Read by the settle frame in `finally`; None until the engine exists (a crash
        # while building one still settles the row, with no duration).
        engine: Optional[Any] = None
        start = 0
        # Set with the child's final report (the committed capture, else JSON text when
        # `output_schema` asked for one) and the validation errors that survived the
        # corrective retry.
        report = ""
        structured_problem: list[str] = []

        try:
            # The ruleset decides the child's tools whether or not the parent registry
            # was inherited, so it is computed first: the skill filter below is the same
            # decision expressed in the prompt.
            rules = child_permission([], agent) + child_rules(agent)
            # P5 / `subagent_depth`: a child below the limit may hold `task` again, and
            # the manager's spawn bridge is wired for it below. The ruleset still has the
            # final say — `child_permission` denies `task` unless the agent's own ruleset
            # mentions it, so nesting is opt-in per agent definition.
            limit = subagent_depth_limit(config)
            nested_allowed = limit > 0 and self.session_depth(child_id) < limit
            # `primary_tools` + the interactive doors: removed whatever the ruleset says.
            never = _never_tools(config, allow_task=nested_allowed)
            # The child's registry is a COPY of the parent session's, filtered per agent, so
            # MCP/connector tools, skills, memory,
            # plan tools, run_code and terminals all cross into the child. The fixed set
            # `_child_agent` declares is only the fallback for a spawn with no live parent
            # (direct callers; the CLI thread path is `task_runtime` and was never this).
            # ...minus the tools bound to the PARENT SESSION: its goal, wakes,
            # schedule origin, channel subscriptions, history and team seat. The child's own
            # build below registers the ones a child is entitled to, bound to the child.
            # The tools, settings and thinking level come from the session that delegated
            # the work: for an `agent()` of a background workflow that is the session that
            # ran the workflow, not the runner, which holds only `workflow` and `task`
            # (`_delegator_engine`).
            source = self._delegator_engine(parent_id, parent_engine)
            base_registry = (
                source.registry.filtered(exclude=never, session_bound=False)
                if source is not None
                else None
            )
            # A rule that denies the skill surface must suppress the MENU too, not just
            # the tool: an engine that still lists skills the model may not load is
            # instructing it to ask for a refusal. An empty `allowed` set makes the
            # catalog and `load_skill` agree.
            skill_allowed = set() if evaluate("load_skill", "", rules) == DENY else None

            model = (
                child.model if (route_pinned and child is not None) else ""
            ) or agent.model or (child.model if child else "") or self.model
            steps = (
                agent.steps if agent.steps is not None else config.subagent_steps
            )
            # The child row must record the agent + model ACTUALLY used. The agent is written at creation; this is the
            # resume case, where the config now names a different model for the same agent.
            # A per-call route override IS the row's model (written at creation), so a
            # routed run never rewrites it here.
            if (
                child is not None
                and model
                and child.model != model
                and not route_pinned
            ):
                child.model = model
                self.session_store.save(child)
            # The agent's own knobs ride `model_settings` (the same channel the parent
            # session's real settings use), merged UNDER the caller's explicit settings
            # exactly like the thread harness does, with `options` last: a passthrough
            # key can override a named knob. The explicit half is the parent engine's base settings without the thinking
            # patch (that rides `thinking_level`) and without its cache affinity (the
            # child is not the parent's prefix — `build_engine` keys its own).
            explicit: dict[str, Any] = {}
            if source is not None:
                from harness.llm.providers.base import CACHE_AFFINITY

                explicit = {
                    key: value
                    for key, value in (
                        getattr(source, "_base_model_settings", None) or {}
                    ).items()
                    if key != CACHE_AFFINITY
                }
            knobs: dict[str, Any] = {}
            if agent.temperature is not None:
                knobs["temperature"] = agent.temperature
            if agent.top_p is not None:
                knobs["top_p"] = agent.top_p
            model_settings = {**explicit, **knobs, **(agent.options or {})}
            # The agent's own variant wins; when it declares NO model of its own, the caller's thinking level is forwarded —
            # the same condition under which the child already inherits the caller's model.
            # A level the spawning call chose (`effort`) is the child row's, and beats both.
            variant = (getattr(child, "thinking", None) if child is not None else None) or agent.variant or (
                getattr(source, "thinking_level", None)
                if source is not None and not agent.model
                else None
            )

            if child and child.mode:
                # Legacy stored modes map onto the 3 presets; a stored raw `plan` was a
                # MODE and its state must survive as the plan overlay.
                mode, plan = Mode.migrated(child.mode)
                plan = plan or bool(getattr(child, "plan", False))
            else:
                mode, plan = self.mode, False

            engine = build_engine(
                agent=_child_agent(agent, subagents=nested_allowed),
                workspace=child.workspace if child else None,
                # The parent's SSH world, rebuilt for this child's turn: verifying it
                # again is the same refusal the parent session had to pass. A local child
                # passes None on both, exactly as before.
                environment=child_remote_profile,
                environment_runner=child_remote_runner,
                environment_password=child_remote_password,
                model=model,
                mode=mode,
                plan=plan,
                # A definition may bring its own provider: a background workflow's runner
                # answers with the approved call and its result, and calls no model (0110).
                provider=getattr(agent, "provider_client", None) or self.provider,
                # The parent manager's language-server pool: a child working in the same
                # root joins the parent's server instead of spawning a duplicate.
                lsp_manager=self.lsp,
                # The parent session's toolset, filtered — or None, which builds the
                # fixed fallback `_child_agent` declares.
                base_registry=base_registry,
                # The child's own root set: its workspace primary, its scratch, and the
                # parent's added folders (create_child_session copied them).
                roots=self.get_roots(child_id),
                messages=messages,
                session_id=child_id,
                # The child's OWN history is searchable once it compacts (item 2.5), like
                # any session's; the parent's `search_history` stayed behind.
                conversation_store=self.session_store,
                # `or 0` is build_engine's "uncapped": a child is capped by
                # `subagent_steps`/the agent's own `steps`, never by the session's
                # `max_iterations` fallback.
                max_iterations=steps or 0,
                model_settings=model_settings or None,
                thinking_level=variant or None,
                skill_filter=skill_allowed,
                # A child's ask goes through the manager's session-scoped broker (P1):
                # the ruleset already decided (`allow` never reaches an approver), and
                # `ask`/engine-gate calls park on a `permission_asked` frame the parent's
                # dock answers. Replaces the Inbox approver this used to carry, whose
                # cards nothing in the GUI could answer (the one missing piece of child sessions).
                approver=self._broker_approver(child_id, parent_id),
                secrets=self.secrets,
                # A nested child (P5) spawns through the SAME manager bridge a root
                # session uses, so a grandchild is a real session with its own row,
                # transcript and broker ask — its ask routes through this child.
                subagent_spawner=self._spawn_subagent_sync,
                subagent_cancel_origin=self.cancel_call_children,
                subagent_metadata=self._subagent_metadata_emitter(child_id),
                permission_asker=self._permission_asker(child_id),
                # The Settings toggle rides every build,
                # children included, so a grandchild inherits the same answer.
                model_selection=self.subagent_model_selection(),
                session_depth=lambda sid=child_id: self.session_depth(sid),
            )
            # Owner isolation: the child's registry was a
            # COPY of the parent's, so its terminal tools still pointed at the parent's
            # shells — a child could list, read, type into or close them. Rebind them to
            # the child's own world BEFORE the ruleset decides which survive; after this,
            # a parent's terminal id answers "no terminal" in the child and vice versa.
            if (
                engine.world is not None
                and engine.registry.get("terminal_open") is not None
            ):
                from harness.tools.terminal import replace_terminal_tools

                replace_terminal_tools(engine.registry, engine.world.terminals)
            # The SAME isolation for the shell (owner-hit 2026-09-20): an inherited
            # `run_shell` closure pointed at the PARENT's shell process, so two children
            # shared one REPL — their commands queued behind each other (a child's 8s
            # sleep delayed the sibling) and their trailer markers crossed, handing one
            # child "(no output)" while its command was still waiting. Each child gets
            # its own executor, exactly like the terminal rebind above.
            if engine.world is not None:
                from harness.tools.shell import replace_shell_tools

                replace_shell_tools(engine.registry, engine.world.executor)
            # The SAME isolation for the FILE and SEARCH tools (owner-hit 2026-09-18): the
            # inherited `write_file`/`read_file`/`grep` closures resolved against the
            # PARENT's root list, so a child could neither read nor write its OWN scratch —
            # the folder its plan already lived in and the one `REEVE_SCRATCH` names —
            # while its boundary and plan store used the child's own roots. Rebuilt from
            # the child's context here, before the ruleset pruning below.
            child_context = getattr(engine, "context", None)
            if engine.world is not None and child_context is not None:
                from harness.modes.catalog import replace_path_tools

                replace_path_tools(engine.registry, child_context)
            # And for the TASK LIST: the inherited `todo_write` closure wrote the PARENT's
            # list, while the engine's own list (`engine.todo`: what its nudge, its
            # compaction and its log read) stayed empty. Only a child whose ruleset opts
            # into `todo_write` keeps the tool past the pruning below; that child must plan
            # on its own list, not put its steps into the parent's Progress panel.
            if engine.todo is not None and engine.registry.get(TODO_WRITE) is not None:
                engine.registry.register_all(todo_tools(engine.todo))
            # The ruleset decides the child's tools: a wildcard deny REMOVES a tool
            # and `ask` always falls through to the approver — never the reverse, which is why a ruleset `allow` never prompts.
            # `never` is removed even when the ruleset would allow it — the interactive
            # doors and `primary_tools` are not the ruleset's to grant. The parent half
            # of `child_permission` is empty because a session's denies are not carried as
            # a rule list today (the task tool's `session_rules` hook arrives empty from
            # `build_engine`); it stays in the merge so that the day they are, a parent's
            # refusal still cannot be laundered through a child.
            for name in disabled_tools(engine.registry.names(), rules):
                engine.registry.remove(name)
            for name in never:
                engine.registry.remove(name)
            # `workflow` and `list_subagent_models` exist FOR `task` — `build_engine` offers
            # them only beside it ("a helper with no subagents to start would fail every
            # script"). The copy carried them into a child that may not delegate.
            if engine.registry.get("task") is None:
                for name in TASK_COMPANIONS:
                    engine.registry.remove(name)
            # The commit tool, bound to this child's schema. Registered AFTER the ruleset
            # filtering on purpose: it is the contract of THIS spawn, not a session tool
            # a ruleset may remove.
            captured: dict[str, Any] = {}
            if output_schema:
                engine.registry.register(capture_tool(output_schema, captured))
                engine.captured_output = captured  # type: ignore[attr-defined]
            # `run_code` names no tools, so the schema a child inherits is
            # already its own: what its program may call is its own registry, read when
            # the program runs.
            engine.permissions.rule_decider = rule_decider(rules)
            if output_schema:
                # A committed capture is FINAL: the ruleset gate is where every later
                # call is refused, so the child cannot keep working after it committed.
                engine.permissions.rule_decider = capture_decider(
                    engine.permissions.rule_decider, captured
                )
            # The rail answer is the child's REPORT, not a session handoff.
            engine.cap_prompt = CAP_PROMPT
            # The definition's own engine knobs, last (a workflow runner's script clock).
            prepare = getattr(agent, "prepare_engine", None)
            if prepare is not None:
                prepare(engine)
            # The manager-side wiring `get_engine` does for a session, so a child's
            # checkpoints are durable and its event log / compaction policy apply.
            engine.flush = lambda: self.flush_session(child_id, engine)
            engine.log = self.session_log(child_id)
            from harness.extensions.plugins import log_active

            log_active(engine.log, child.workspace if child is not None else None)
            engine.compaction_settings = lambda: self.compaction_settings(
                None
                if remote_child
                else (child.workspace if child is not None else None)
            )
            if child is not None and child.compaction:
                from harness.context.compaction import CompactionState

                engine.compaction_state = CompactionState.from_dict(child.compaction)
            # A resume rebuilds the engine; the previous one (and its shell) must go.
            previous = self._engines.pop(child_id, None)
            if previous is not None:
                _close_engine_quietly(previous)
            self._engines[child_id] = engine
            if child_id in self._cancel_requested:
                # A Stop that landed after the claim but before this engine existed
                # (`cancel_session`): now that there is something to pull, pull it.
                self._cancel_requested.discard(child_id)
                engine.request_interrupt()

            start = len(engine.messages)
            first_prompt = (
                f"{prompt}\n\n{capture_instruction(output_schema)}"
                if output_schema
                else prompt
            )
            # From here a parent's message joins this turn.
            with self._subagent_lock:
                self._live_child_turns.add(child_id)
            outcome = await self._drive_turn(
                child_id,
                engine,
                first_prompt,
                dead_letter=False,
                on_event=_watch,
            )
            outcome = await self._read_late_messages(child_id, engine, outcome, parent_id, _watch)
            report, structured_problem = _structured_report(engine, start, output_schema)
            if output_schema and structured_problem and not outcome.error:
                # One corrective retry: the validation errors ride the next prompt, and
                # the child's transcript keeps both attempts.
                with self._subagent_lock:
                    self._live_child_turns.add(child_id)
                outcome = await self._drive_turn(
                    child_id,
                    engine,
                    capture_correction(output_schema, structured_problem),
                    dead_letter=False,
                    on_event=_watch,
                )
                outcome = await self._read_late_messages(child_id, engine, outcome, parent_id, _watch)
                report, structured_problem = _structured_report(
                    engine, start, output_schema
                )
        except Exception as exc:  # a spawn crash is a result, not a parent crash
            self.mark_idle(child_id)
            return {
                "task_id": child_id,
                "status": f"error: {exc}",
                "report": "",
                "tokens": 0,
            }
        finally:
            with self._subagent_lock:
                self._live_child_turns.discard(child_id)
            if engine is not None:
                # The child's terminals die with its turn: a resume
                # rebuilds the engine and gets fresh ones, and a finished child must not
                # leave shells holding ports.
                _close_engine_terminals(engine)
            self._release_child_slot(parent_id, child_id)
            # The settle frame (P2): the running row learns the child's elapsed the moment
            # the turn ends, from the transcript pair `_child_duration_ms` reads. It is
            # emitted BEFORE the idle announcement and before the tool result, so the row
            # still owns it when `tool_finished` settles it.
            _emit_metadata(
                on_metadata,
                {
                    "session_id": child_id,
                    "toolcalls": tool_calls,
                    "ended_at": time.time(),
                    "duration_ms": _child_duration_ms(engine, start),
                },
            )
            # `_drive_turn`'s finally already released the claim (`mark_idle`); this
            # event is what makes the release visible to clients that are not viewing
            # the child. Parent sessions keep today's behaviour: no `session_status`,
            # so the child events stay purely additive to every existing stream.
            await self._announce_session_event(
                {
                    "type": "session_status",
                    "data": {"session_id": child_id, "status": "idle"},
                }
            )

        status = outcome.status or "completed"
        if output_schema and structured_problem and status != "error":
            status = (
                "error: structured output invalid after a retry — "
                + "; ".join(structured_problem[:3])
            )
        if status == "error":
            status = f"error: {outcome.error or 'unknown error'}"
        # The child's own record of what it changed (orchestration audit O3), a warning for
        # a file a sibling of this turn changed too (O4), and where a kept worktree holds
        # the work — `mark_idle` already released an empty one.
        files = changed_files(engine.messages[start:])
        overlaps = _sibling_overlaps(parent_engine, child_id, files)
        kept = self._child_worktrees.get(child_id)
        settle = getattr(agent, "settle_result", None)
        result = {
            "task_id": child_id,
            "status": status,
            "report": report or last_assistant_text(engine.messages[start:]) or "",
            "tokens": _usage_total(engine.messages[start:]),
            "changed_files": files,
            **({"overlaps": overlaps} if overlaps else {}),
            **(
                {"worktree": {"path": kept.get("path"), "branch": kept.get("branch")}}
                if kept
                else {}
            ),
            **(
                {"unisolated": self._worktree_unavailable.pop(child_id)}
                if child_id in self._worktree_unavailable
                else {}
            ),
        }
        # A definition whose work happens in ITS children (a workflow runner) reports their
        # files and spend instead of its own empty ones.
        return settle(child_id, result) if settle is not None else result

    def _start_background_child(
        self,
        *,
        parent_id: str,
        child_id: str,
        description: str,
        prompt: str,
        agent: AgentInfo,
        output_schema: Optional[dict[str, Any]] = None,
        on_metadata: Optional[Callable[[dict[str, Any]], None]] = None,
        parent_engine: Optional[Any] = None,
        route_pinned: bool = False,
    ) -> dict[str, Any]:
        """Detach a child's turn and register it (step D).

        The turn runs as one task on the manager loop, so every broadcast, checkpoint
        and idle hook it reaches is on the same thread of control as a user turn
        (started with the same session id). The registry entry is
        the completion writer's to clear and the map `cancel_session` consults for the
        child's loop; the task itself holds the child id, so the registry is bookkeeping,
        not control flow.

        Returns the running envelope immediately — the `task` tool's background branch
        already expects exactly this shape.
        """

        async def work() -> None:
            await self._settle_background(
                parent_id=parent_id,
                child_id=child_id,
                description=description,
                agent=agent,
                run=self._run_child_turn(
                    child_id=child_id,
                    parent_id=parent_id,
                    prompt=prompt,
                    agent=agent,
                    output_schema=output_schema,
                    on_metadata=on_metadata,
                    parent_engine=parent_engine,
                    route_pinned=route_pinned,
                ),
            )

        self._detach_background(child_id, parent_id, work)
        return {
            "task_id": child_id,
            "status": "running",
            "report": render_output(
                session_id=child_id,
                state="running",
                summary="Background task started",
                text=BACKGROUND_STARTED,
            ),
        }

    async def _read_late_messages(
        self,
        child_id: str,
        engine: Any,
        outcome: Any,
        parent_id: Optional[str],
        on_event: Callable[[str, dict[str, Any]], None],
    ) -> Any:
        """Give the parent's messages this turn missed a turn of their own.

        A turn reads what was queued for it after each step and before it finishes; one that
        arrived after that last read is still in the engine when the turn ends. Taking it and
        leaving `_live_child_turns` happen in one locked step, so a message is either taken
        here or, once the child has left, given to its job's queue or a resume instead. A
        turn that was stopped or failed takes nothing on: the stop is the answer.
        """
        while True:
            with self._subagent_lock:
                late = engine.take_steering()
                if not late or outcome.status == "interrupted" or outcome.error:
                    self._live_child_turns.discard(child_id)
                    return outcome
            outcome = await self._drive_turn(
                child_id,
                engine,
                "\n\n".join(text for text, _source, _display in late),
                source=parent_source(parent_id or ""),
                dead_letter=False,
                on_event=on_event,
            )

    async def _settle_background(
        self,
        *,
        parent_id: str,
        child_id: str,
        description: str,
        agent: Optional[AgentInfo] = None,
        run: Any,
    ) -> None:
        """Await a detached child's turn, drain its follow-ups, retire it, inject once (D/P6).

        The label feeds the parent's "still running" line (background_status.py) for exactly
        as long as the child is registered.

        The one completion writer shared by a `background=True` spawn and a foreground
        child that was promoted mid-run, so the two cannot disagree about the injection,
        the `interrupted` filter or the cleanup. `run` is an awaitable — a coroutine for
        the spawn, the already-running turn task for a promoted child.

        Follow-ups (`deliver_to_child`) run here, one after another, under the
        job's own definition (`agent`) when the message named none: the parent's single
        injected envelope is the LAST turn's report.

        The job leaves the registry BEFORE its result is injected, in the same locked step
        that finds its queue empty. Delivering the result opens the parent's next turn and
        waits for it; cleared only afterwards, the child still read as running in that turn,
        so a message the parent sent it there was queued, answered "sent", and dropped by
        the cleanup (session 1967dcdb-7d2, 2026-10-12). Retired first, that message resumes
        the child instead.
        """
        # One clock for the whole job, follow-ups included (O7): the thread path stops a child
        # at 900 s, and a manager child in the background used to run until it finished, a
        # person pressed Stop, or the repeat guard stepped in.
        with self._subagent_lock:
            self._background_labels[child_id] = description
        minutes = self._background_timeout_minutes(parent_id)
        deadline = time.monotonic() + minutes * 60 if minutes > 0 else None
        try:
            out = await self._bounded_child_turn(run, child_id, deadline, minutes)
            while True:
                with self._subagent_lock:
                    followups = self._background_followups.pop(child_id, None) or []
                    if not followups or str(out.get("status") or "").startswith("error: timed out"):
                        self._retire_background(child_id)
                        break
                for _desc, prompt, chosen, meta, engine in followups:
                    try:
                        out = await self._bounded_child_turn(
                            self._run_child_turn(
                                child_id=child_id,
                                parent_id=parent_id,
                                prompt=prompt,
                                agent=chosen or agent,
                                on_metadata=meta,
                                parent_engine=engine,
                            ),
                            child_id,
                            deadline,
                            minutes,
                        )
                    except Exception:  # one bad follow-up must not skip the injection
                        logger.exception(
                            "background child %s follow-up crashed", child_id
                        )
            await self._inject_background_result(
                parent_id=parent_id,
                child_id=child_id,
                description=description,
                result=out,
            )
        except Exception:  # the registry must clear even if the injection dies
            logger.exception("background child %s crashed", child_id)
        finally:
            with self._subagent_lock:
                self._retire_background(child_id)
                self._background_generation.pop(child_id, None)
            # A goal waits while a child of its session runs (`_goal_awaits_background`).
            # The result's own turn can end BEFORE this cleanup, so its idle edge would find
            # the child still registered and open nothing; asking again now, after the
            # registry is clear, is what lets the goal continue.
            self._schedule_goal_continue(parent_id)

    def _retire_background(self, child_id: str) -> None:
        """Take a background job out of the registry; the caller holds `_subagent_lock`. Its
        revert generation stays: the injection that follows reads it."""
        self._background_tasks.pop(child_id, None)
        self._background_parents.pop(child_id, None)
        self._background_labels.pop(child_id, None)
        self._background_loops.pop(child_id, None)
        self._background_followups.pop(child_id, None)

    def _background_timeout_minutes(self, parent_id: str) -> float:
        """`[delegation] background_timeout_minutes` for this parent's workspace, else the
        default. A bad value degrades to the default rather than to no clock."""
        try:
            parent = self.session_store.load(parent_id)
            config = load_config(parent.workspace if parent and not parent.environment_id else None)
            table = config.delegation if isinstance(config.delegation, dict) else {}
            value = float(table.get("background_timeout_minutes", BACKGROUND_TIMEOUT_MINUTES))
            return value if value >= 0 else BACKGROUND_TIMEOUT_MINUTES
        except Exception:
            return BACKGROUND_TIMEOUT_MINUTES

    async def _bounded_child_turn(
        self, run: Any, child_id: str, deadline: Optional[float], minutes: float
    ) -> dict[str, Any]:
        """Await one background turn until `deadline`, then stop it and say so.

        A stopped child settles `interrupted`, which the completion writer drops (a
        person's Stop must not reopen the parent). Past the clock nobody pressed Stop, so
        the result is re-stated as a failure that keeps the child's partial report — the
        parent learns the job ended and what it had found.
        """
        task = asyncio.ensure_future(run)
        if deadline is None:
            return await task
        try:
            return await asyncio.wait_for(asyncio.shield(task), max(0.0, deadline - time.monotonic()))
        except asyncio.TimeoutError:
            logger.info("background child %s stopped after %.0f min", child_id, minutes)
            self._cancel_session_tree(child_id, [])
            out = await task
            return {
                **(out or {}),
                "status": f"error: timed out after {minutes:g} min in the background",
            }

    def _detach_background(
        self,
        child_id: str,
        parent_id: str,
        work: Callable[[], Any],
    ) -> None:
        """Run a background child's turn somewhere that outlives the spawn call.

        The manager loop is the right home whenever it is running: it is where every
        other turn's broadcasts, checkpoints and idle hooks live, so the child shares
        one thread of control with the app. Without it — a bare manager in tests, or
        `_spawn_subagent_sync`'s `asyncio.run` bridge — the caller's loop is closed the
        moment the spawn returns, which would cancel the child it just announced. The
        child then rides a private loop on a daemon thread instead; the registry values
        are that thread and that loop rather than an `asyncio.Task` (the key, the
        lifetime and the completion notification are identical). The private loop's
        teardown is `task_runtime._run_child`'s: strays cancelled and settled, the
        executor left alone — a provider call may still be parked in a worker thread,
        and a spawn must never wait on a wedged model.
        """
        loop = self._loop
        if loop is not None and loop.is_running():
            with self._subagent_lock:
                self._note_background_child(child_id, parent_id)
                self._background_tasks[child_id] = loop.create_task(work())
                self._background_loops[child_id] = loop
            return

        private = asyncio.new_event_loop()

        def _run() -> None:
            asyncio.set_event_loop(private)
            try:
                private.run_until_complete(work())
            finally:
                try:
                    strays = [t for t in asyncio.all_tasks(private) if not t.done()]
                    for t in strays:
                        t.cancel()
                    if strays:
                        private.run_until_complete(
                            asyncio.gather(*strays, return_exceptions=True)
                        )
                    private.run_until_complete(private.shutdown_asyncgens())
                except Exception:
                    pass  # teardown is best-effort; the registry already settled
                private.close()

        thread = threading.Thread(
            target=_run, name=f"subagent-{child_id}", daemon=True
        )
        # Registered BEFORE the thread starts, so `cancel_session` can always find the
        # loop and the completion writer always finds an entry to clear.
        with self._subagent_lock:
            self._note_background_child(child_id, parent_id)
            self._background_tasks[child_id] = thread
            self._background_loops[child_id] = private
        thread.start()

    def _note_background_child(self, child_id: str, parent_id: str) -> None:
        """Register a background child and the revert generation it was born in.

        The generation is what lets a completion be judged later (see
        `_inject_background_result`): a child spawned before an `/undo` belongs to the span
        the person just hid, and its result must not reopen the parent with news about it.
        """
        self._background_parents[child_id] = parent_id
        self._background_generation[child_id] = self._revert_generation.get(parent_id, 0)

    async def _inject_background_result(
        self,
        *,
        parent_id: str,
        child_id: str,
        description: str,
        result: dict[str, Any],
    ) -> None:
        """Deliver a finished background child's envelope into the parent (D).

        The envelope becomes a synthetic text part on the parent session, so the model reads
        the child's result without a polling tool call while a client can skip rendering it
        as the person's own bubble. It uses the same split the connector messages already
        use: the envelope is the model-facing `content` (a normal user-role turn), and the
        display-only `source` sidecar carries the synthetic marker — `main_loop.py`
        strips `source` before every provider feed, so the marker never reaches the model.

        An `interrupted` child injects NOTHING: the person pressed Stop, and a stopped
        background job must not reopen the parent with a message nobody waits for (the
        same filter that applies to a cancelled job). A parent deleted
        while the child ran is likewise nobody to notify.
        """
        status = str((result or {}).get("status") or "")
        if status == "interrupted":
            return
        # FENCING (the branch generation): a child born before the parent's latest
        # `/undo` belongs to the span the person just hid. Delivering its result would put
        # news about an undone task into the visible tail, where the model reads it as work
        # it was told to do — so it is dropped, with a log line for anyone debugging why a
        # finished job went quiet. Fail-closed: the child is very likely cancelled too
        # (`cancel_session` walks descendants), and this is the belt to that suspenders.
        generation = self._background_generation.pop(child_id, 0)
        if self._revert_generation.get(parent_id, 0) > generation:
            logger.info(
                "dropped a background completion from a reverted span: child=%s parent=%s",
                child_id,
                parent_id,
            )
            return
        failed = status.startswith("error")
        text = str((result or {}).get("report") or "")
        if failed:
            # The reason AND what the child had written by then: a report alone hid why
            # the job failed (a clock, a provider error), the reason alone dropped its work.
            text = with_partial(status[7:].strip() or "unknown error", text)
        envelope = render_output(
            session_id=child_id,
            state="error" if failed else "completed",
            summary=(
                f"Background task failed: {description}"
                if failed
                else f"Background task completed: {description}"
            ),
            text=text,
            changes=changes_of(result or {}),
        )
        if self.session_store.load(parent_id) is None:
            return  # the parent was deleted while the child ran: no one to wake
        # The child's spend reaches the parent's token budget (O7): a foreground child's
        # tokens ride its tool result, a background one's had no carrier. The parent turn
        # that receives this envelope folds them in at its next step (`carry_delegated`).
        parent_engine = self._engines.get(parent_id)
        tokens = int((result or {}).get("tokens") or 0)
        if parent_engine is not None and tokens > 0:
            parent_engine.carry_delegated(child_id, tokens)
        # `steer_if_busy` keeps the default: a parent mid-turn gets the envelope at its
        # next step (exactly when the notification is useful), an idle parent opens a
        # fresh turn whose provider feed now carries the finished result.
        steps = (result or {}).get("steps")
        # A workflow run reports back as the run it is, with how many agents did its work: the
        # line read "Subagent finished" over a runner whose own transcript is one call.
        child = self.session_store.load(child_id)
        workflow = child is not None and child.agent == WORKFLOW_RUNNER
        await self.deliver_to_session(
            parent_id,
            envelope,
            # Display only: the envelope is the model's to read, and this is what a surface
            # says on the line that opens the turn it starts (which child, how it ended).
            source={
                "synthetic": "background_task",
                "session_id": child_id,
                "title": str(description or "")[:200],
                "state": "error" if failed else "completed",
                **({"steps": steps} if isinstance(steps, int) and not isinstance(steps, bool) else {}),
                **({"workflow": True, "agents": len(self.children_of(child_id))} if workflow else {}),
            },
        )

    def promote_subagent(self, session_id: str) -> dict[str, Any]:
        """Promote a session's running foreground `task` children to background (P6).

        Every running, non-background `task` job whose metadata names that parent session is
        promoted; the waiting tool call then returns the running envelope and the job's
        completion is injected like any other. This resolves the promotion future each
        foreground spawn parked in `_run_foreground_child`, for all of the session's direct
        children, since one turn may have delegated in parallel.

        The future is resolved ON the child's own loop (`call_soon_threadsafe`), so this
        method is safe from the route thread, a tool worker thread, or a test. Nothing
        running returns `{ok: false, error}` — a no-op, not an exception; a second promote
        of the same child finds it already gone from `_promotable` for the same reason.

        Nested grandchildren are out of scope: only the DIRECT children whose spawn named
        this session are promoted.
        """
        promoted: list[str] = []
        for child_id, entry in list(self._promotable.items()):
            if entry.get("parent_id") != session_id:
                continue
            future = entry.get("future")
            loop = entry.get("loop")
            if future is None or loop is None or future.done() or not loop.is_running():
                continue
            try:
                loop.call_soon_threadsafe(_resolve_promotion, future)
            except RuntimeError:  # the child's loop closed under us: nothing to promote
                continue
            promoted.append(child_id)
        if not promoted:
            return {"ok": False, "error": "no running foreground task to promote"}
        return {"ok": True, "session_id": session_id, "promoted": promoted}

    def cancel_session(self, session_id: str) -> dict[str, Any]:
        """Stop a session's live turn and every `task` descendant (step D).

        Cancelling a session cancels its own runner and then its descendants transitively,
        by walking the durable parent links (`children_of`): the same edge for a child
        session, which keeps working for a foreground child that never had a job entry. The intervention is `request_interrupt` — the
        rail the Stop button has always pulled — so each turn ends `interrupted`, the
        turn boundary is logged, and `_drive_turn` releases the claim (`mark_idle`).

        A child whose detached task has not built its engine yet (the window between
        `try_mark_running` and `_run_child_turn`'s build) is flagged in
        `_cancel_requested`; the turn reads the flag as it starts. A child that is
        already mid-turn is interrupted through its engine. Idle engines are interrupted
        too — harmless, since `_begin_turn_controls` clears the flag at the next turn,
        and it is what makes this a faithful replacement for the raw Stop frame.

        Returns `{"ok": True, "session_id": …, "interrupted": [ids]}` — the RUNNING
        sessions the stop reached, deepest last.
        """
        stopped: list[str] = []
        self._cancel_session_tree(session_id, stopped)
        return {"ok": True, "session_id": session_id, "interrupted": stopped}

    def _cancel_session_tree(self, session_id: str, stopped: list[str]) -> None:
        # A pending permission ask belongs to a turn that is about to end: resolve it
        # here, or its future leaks and the dock keeps drawing a prompt nothing can
        # answer. The `permission_replied` event is what clears the dock.
        self._cancel_permission_requests(session_id)
        # Calls queued for a slot have not started: Stop takes them out of line.
        for queued, _description in list(self._queued_spawns.get(session_id, ())):
            queued.cancel()
        # An external CLI child has no engine to interrupt: set its stop flag, which the
        # runner polls (and the process tree is killed there).
        external_stop = self._external_stops.get(session_id)
        if external_stop is not None:
            external_stop.set()
        engine = self._engines.get(session_id)
        if engine is not None:
            try:
                self._interrupt_engine(session_id, engine)
            except Exception:
                pass  # a dead engine must not block the stop
        if self.is_running(session_id):
            if engine is None:
                self._cancel_requested.add(session_id)
            stopped.append(session_id)
        for child_id in self.children_of(session_id):
            self._cancel_session_tree(child_id, stopped)

    def _interrupt_engine(self, session_id: str, engine: Any) -> None:
        """Pull a live engine's Stop rail, thread-safely when the engine runs elsewhere.

        A child on the manager loop is usually interrupted from that same loop (the WS
        Stop handler), where `request_interrupt` is just a flag write. The no-server-loop
        fallback runs the child on a private loop on a daemon thread, so its interrupt
        must be handed to that loop with `call_soon_threadsafe` — setting the engine's
        `asyncio.Event` from a foreign thread would not wake the loop that awaits it.
        """
        loop = self._background_loops.get(session_id)
        try:
            here = asyncio.get_running_loop()
        except RuntimeError:
            here = None
        if loop is not None and loop is not here and loop.is_running():
            loop.call_soon_threadsafe(engine.request_interrupt)
        else:
            engine.request_interrupt()

    def _spawn_subagent_sync(self, **kwargs: Any) -> dict[str, Any]:
        """The `task` tool's sync door into `spawn_subagent` (step B).

        Tools execute in a worker thread (`asyncio.to_thread`), while the child's turn
        must run on the server loop — the same loop every other turn uses, so
        broadcasts, checkpoints and the idle hooks see one thread of control.
        `run_coroutine_threadsafe` bridges the two; the worker blocks until the child's
        turn ends, which is exactly what a foreground `task` call is. Without a server
        loop (a bare manager in tests), the coroutine runs on a private loop.
        """
        to = kwargs.pop("to", None)
        if to is not None:
            # `message_task` names a child by its id or its name, and only one this
            # session started: resolved here, where the transcript and the rows are.
            from .subagent_messages import resolve_child

            child_id, why = resolve_child(self, str(kwargs.get("parent_id") or ""), str(to))
            if why:
                return {"error": why}
            kwargs["task_id"] = child_id
        if kwargs.pop("stop", False):
            # `message_task(stop=true)`: the parent stops its own background child.
            from .subagent_stop import stop_subagent

            return stop_subagent(self, str(kwargs.get("parent_id") or ""), str(kwargs.get("task_id") or ""))
        loop = self._loop
        if loop is None or loop.is_closed():
            return asyncio.run(self.spawn_subagent(**kwargs))
        future = asyncio.run_coroutine_threadsafe(self.spawn_subagent(**kwargs), loop)
        return future.result()

    def rename_session(self, session_id: str, title: str) -> dict[str, Any]:
        if session_id.startswith("__"):
            return {"ok": False, "error": "internal sessions cannot be renamed"}
        ok = self.session_store.rename(session_id, title)
        return {
            "ok": ok,
            "session_id": session_id,
            "title": " ".join((title or "").split())[:120],
        }

    def set_session_flags(
        self,
        session_id: str,
        *,
        pinned: Optional[bool] = None,
        archived: Optional[bool] = None,
    ) -> dict[str, Any]:
        if session_id.startswith("__"):
            return {"ok": False, "error": "internal sessions cannot be modified here"}
        ok = self.session_store.set_flags(session_id, pinned=pinned, archived=archived)
        return {"ok": ok, "session_id": session_id}

    def delete_session(self, session_id: str) -> dict[str, Any]:
        if session_id.startswith("__"):
            return {"ok": False, "error": "internal sessions cannot be deleted here"}
        # Child sessions are pruned with their parent: a child has no row in
        # any listing, so an orphan would be unreachable — and its engine, scratch dir
        # and per-session bindings would leak with it. Recursion covers grandchildren,
        # and each child is torn down exactly as a root is, below.
        for child_id in self.children_of(session_id):
            self.delete_session(child_id)
        # A deleted session cannot answer a parked permission ask (P1): settle its
        # futures and clear the dock, exactly as the Stop path does.
        self._cancel_permission_requests(session_id)
        # Its terminal panel's shell is a process tree of ours; deleting the session must
        # not leave it running (user_terminal.py).
        self.user_terminal_close(session_id)
        # ...and its PER-SESSION MCP connections (the built-in browser): the child
        # process is the session's, so it is released with it instead of idling until
        # the app ends.
        self.mcp.forget_scope(session_id)
        engine = self._engines.pop(session_id, None)
        if engine is not None:
            try:
                # (was engine.interrupt() — a method that never existed; the AttributeError
                # was silently swallowed, so deleting a running session never stopped it.)
                engine.request_interrupt()
            except Exception:
                pass
            # Its terminals are live process trees; a deleted session must not leave a
            # shell (or the dev server it started) running behind it.
            _close_engine_terminals(engine)
            # Its `session_end` hooks run now, while its files still exist (bounded).
            from .session_end import end_before_delete

            end_before_delete(engine, getattr(self, "_loop", None))
        record = self.session_store.load(session_id)
        ok = self.session_store.delete(session_id)
        # Ops rows go with the session: a deleted session must not keep inflating totals.
        self.metrics_store.remove_session(session_id)
        # Deleting a session is the one implicit unsubscribe (otherwise subscriptions are permanent).
        self.subscriptions.remove_session(session_id)
        # ...and releases any Slack threads it owned: the next tag there spawns fresh.
        self.mention_sessions.remove_session(session_id)
        # ...and drops its per-session connector overrides (like subscriptions).
        self.session_connections.remove_session(session_id)
        # A deleted session's share record goes with it; the remote copy is the user's to
        # revoke, and there is nothing local left to attach it to.
        self.share_store.clear(session_id)
        # ...and its per-session skill mutes (mutes die with the session).
        self.session_skills.remove_session(session_id)
        # ...and closes its pending Inbox items — an orphaned approval/question can never be
        # meaningfully answered (owner call, 2026-07-03).
        self.inbox.resolve_session(session_id)
        # ...and drops the session's "Allow always" permission grants (P1): a grant is
        # authority for THIS conversation, and the conversation is gone. Any future ask
        # from a reused id starts clean.
        self._permission_grants.pop(session_id, None)
        # ...and its scratch dirs. STRICTLY scoped: only a directory inside scratch_base is
        # removed — a real project folder the user picked is never touched. A session on a real
        # folder has its scratch BESIDE it (scratch_base/<id>), which was left behind on delete
        # until 2026-10-18; it goes too.
        if ok and record:
            scratch = self.scratch_base().resolve()
            owned = [Path(record.workspace)] if record.workspace else []
            if self._SESSION_ID_RE.match(session_id) and session_id not in {".", ".."}:
                owned.append(scratch / session_id)
            for ws in owned:
                try:
                    resolved = ws.resolve()
                    if (
                        resolved.is_relative_to(scratch)
                        and resolved != scratch
                        and resolved.is_dir()
                    ):
                        shutil.rmtree(resolved)
                except OSError:
                    pass  # a stale/foreign path must not fail the delete
        return {"ok": ok, "session_id": session_id}
