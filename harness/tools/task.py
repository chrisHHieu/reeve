"""The `task` tool — the subagent surface.

The call's shape (parameters, envelope, resume, background) is defined here; the text a model
reads about it is this app's own. What the model sees and gets back is the contract:

- **Parameters** `description`, `prompt`, `subagent_type`, `task_id`, `command`, plus
  `background`, and for a new task `name` and `effort`. There is no poll tool: a
  background task reports itself. What is said to a subagent afterwards, and stopping one, is
  `message_task` (`task_message.py`), registered beside this tool on a durable session.
- **Description** is `DESCRIPTION`, followed by the agents this caller may use
  (`Subagent types:`), which is itself a permission decision (`describe_task`).
- **Result** is the envelope:
  `<task id="…" state="completed"><task_result>…</task_result></task>` — the child's LAST
  assistant text and nothing else.
- **Background** returns `state="running"` immediately and injects the finished envelope
  into the session when the child ends. Nobody polls.
- **Resume** is `task_id`: the same child session continues with its context.
- **Failure** is the child's own error text: `Subagent failed (task_id: …): …`.

Differences that are the harness's, not the model's: a background completion wakes the
session through the wake store when the caller has one. A manager-backed session
(`spawner` set) makes the child a real session — its transcript IS the durable record —
and replaces the wake note with a synthetic envelope injected into the parent (steps
B/D of child sessions). Without a manager (CLI, unit tests) the child is a private thread and only its
step COUNT is kept for the result; there is no run store (step E).
"""

from __future__ import annotations

import itertools
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from . import output
from .contract import ToolContract, ToolPresentation, attach_contract, clip
from harness.llm.providers.registry import descriptor_configured, provider_descriptors
from harness.llm.providers.variants import levels_for
from harness.orchestration.subagent_models import models_for_provider, provider_profile, resolve_route, route_problem
from .task_agents import (
    ASK,
    DENY,
    AgentInfo,
    caller_task_rules,
    depth_limit_message,
    describe_task,
    evaluate,
    reject_message,
    relevant_rules,
    resolve_agents,
    subagent_depth_limit,
    subagent_parallel_limit,
)
from .json_schema import validate_output_schema
from .task_message import message_task_tool, name_problem
from .task_runtime import ChildResult, build_child_engine, child_rules, drive

#: What the call is, what the subagent gets and what comes back. WHEN to
#: delegate and how many is the system prompt's Delegation section, and only there: the
#: text this replaced said "launch multiple agents concurrently
#: whenever possible" beside a prompt that says one subagent for a focused question.
#: The second paragraph is load-bearing: it is what makes a model write a prompt that
#: stands on its own, which is the only thing a child gets.
DESCRIPTION = """Hand a piece of work to a subagent: a separate session with its own context window that works alone and reports back once.

The subagent gets `prompt` and nothing else. It cannot see this conversation or what the user asked, so the prompt states the goal, the files or sources it owns, whether it may change anything or only look, what to send back, and how to check its own work.

What comes back is its last message, as the result of this call, with the files it changed and a task_id. The user does not see that message: put what matters in your own answer, and check the report against the listed files before relying on it.

Use it for work that stands on its own and would otherwise fill this conversation with reading: a broad search, an independent part of the job, a check by fresh eyes. Read a file you can name, look up one thing, or take a step that needs this conversation yourself: those are faster here. Parts that do not depend on each other go in one step as several calls, each with its own files, and never two subagents editing one file. While a subagent has a piece of work, do not do that work yourself.

task_id continues a finished subagent with everything it has already read, instead of starting over. To tell a subagent something while it works, or to stop it, use message_task: give the task a name here to address it by."""

BACKGROUND_DESCRIPTION = (
    "background=true returns at once and the result arrives as a message when the subagent "
    "finishes. Use it only for work you do not need before going on; otherwise leave it "
    "off and the call returns the result."
)

#: How many subagents run at once, said where the model decides how many to start. The cap lived
#: only in a refusal: a model asked for seven at once, four ran, and three waited fourteen minutes
#: for a slot that never freed and failed (owner, 2026-10-21).
PARALLEL_DESCRIPTION = {
    "queued": (
        "This session runs at most {limit} subagents at once. A call past that is queued: it "
        "returns at once, the subagent starts by itself when a running one finishes, works in the "
        "background, and its result arrives as a message."
    ),
    "refused": "This session runs at most {limit} subagents at once; a call past that is refused.",
}

#: What a call answers when every slot is taken: it is in line, not started
#: and not lost, and its result comes the way a background task's does.
QUEUED = "\n".join(
    [
        "This task is queued: {running} subagents are already working and this session runs at most {limit} at once. It has NOT started.",
        "It starts by itself when one of them finishes, works in the background, and its result arrives as a message like any background task. Each turn's context lists the tasks still queued.",
        "DO NOT redo its work, call it again, sleep, or poll for it. Go on with work that does not depend on it, or tell the user what is queued and end your response.",
    ]
)

BACKGROUND_STARTED = "\n".join(
    [
        "The task is working in the background. You will be notified automatically when it finishes.",
        "Until that message arrives it is still running — silence means working, not lost; each turn's context lists the background subagents still running.",
        "DO NOT sleep, poll for progress, ask the task for status, or duplicate this task's work — avoid working with the same files or topics it is using.",
        "Work on non-overlapping tasks, or briefly tell the user what you launched and end your response.",
    ]
)

#: What a FOREGROUND call answers when its child went to the background anyway — a person
#: promoted it, or it outlived the call's own rail. It is not `BACKGROUND_STARTED`:
#: the caller asked to wait for a result and is being told it does not have one. In the real
#: store, 29 foreground calls came back with the started-notice ("work on non-overlapping
#: tasks"); the parent then touched the child's own files in 11 of them and edited files
#: while a `verify` child was still checking in 9.
BACKGROUND_MOVED = "\n".join(
    [
        "This task is still working and now continues in the background. Its result is NOT in this message.",
        "You will be notified automatically when it finishes. Until that message arrives it is still running — silence means working, not lost; each turn's context lists the background subagents still running.",
        "DO NOT redo its work, act as if it had answered, sleep, poll for progress, or ask the task for status.",
        "If your next step needs its result, tell the user what you are waiting for and end your response. Otherwise continue only with work that does not depend on it and does not touch the files or topics it is using.",
    ]
)

#: Child sessions are named by the run ids the run view already keys on, which keeps `task_id` and the stored record the same string — the resume path
#: depends on that.
_TIMEOUT_SECONDS = 900.0
#: How long a foreground call holds its caller. The thread path stops a child at
#: this rail; the manager, which can keep a child alive past its caller, hands a
#: still-working one over to the background here instead. The contract's budget (`_wrap`) is
#: this plus a minute, so the hand-over always beats the engine's "the turn stops waiting"
#: backstop — which would leave the child running with nobody to receive its result.
FOREGROUND_WAIT_SECONDS = _TIMEOUT_SECONDS
#: How many finished children stay resumable (their context is the expensive part).
_MAX_RESUMABLE = 3
#: How much of a failed child's last words the failure keeps.
_PARTIAL_CHARS = 4000


def with_partial(failure: str, report: Any) -> str:
    """A failure sentence plus whatever the child had written by then (orchestration audit
    O5): a child stopped by its deadline has usually found something, and "a partial answer
    the caller can weigh beats silence" (`task_runtime.drive`)."""
    text = report.strip() if isinstance(report, str) else ""
    if not text:
        return failure
    if len(text) > _PARTIAL_CHARS:
        text = text[:_PARTIAL_CHARS] + " …[partial report cut]"
    return f"{failure}. Partial report:\n{text}"


def render_output(
    *, session_id: str, state: str, text: str, summary: str = "", changes: str = ""
) -> str:
    """The envelope the parent model reads. `changes` is the
    child's `<changes>` block (`child_changes.changes_block`), after its result."""
    tag = "task_error" if state == "error" else "task_result"
    lines = [f'<task id="{session_id}" state="{state}">']
    if summary:
        lines.append(f"<summary>{summary}</summary>")
    lines.append(f"<{tag}>")
    lines.append(text)
    lines.append(f"</{tag}>")
    if changes:
        lines.append(changes)
    lines.append("</task>")
    return "\n".join(lines)


def changes_of(out: dict) -> str:
    """The `<changes>` block for a manager result that carries the child's record, or ''."""
    files = out.get("changed_files")
    if not isinstance(files, list):
        return ""
    from harness.orchestration.child_changes import changes_block

    return changes_block(
        files,
        overlaps=out.get("overlaps"),
        worktree=out.get("worktree"),
        unisolated=str(out.get("unisolated") or ""),
    )


def _denied_message(rules: Iterable[tuple[str, str, str]]) -> str:
    """The text a child reads when its spawn is denied by a rule."""
    rendered = ", ".join(f"{p}:{pat}" for p, pat, _ in rules)
    return (
        "The user has specified a rule which prevents you from using this specific tool "
        f"call. Here are some of the relevant rules [{rendered}]"
    )


def task_tools(
    *,
    workspace: str | Path,
    provider: Any,
    model: str,
    caller_agent: str = "general",
    agents: Optional[dict[str, AgentInfo]] = None,
    model_settings: Optional[dict[str, Any]] = None,
    timeout_seconds: Optional[float] = None,
    stop_flag: Optional[threading.Event] = None,
    undo: Optional[Any] = None,
    secrets: Optional[Any] = None,
    spill: Optional[Any] = None,
    wake_store: Optional[Any] = None,
    session_id: Optional[str] = None,
    session_rules: Optional[Iterable[tuple[str, str, str]]] = None,
    depth_of: Optional[Callable[[], int]] = None,
    #: The CALLER session's ancestry depth, counted by walking `parent_session` links at
    #: call time.
    #: A root session is 0, its child 1. None (CLI, unit tests, direct callers) means the
    #: tool cannot see a parent chain and counts itself as a root; the manager threads a
    #: callable because the answer changes as children are created.
    session_depth: Optional[Callable[[], int]] = None,
    #: The @-mention bypass: the spawn ask is skipped when the turn's user message carries an
    #: `agent` part. It is the same fact from the composer — an engine-side callable read per call, because
    #: the engine is built once and each turn may or may not be a mention turn. None = no
    #: mention machinery on this surface.
    mention_bypass: Optional[Callable[[], bool]] = None,
    approver: Optional[Any] = None,
    #: Steps a child may take; None = uncapped (the default), a positive number is
    #: the rail.
    steps: Optional[int] = None,
    config: Optional[Any] = None,
    #: The manager's sync door into `SessionManager.spawn_subagent` (step B).
    #: When set, `_start`/`_resume` spawn a real child SESSION through the manager's turn
    #: driver instead of running `task_runtime.drive` on a private thread; `task_id` is
    #: then a child session id that survives a restart. None (CLI, unit tests) keeps the
    #: thread path byte-for-byte.
    spawner: Optional[Callable[..., dict]] = None,
    #: The parent-session frame emitter for a spawned child's live metadata (step C
    #: of child sessions). `build_engine` threads the manager's emitter here; the tool wraps it per
    #: call with the call id `output.current_call()` exposes and hands the result to
    #: `spawner(..., on_metadata=…)`. The frame this tool emits is
    #: `{"type": "tool_metadata", "data": {"call_id": <the task call>, "metadata": {…}}}`
    #: — the name fits the `tool_proposed`/`tool_finished` family, and the GUI attaches
    #: `metadata` to the row it opened with that call_id. None (CLI, thread path) = no
    #: frames and no behaviour change.
    subagent_metadata: Optional[Callable[[dict], None]] = None,
    #: The session's sync door into the permission broker (P1). When set and the
    #: caller's ruleset evaluates `task.<subagent_type>` to `ask`, the spawn parks on a
    #: `permission_asked` frame first (permission "task",
    #: patterns [subagent_type], always ["*"], metadata {description, subagent_type}).
    #: It returns the reply verbatim ("once"/"always"/"reject");
    #: `reject` refuses the spawn with the standard reject string. None = today's
    #: behaviour (a ruleset `ask` still spawns; only the CLI/thread path reaches it).
    permission_asker: Optional[Callable[..., str]] = None,
    #: Whether a delegation call may pick its own LLM
    #: route. ON by default (the feature shipped on). OFF removes `provider`/`model`
    #: from the schema, refuses them if they arrive anyway, and does not register the
    #: `list_subagent_models` companion.
    model_selection: bool = True,
) -> list:
    """Build the session's `task` tool.

    One closure per session: the run table, the id counter and the resumable engines are the
    session's, so a `task_id` is only
    meaningful to the session that minted it.
    """
    workspace = str(Path(workspace).resolve())
    agents = agents or resolve_agents(config)
    caller = agents.get(caller_agent) or AgentInfo(name=caller_agent)
    rules = list(session_rules or [])
    # The session's own model, under a name the `task` tool's `model` override cannot
    # shadow: `resolve_route` needs it as the base a bare override extends.
    session_model = str(model or "")
    # The caller's effective ruleset for `task` decisions (defaults + global permission +
    # the caller's own). A persona caller has no definition
    # in `agents`, so without this layering `evaluate` would default to `ask` and every
    # spawn would prompt. Computed once; `_wrap`'s agent list reads the same rules.
    caller_rules = caller_task_rules(caller, config)
    deadline = timeout_seconds if timeout_seconds is not None else _TIMEOUT_SECONDS

    runs: dict[str, _Run] = {}
    #: Finished run ids, oldest first — the order `_keep_resumable` forgets them in.
    finished: list[str] = []
    runs_lock = threading.Lock()
    counter = itertools.count(1)

    def _live() -> int:
        with runs_lock:
            return sum(1 for r in runs.values() if not r.done.is_set())

    def _metadata_tap() -> Optional[Callable[[dict], None]]:
        """Bind the parent's frame emitter to THIS `task` call (step C).

        The engine dispatches every tool inside `output.capture(call_id, …)`
        (`engine/authorize.py`), so the contextvar is how a tool learns its own call id —
        the manager, which only sees the child, never does. The metadata observer is
        wrapped HERE and not at the manager: the frame needs the parent call id, and this
        is the only layer that has both facts. Best-effort: a dead socket must never fail
        the child, so the emitter call is guarded even though the manager's own broadcast
        already swallows send failures.
        """
        if subagent_metadata is None:
            return None
        call_id = output.current_call() or ""

        def tap(metadata: dict) -> None:
            try:
                subagent_metadata(
                    {
                        "type": "tool_metadata",
                        "data": {"call_id": call_id, "metadata": metadata},
                    }
                )
            except Exception:
                pass

        return tap

    def task(
        description: str,
        prompt: str,
        subagent_type: str,
        task_id: Optional[str] = None,
        command: Optional[str] = None,
        background: bool = False,
        inherit_context: bool = False,
        output_schema: Optional[dict] = None,
        provider: str = "",
        model: str = "",
        isolation: str = "",
        name: str = "",
        effort: str = "",
    ) -> dict:
        """Launch a new agent to handle complex, multistep tasks autonomously.

        Args:
            description (str): A short (3-5 words) description of the task.
            prompt (str): The task for the agent to perform. It must stand on its own: the
                child cannot see this conversation (unless `inherit_context` is true).
            subagent_type (str): The type of specialized agent to use for this task.
            task_id (str): Only if you mean to resume a previous task (a prior task_id
                continues the same subagent session instead of creating a fresh one). If
                that task is still running in the background, the prompt is delivered to
                it as additional context and it keeps running.
            command (str): The command that triggered this task.
            background (bool): Run the agent in the background. You will be notified when
                it completes. DO NOT sleep, poll, or proactively check on its progress.
            inherit_context (bool): Start the child from a copy of THIS conversation's
                history instead of a blank one. Use it when the task needs everything
                discussed so far — it costs the whole history in the child's context, so
                prefer a self-contained prompt when that is enough.
            output_schema (dict): An object-rooted JSON Schema for a STRUCTURED result
                (supported keywords: type, properties, required, additionalProperties,
                items, enum, const, oneOf). The child is told to answer with the object
                and its reply is validated; one corrective retry is allowed, then the
                delegation fails. The result's report is the JSON object.
            provider (str): Optional LLM provider override for a NEW task (e.g. a cheap
                route for bulk work). A registered, configured provider id; use
                `list_subagent_models` to discover ids. Either this or `model` may be
                given alone.
            model (str): Optional LLM model override for a NEW task: a bare id (keeps the
                provider) or a full `provider:model`. Not available when resuming.
            isolation (str): "worktree" gives a NEW task its own git worktree — a separate
                copy of the repository at the current commit — so children that write at
                the same time cannot overwrite each other.
            name (str): What to call a NEW task by later in `message_task`; one name per
                session.
            effort (str): How hard a NEW in-process task thinks, one of the levels its model
                offers (`list_subagent_models` with the model shows them).
        """
        # Depth FIRST, before the permission ask and the unknown-agent check, because walking
        # the ancestry decides whether nested subagents are allowed at all, and config `subagent_depth = 0` must refuse here
        # even if some caller registered the tool by hand.
        limit = subagent_depth_limit(config)
        depth = session_depth() if session_depth is not None else 0
        if depth >= limit:
            return {"error": depth_limit_message(limit)}
        # Parameter validation before the permission ask: a malformed call must be
        # answered, not parked on a consent card the user cannot make true.
        if inherit_context and task_id:
            return {
                "error": (
                    "inherit_context only applies to a new task — a resumed task "
                    "already carries its own context."
                )
            }
        name = str(name or "").strip()
        effort = str(effort or "").strip().lower()
        if (name or effort) and task_id:
            return {
                "error": (
                    "name and effort only apply to a new task — a resumed task keeps its name "
                    "and the way it thinks."
                )
            }
        if name and name_problem(name):
            return {"error": name_problem(name)}
        if effort and spawner is None:
            return {
                "error": (
                    "effort needs a durable session (the desktop app); omit it and the child "
                    "thinks the way its agent type is set up to."
                )
            }
        isolation = str(isolation or "").strip().lower()
        if isolation and isolation != "worktree":
            return {"error": f'isolation must be "worktree" or omitted (got "{isolation}")'}
        if isolation and task_id:
            return {
                "error": (
                    "isolation only applies to a new task — a resumed task keeps the "
                    "folder it has been working in."
                )
            }
        if inherit_context and spawner is None:
            return {
                "error": (
                    "inherit_context needs a durable session (the desktop app); omit it "
                    "to run the child with a self-contained prompt."
                )
            }
        if output_schema is not None:
            problem = validate_output_schema(output_schema)
            if problem:
                return {"error": problem}
            if task_id:
                return {
                    "error": (
                        "output_schema only applies to a new task — a resumed task "
                        "already carries its own instructions."
                    )
                }
            if spawner is None:
                return {
                    "error": (
                        "output_schema needs a durable session (the desktop app); omit "
                        "it to run the child with a plain answer."
                    )
                }
        override_provider = str(provider or "").strip()
        override_model = str(model or "").strip()
        if override_provider or override_model:
            if not model_selection:
                return {
                    "error": (
                        "model selection is turned off in Settings ▸ Subagents — this "
                        "child runs on the model its agent type is configured with; "
                        "turn the switch on to pick a route per call."
                    )
                }
            # Route overrides are for a FRESH child: a resumed session keeps the model it
            # started on (its context was built by that model's tokenizer/pricing), and a
            # silent mid-session switch would misprice and misroute it.
            if task_id:
                return {
                    "error": (
                        "provider/model only apply to a new task — a resumed task keeps "
                        "the model it started on; start a fresh task to switch."
                    )
                }
            # The two CLI agents are AGENT TYPES, not LLM providers: a provider string
            # naming one gets a targeted answer instead of "not registered", because
            # `list_subagent_models` lists them right beside the real providers.
            from harness.orchestration.subagent_external import PROVIDER_BINARIES

            if override_provider in PROVIDER_BINARIES:
                target = (
                    f'subagent_type="{override_provider}"'
                    + (f', model="{override_model}"' if override_model else "")
                )
                return {
                    "error": (
                        f'"{override_provider}" is a CLI subagent (an agent type), not '
                        f"an LLM provider — call task({target}) instead."
                    )
                }
            problem = route_problem(override_provider, secrets)
            if problem:
                return {"error": problem}
        # The @-mention bypass: the person named the agent, so the spawn ask is skipped
        # entirely and does not consult the caller's ruleset on this path either (the mention IS
        # the consent). The caller's deny therefore yields to an explicit mention, which
        # is exactly why the synthetic instruction carries "guaranteed to exist".
        bypass = bool(mention_bypass()) if mention_bypass is not None else False
        action = evaluate("task", subagent_type, caller_rules + list(rules))
        if action == DENY and not bypass:
            return {
                "error": _denied_message(
                    relevant_rules(caller_rules + list(rules), "task")
                )
            }
        if action == ASK and permission_asker is not None and not bypass:
            # The spawn ask: the permission is `task`, the pattern is the agent type, `always` is `*` (one
            # "Allow always" covers every agent for this session), and the metadata
            # carries what the dock shows. The ask happens BEFORE the unknown-agent
            # check. A reject refuses the spawn with the standard reject string; the ruleset
            # `ask` on the CLI/thread path (no
            # asker) keeps today's behaviour of spawning unprompted.
            call_id = output.current_call() or ""
            verdict = permission_asker(
                permission="task",
                patterns=[subagent_type],
                metadata={
                    "description": description,
                    "subagent_type": subagent_type,
                },
                always=["*"],
                tool={"call_id": call_id} if call_id else None,
            )
            if verdict == "reject":
                return {"error": reject_message()}
        agent = agents.get(subagent_type)
        if agent is None:
            return {
                "error": f"Unknown agent type: {subagent_type} is not a valid agent type"
            }
        if getattr(agent, "disabled", False):
            # Turned off in Settings ▸ Subagents (`disabled_subagents.json`): the row is
            # still listed there so it can be switched back on, but delegation to it is
            # refused here — before any route override or resume path runs.
            return {
                "error": (
                    f"{agent.name} is turned off in Settings ▸ Subagents — turn it back "
                    "on there to delegate to it."
                )
            }
        # The override's base is what the child would have run WITHOUT it: the agent's own
        # model when it pins one, else the session's. A malformed pair (a model that names
        # a different provider) is answered here, before any child exists.
        route = ""
        if override_provider or override_model:
            cli = str(getattr(agent, "provider", "") or "")
            if cli:
                # A CLI child IS its own provider: it takes a BARE model id from its
                # account's own catalog (`--model`), never a provider prefix.
                if override_provider:
                    return {
                        "error": (
                            f"{agent.name} runs on the {cli} CLI's own provider — pass a "
                            "bare `model` instead, or neither."
                        )
                    }
                if ":" in override_model:
                    return {
                        "error": (
                            f"the {cli} CLI takes a bare model id from its own account "
                            'catalog — drop the provider prefix (see '
                            f'list_subagent_models with provider="{cli}"), or drop `model`.'
                        )
                    }
                route = override_model
            else:
                route, problem = resolve_route(
                    agent.model or session_model, override_provider, override_model
                )
                if problem:
                    return {"error": problem}
        if task_id:
            return _resume(task_id, prompt, description, agent)
        # An agent defined to run in the background (a plugin agent's `background: true`) does,
        # where there is a session to notify; elsewhere it runs in the foreground.
        if getattr(agent, "background", False) and wake_store is not None and session_id:
            background = True
        if background and (wake_store is None or not session_id):
            # This is gated on the one thing it actually needs. Without a durable session there is nobody to
            # notify, and the old poll tool that papered over that is gone.
            return {
                "error": (
                    "background tasks need a durable session (the desktop app); run it "
                    "in the foreground instead"
                )
            }
        live_limit = subagent_parallel_limit(config)
        if live_limit > 0 and _live() >= live_limit:
            return {
                "error": (
                    f"{_live()} tasks are already running in this session and the limit "
                    f"is {live_limit} — wait for one to finish."
                )
            }
        return _start(
            description=description,
            prompt=prompt,
            agent=agent,
            background=background,
            command=command,
            inherit_context=inherit_context,
            output_schema=output_schema,
            route=route,
            override_provider=override_provider,
            override_model=override_model,
            isolation=isolation,
            name=name,
            effort=effort,
        )

    # -- the run ---------------------------------------------------------------

    def _start(
        *,
        description: str,
        prompt: str,
        agent: AgentInfo,
        background: bool,
        command: Optional[str],
        inherit_context: bool = False,
        output_schema: Optional[dict] = None,
        resumed: Optional["_Run"] = None,
        route: str = "",
        override_provider: str = "",
        override_model: str = "",
        isolation: str = "",
        name: str = "",
        effort: str = "",
    ) -> dict:
        if spawner is not None:
            # The manager owns child sessions: the spawn creates/reuses a real
            # session row, drives it through the same turn driver a user turn uses, and
            # reports back the child's last text + tokens. No run table, no thread — the
            # child's transcript IS the record. `background=True` is
            # supported here too (step D): the manager detaches the turn and answers with
            # the same running envelope this tool's thread branch returns, then injects
            # the finished envelope into the parent itself. `inherit_context` seeds the
            # new child with the parent's transcript.
            return _spawned(
                spawner(
                    parent_id=session_id or "",
                    description=description,
                    prompt=prompt,
                    agent_name=agent.name,
                    background=background,
                    task_id=resumed.id if resumed is not None else None,
                    inherit_context=inherit_context,
                    output_schema=output_schema,
                    # A fresh child's route override (`provider` and `model`): the manager resolves it against the child row it creates,
                    # and refuses it on a resume the same way this tool does.
                    provider=override_provider,
                    model=override_model,
                    # The call this spawn belongs to (a workflow sub-call answers with
                    # `<root>:code:<n>`), so the host can kill a run's agents with it.
                    origin=output.current_call() or "",
                    on_metadata=_metadata_tap(),
                    # Only when asked: every existing spawner signature stays valid.
                    **({"isolation": isolation} if isolation else {}),
                    **({"name": name} if name else {}),
                    **({"effort": effort} if effort else {}),
                )
            )
        if name and resumed is None:
            # Without a manager the run id IS the name, so the id a result shows is the one
            # the model chose.
            with runs_lock:
                if name in runs:
                    return {"error": f'this session already has a task called "{name}": pick another name'}
        run_id = (name or f"{agent.name}-{next(counter)}") if resumed is None else resumed.id
        engine = resumed.engine if resumed is not None else _child_engine(agent, route)
        run = _Run(
            id=run_id,
            agent=agent,
            engine=engine,
            description=description,
            prompt=prompt,
            stop=resumed.stop if resumed is not None else threading.Event(),
        )
        with runs_lock:
            runs[run_id] = run
        run.background = background

        def work() -> None:
            try:
                out = drive(
                    engine,
                    prompt,
                    stop_events=(run.stop, stop_flag) if stop_flag is not None else (run.stop,),
                    deadline=deadline,
                )
            except Exception as exc:  # a child crash is a result, not a parent crash
                out = ChildResult(status=f"error: {exc}")
            finally:
                run.steps = out.steps
                run.report = out.report
                run.error = _failed(out)
                run.status = out.status
                run.tokens = out.tokens
                run.done.set()
                _deliver(run)
                if run.engine is not None:
                    try:
                        run.engine.executor.close()
                    except Exception:
                        pass
                _keep_resumable(run.id)

        if background:
            threading.Thread(target=work, name=run_id, daemon=True).start()
            return {
                "task_id": run_id,
                "status": "running",
                "report": render_output(
                    session_id=run_id,
                    state="running",
                    summary="Background task started",
                    text=BACKGROUND_STARTED,
                ),
            }
        work()
        return _foreground_result(run)

    def _keep_resumable(finished_id: str) -> None:
        """Keep the last `_MAX_RESUMABLE` finished runs; forget the older ones (O6).

        Each run holds its child's whole engine and history, and the table used to grow for
        the life of the session — an editor session open all day, an automation spawning
        many children. A forgotten `task_id` gets the "its context is gone" answer the
        resume path already gives. A run resumed and running again is never forgotten.
        """
        with runs_lock:
            if finished_id in finished:
                finished.remove(finished_id)
            finished.append(finished_id)
            while len(finished) > _MAX_RESUMABLE:
                old = finished.pop(0)
                stale = runs.get(old)
                if stale is not None and stale.done.is_set():
                    runs.pop(old, None)
                    stale.engine = None

    def _foreground_result(run: "_Run") -> dict:
        failed = _failed_of(run)
        if failed:
            return {"error": failed}
        return {
            "task_id": run.id,
            "status": run.status,
            "tokens": run.tokens,
            "steps": run.steps,
            "report": render_output(
                session_id=run.id, state="completed", text=run.report
            ),
        }

    def _failed_of(run: "_Run") -> str:
        if run.error:
            failure = f"Subagent failed (task_id: {run.id}): {run.error}"
        elif run.status.startswith("error"):
            failure = f"Subagent failed (task_id: {run.id}): {run.status[7:]}"
        elif run.status == "interrupted":
            failure = f"Subagent failed (task_id: {run.id}): Task cancelled"
        elif run.status == "timeout":
            failure = f"Subagent failed (task_id: {run.id}): timed out after {int(deadline)}s"
        elif not run.report.strip():
            return f"Subagent failed (task_id: {run.id}): it produced no report"
        else:
            return ""
        return with_partial(failure, run.report)

    def _failed(out: ChildResult) -> str:
        """The task fails when the child's last tool part errored."""
        if out.errors and out.status in ("completed", "unknown"):
            return str(out.errors[-1])
        return ""

    def _deliver(run: "_Run") -> None:
        """A finished background task notifies its session: the envelope goes INTO the conversation, so the parent
        model reads the result without a tool call. Our channel for that is the completion
        wake, whose note is that envelope.

        This is the NO-MANAGER path (CLI, TUI, an engine built without
        `subagent_spawner`): the wake store is the session's only durable door back in.
        With a manager present the spawner branch of `_start` returns before any `_Run`
        exists, and the manager injects the finished envelope itself
        (`SessionManager._inject_background_result`) — so a managed session must never
        see BOTH a wake note and an injected message for one child."""
        if not run.background or wake_store is None or not session_id:
            return
        envelope = render_output(
            session_id=run.id,
            state="error" if _failed_of(run) else "completed",
            summary=(
                f"Background task failed: {run.description}"
                if _failed_of(run)
                else f"Background task completed: {run.description}"
            ),
            text=run.report or (_failed_of(run) or "no report"),
        )
        try:
            wake_store.add_completion(
                session_id,
                run.id,
                note=envelope,
                depth=depth_of() if depth_of is not None else 0,
            )
        except Exception:  # a wake that cannot be written must not fail the run
            pass

    def _child_engine(agent: AgentInfo, route: str = "") -> Any:
        merged = child_rules(agent) + list(rules)
        return build_child_engine(
            workspace=workspace,
            provider=provider,
            model=route or agent.model or model,
            agent=agent,
            model_settings=model_settings,
            max_iterations=agent.steps if agent.steps is not None else steps,
            rules=merged,
            undo=undo,
            secrets=secrets,
            spill=spill,
            approver=approver,
        )

    def _spawned(out: dict) -> dict:
        """The tool-side shape of a `spawn_subagent` result (step B).

        The parent model must not be able to tell which harness ran the child, so the
        manager's `{task_id, status, report, tokens}` is wrapped in the same `<task …>`
        envelope and `Subagent failed (task_id: …)` failure shape the thread path
        returns. A refusal before a session existed (unknown agent, background) is
        already a finished error sentence and is passed through verbatim.

        A `status="running"` result (step D — `spawn_subagent`'s background
        branch) is passed through as it stands: the manager already rendered the running
        envelope, and wrapping it as `state="completed"` would tell the model the child
        was done while it works.
        """
        task_id = str((out or {}).get("task_id") or "")
        status = str((out or {}).get("status") or "")
        error = str((out or {}).get("error") or "")
        if error and not task_id:
            return {"error": error}
        if status in ("running", "queued"):
            report = out.get("report")
            if isinstance(report, str) and report.strip():
                return {"task_id": task_id, "status": status, "report": report}
        if status == "interrupted":
            return {
                "error": with_partial(
                    f"Subagent failed (task_id: {task_id}): Task cancelled", out.get("report")
                )
            }
        if error or status.startswith("error"):
            reason = error or status[7:].strip() or "unknown error"
            return {
                "error": with_partial(
                    f"Subagent failed (task_id: {task_id}): {reason}", out.get("report")
                )
            }
        report = out.get("report")
        if not isinstance(report, str) or not report.strip():
            return {
                "error": f"Subagent failed (task_id: {task_id}): it produced no report"
            }
        return {
            "task_id": task_id,
            "status": status or "completed",
            "tokens": int(out.get("tokens") or 0),
            # The child's session transcript is the step record now;
            # the presentation keeps the field so the GUI keeps its column.
            "steps": int(out.get("steps") or 0),
            # What the child changed, next to what it said (orchestration audit O3).
            **(
                {"changed_files": out["changed_files"]}
                if isinstance(out.get("changed_files"), list)
                else {}
            ),
            "report": render_output(
                session_id=task_id, state="completed", text=report, changes=changes_of(out)
            ),
        }

    def _resume(task_id: str, prompt: str, description: str, agent: AgentInfo) -> dict:
        if spawner is not None:
            # A session-backed child resumes from its own store — the same child even
            # across a restart (step B). The thread path's in-memory `runs`
            # table is its substitute for that, and is not consulted here.
            return _spawned(
                spawner(
                    parent_id=session_id or "",
                    description=description or "",
                    prompt=prompt,
                    agent_name=agent.name,
                    background=False,
                    task_id=task_id,
                    on_metadata=_metadata_tap(),
                )
            )
        with runs_lock:
            run = runs.get(task_id)
        if run is None or run.engine is None:
            return {
                "error": (
                    f"Unknown task_id: {task_id} — its context is gone, so start a fresh "
                    "task (omit task_id) and include what it needs."
                )
            }
        if not run.done.is_set():
            return {"error": f"{task_id} is still running — wait for it to finish."}
        if run.tokens > 400_000:
            return {
                "error": (
                    f"{task_id} already spent {run.tokens:,} tokens — carrying that "
                    "context further is cheaper to rebuild: start a fresh task."
                )
            }
        run.done.clear()
        return _start(
            description=description or run.description,
            prompt=prompt,
            agent=run.agent,
            background=False,
            command=None,
            resumed=run,
        )

    # -- the tool surface ------------------------------------------------------

    wrapped = _wrap(
        task,
        AgentInfo(name=caller.name, permission=caller_rules),
        agents,
        model_selection=model_selection,
        parallel=(subagent_parallel_limit(config), "queued" if spawner is not None else "refused"),
    )
    # Talking to a subagent after its brief: only a durable session has children that
    # outlive the call that started them.
    if spawner is not None:
        wrapped.append(
            message_task_tool(
                spawner=spawner,
                session_id=session_id or "",
                metadata_tap=_metadata_tap,
                settle=_spawned,
            )
        )
    if not model_selection:
        return wrapped
    return [*wrapped, *route_tools(secrets=secrets)]


def route_tools(*, secrets: Any = None) -> list:
    """The delegation companion: `list_subagent_models`, the discovery tool.

    Registered beside `task` so the ids a route override accepts are discoverable where
    the override lives. It reads the same provider registry and SecretStore the router
    uses — no manager, no new data source — and the model list is the one the provider's
    own setup page shows (live, cached, failure-tolerant).
    """

    def list_subagent_models(provider: str = "", model: str = "") -> str:
        """Discover LLM routes for subagents without changing the current agent.

        Call with no arguments to list the providers configured for this session, with
        `provider` to list that provider's advertised models, or with `provider` and
        `model` to inspect one exact model. The `provider` ids are LLM ROUTES for the
        `task` tool's `provider`/`model` fields; the two CLI entries (codex,
        claude-code) are AGENT TYPES instead — pass them as `subagent_type` and give
        one of their catalog ids as `model`. Catalog membership is advisory: an adapter
        may accept an unlisted id.

        Args:
            provider (str): Registered provider id. Omit to list providers.
            model (str): Exact model id to inspect. Requires `provider`; omit to list
                that provider's advertised models.
        """
        name = str(provider or "").strip()
        wanted = str(model or "").strip()
        if wanted and not name:
            return "`model` requires `provider`."
        if not name:
            rows = [
                f"{d.name} — {d.title}"
                for d in provider_descriptors()
                if descriptor_configured(d, provider_profile(secrets, d.name))
            ]
            # The two CLI subagents are listed too — but they are AGENT TYPES, not
            # routes, and the label says so: a provider string naming one is refused.
            from harness.orchestration.subagent_external import PROVIDER_BINARIES

            rows += [
                f'{cli} — CLI subagent: call task(subagent_type="{cli}"), not a provider'
                for cli in sorted(PROVIDER_BINARIES)
            ]
            return "\n".join(rows)
        from harness.orchestration.subagent_external import PROVIDER_BINARIES

        if name in PROVIDER_BINARIES:
            return _cli_models(name, wanted)
        problem = route_problem(name, secrets)
        if problem:
            return problem
        if wanted:
            route = f"{name}:{wanted}"
            levels = levels_for(route)
            lines = [route]
            if levels:
                lines.append(
                    "Reasoning levels: " + ", ".join(str(level) for level in levels)
                )
            else:
                lines.append("(no advertised reasoning levels)")
            return "\n".join(lines)
        result = models_for_provider(name, secrets)
        if not result.get("ok"):
            reason = result.get("error") or "unavailable"
            return f"(no advertised models for {name}: {reason})"
        models = [str(item) for item in (result.get("models") or []) if str(item).strip()]
        if not models:
            return f"(no advertised models for {name})"
        lines = [f"{name}:{item}" for item in models[:200]]
        if len(models) > 200:
            lines.append(f"… and {len(models) - 200} more")
        return "\n".join(lines)

    return [list_subagent_models]


def _cli_models(provider: str, wanted: str) -> str:
    """One CLI subagent's catalog: its own account's models, bare ids (the CLI takes no
    provider prefix — `--model <id>`), from the same fetch the row's detail uses."""
    from harness.orchestration.subagent_external import list_models

    if wanted:
        return (
            f'{provider} (agent type) · model "{wanted}"\n'
            f'(call task(subagent_type="{provider}", model="{wanted}"): the CLI takes '
            "the bare id, not a provider route)"
        )
    result = list_models(provider)
    if not result.get("ok"):
        reason = result.get("error") or "unavailable"
        return f"(no advertised models for {provider}: {reason})"
    rows = [
        f"{str(item.get('id'))} — {str(item.get('name') or item.get('id'))}"
        for item in (result.get("models") or [])
        if isinstance(item, dict) and item.get("id")
    ]
    if not rows:
        return f"(no advertised models for {provider})"
    rows = rows[:200]
    rows.append(
        f'(use one as `model` with task(subagent_type="{provider}"); not a provider route)'
    )
    return "\n".join(rows)


class _Run:
    """One child run: its engine, its rails, and its last report."""

    __slots__ = (
        "id",
        "agent",
        "engine",
        "description",
        "prompt",
        "stop",
        "done",
        "report",
        "error",
        "status",
        "tokens",
        "steps",
        "background",
        "created_at",
    )

    def __init__(
        self,
        *,
        id: str,
        agent: AgentInfo,
        engine: Any,
        description: str,
        prompt: str,
        stop: threading.Event,
    ) -> None:
        self.id = id
        self.agent = agent
        self.engine = engine
        self.description = description
        self.prompt = prompt
        self.stop = stop
        self.done = threading.Event()
        self.report = ""
        self.error = ""
        self.status = "running"
        self.tokens = 0
        self.steps: int = 0
        self.background = False
        self.created_at = time.time()


def _wrap(
    task_fn: Any,
    caller: AgentInfo,
    agents: dict[str, AgentInfo],
    *,
    model_selection: bool = True,
    parallel: tuple[int, str] = (0, "refused"),
) -> list:
    """The registry entry: schema, description with the agent list, and the contract. `parallel`
    is the session's cap and what a call past it gets; a cap of 0 says nothing."""
    limit, past_it = parallel
    description = (
        DESCRIPTION
        + "\n\n"
        + BACKGROUND_DESCRIPTION
        + ("\n\n" + PARALLEL_DESCRIPTION[past_it].format(limit=limit) if limit > 0 else "")
        + "\n\n"
        + describe_task(agents, caller)
    )
    task_fn.__doc__ = _docstring(description, model_selection)
    task_fn.__tool_schema__ = _schema(description, model_selection)
    task_fn.__tool_policy__ = _policy
    attach_contract(
        task_fn,
        ToolContract(
            render=_render,
            present_call=_present_call,
            present_result=_present_result,
            facts=_facts,
            timeout_ms=int(_TIMEOUT_SECONDS * 1000) + 60_000,
        ),
    )
    return [task_fn]


def _docstring(description: str, model_selection: bool = True) -> str:
    text = (
        description
        + "\n\n        Args:\n"
        "            description (str): A short (3-5 words) description of the task.\n"
        "            prompt (str): The task for the agent to perform. It must stand on its own.\n"
        "            subagent_type (str): The type of specialized agent to use for this task.\n"
        "            task_id (str): Only to resume a finished task; continues its session. "
        "To tell a running one something, or stop it, use message_task.\n"
        "            name (str): What to call a NEW task by in message_task; one name per "
        "session.\n"
        "            effort (str): How hard a NEW task thinks: a level its model offers.\n"
            "            command (str): The command that triggered this task.\n"
            "            background (bool): Run it in the background; you are notified when it finishes.\n"
            "            inherit_context (bool): Start the child from a copy of THIS "
            "conversation's history (only for a new task).\n"
    )
    if model_selection:
        text += (
            "            provider (str): Optional LLM provider for a NEW task; use "
            "list_subagent_models to discover ids. Not for CLI agents: codex / "
            "claude-code go in subagent_type.\n"
            "            model (str): Optional model id (bare, or provider:model) for a "
            "NEW task; a CLI child takes a bare id from its account catalog.\n"
        )
    return text


def _schema(description: str, model_selection: bool = True) -> dict[str, Any]:
    """Written out, not inferred: the descriptions are the model's whole instruction set
    for delegating, and inference would flatten them."""
    properties: dict[str, Any] = {
        "description": {
            "type": "string",
            "description": "A short (3-5 words) description of the task",
        },
        "prompt": {"type": "string", "description": "The task for the agent to perform"},
        "subagent_type": {
            "type": "string",
            "description": "The type of specialized agent to use for this task",
        },
        "task_id": {
            "type": "string",
            "description": (
                "This should only be set if you mean to resume a finished task (you "
                "can pass a prior task_id and the task will continue the same "
                "subagent session as before instead of creating a fresh one). To tell a "
                "task something while it is still working, or to stop it, use message_task"
            ),
        },
        "name": {
            "type": "string",
            "description": (
                "A short name for a NEW task (lowercase letters, digits and dashes, e.g. "
                "\"survey\"), to address it by in message_task instead of its task_id. One "
                "name per session"
            ),
        },
        "effort": {
            "type": "string",
            "description": (
                "How hard a NEW in-process task thinks: one of the levels its model offers "
                "(list_subagent_models with the provider and model shows them). Low for "
                "mechanical work, high for a review or a hard search. Omit it and the task "
                "thinks the way its agent type is set up to"
            ),
        },
        "command": {
            "type": "string",
            "description": "The command that triggered this task",
        },
        "background": {
            "type": "boolean",
            "description": (
                "Run the agent in the background (see the tool's description). A "
                "foreground task is waited for and returns its result. It answers "
                "state=running only when the user moves it to the background mid-run or "
                "it outlives the call's time limit — not an error, and not a result: do "
                "not redo its work, and end your turn if your next step needs it"
            ),
        },
        "inherit_context": {
            "type": "boolean",
            "description": (
                "Start the child from a copy of THIS conversation's history instead "
                "of a blank one, for a task that needs everything discussed so far. "
                "It costs the whole history in the child's context — prefer a "
                "self-contained prompt when that is enough. Only for a new task"
            ),
        },
        "isolation": {
            "type": "string",
            "enum": ["worktree"],
            "description": (
                "\"worktree\" gives a NEW task its own git worktree: a separate copy of the "
                "repository at the current commit (your uncommitted changes are not in "
                "it). Use it for children that WRITE at the same time, so they cannot "
                "overwrite each other. The child's changes stay in that copy: the result "
                "names it, and the user is offered Apply and Discard — they are not in "
                "your workspace until the user applies them. Needs a git repository; "
                "where it cannot be set up the result says so and the child works here"
            ),
        },
        "output_schema": {
            "type": "object",
            "description": (
                "An object-rooted JSON Schema for a STRUCTURED result. The child is "
                "told to answer with the object and its reply is validated; one "
                "corrective retry is allowed, then the task fails. Keywords: type, "
                "properties, required, additionalProperties, items, enum, const, "
                "oneOf. The result's report is the JSON object. Only for a new task, "
                "in-process agents only"
            ),
            "additionalProperties": True,
        },
    }
    if model_selection:
        # Advertised only while the Settings switch allows the call to use them: a
        # parameter that can never run is an instruction to ask for a refusal.
        properties["provider"] = {
            "type": "string",
            "description": (
                "Optional LLM provider override for a NEW task — e.g. a cheap route "
                "for bulk extraction while you keep the strong model for synthesis. "
                "Must be a registered, configured LLM provider id; call "
                "list_subagent_models to see them. NOT for the CLI agents: codex and "
                "claude-code are agent types — pass them as `subagent_type` and give "
                "one of their catalog ids as `model`. Either this or `model` may be "
                "given alone. Not available when resuming (task_id)"
            ),
        }
        properties["model"] = {
            "type": "string",
            "description": (
                "Optional LLM model override for a NEW task: a bare id (keeps the "
                "agent's provider) or a full `provider:model`. A CLI subagent "
                "(subagent_type=\"codex\"/\"claude-code\") takes a BARE id from its "
                "own account catalog. Use list_subagent_models to see the advertised "
                "ids. Not available when resuming (task_id)"
            ),
        }
    return {
        "type": "function",
        "function": {
            "name": "task",
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": ["description", "prompt", "subagent_type"],
            },
        },
    }


def _policy(arguments: dict[str, Any]) -> dict[str, Any]:
    """Delegation is a read-shaped action for the gate: the child's own tools are what the
    permission rules fence, and the spawn is announced with the description the caller
    wrote, so the receipt says what was launched rather than that something was."""
    return {
        "category": "search",
        "risk_level": "low",
        "capabilities": ["search"],
        "requires_approval": False,
    }


def _render(arguments: dict[str, Any], value: Any) -> str:
    if isinstance(value, dict):
        if "error" in value:
            return str(value["error"])
        if isinstance(value.get("report"), str):
            return value["report"]
    return str(value)


def _present_call(arguments: dict[str, Any]) -> ToolPresentation:
    agent = str((arguments or {}).get("subagent_type") or "task")
    label = agent.title()
    text = str((arguments or {}).get("description") or "").strip()
    return ToolPresentation(
        summary=f"{label} · {text}" if text else f"{label} subagent",
        kind="other",
    )


def _facts(arguments: dict[str, Any], value: Any) -> dict[str, Any]:
    """What the log records about a spawn: it went to the background, was queued for
    a slot, or ended on the session's subagent cap. `harness_review` counts `slot-refusals` from
    `outcome` "cap", which a call past the cap meets only when it cannot be queued (a
    background call, a program's, a subagent's own) since a call past the cap waits its turn."""
    if not isinstance(value, dict):
        return {}
    status = value.get("status")
    if status in ("running", "queued"):
        return {"outcome": status}
    error = str(value.get("error") or "")
    if '"subagent_max_parallel" is' in error or "tasks are already running in this session" in error:
        return {"outcome": "cap"}
    return {}


def _present_result(arguments: dict[str, Any], value: Any) -> ToolPresentation:
    agent = str((arguments or {}).get("subagent_type") or "task")
    label = agent.title()
    if isinstance(value, dict) and "error" in value:
        return ToolPresentation(summary=f"{label} failed", detail=clip(str(value["error"]), 300), kind="other")
    status = str((value or {}).get("status") or "running") if isinstance(value, dict) else "running"
    run = str((value or {}).get("task_id") or "") if isinstance(value, dict) else ""
    steps = (value or {}).get("steps") if isinstance(value, dict) else None
    summary = (
        f"{label} running in the background as {run}" if status == "running"
        else f"{label} queued — starts when a slot frees" if status == "queued"
        else f"{label} finished"
    )
    detail = None
    if isinstance(steps, int) and steps:
        detail = f"{steps} steps"
    return ToolPresentation(summary=summary, detail=detail, kind="other", run_id=run, run_steps=steps)


__all__ = ["DESCRIPTION", "render_output", "task_tools"]
