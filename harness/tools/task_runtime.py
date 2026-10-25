"""The child run: a subagent's engine, its rails, and how its report is collected.

This is the harness half of delegation, kept deliberately separate from the tool's
surface (`task.py`). What lives here was already right — a child is a whole engine in a
worker thread, unwound by its own stop rail, the session's stop flag and a wall clock —
and what changed is only what a child IS: an agent definition with a permission ruleset
(`task_agents.py`) instead of a hard-coded archetype.

The child is a normal session run, and the tool only
creates it, waits, and reads the last text part back
(the report is the child's LAST assistant text, not a join).
"""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

import aisuite as ai

from harness.loop import ApprovalOutcome, TurnEngine
from harness.session.events import EventType
from harness.safety.permissions import PermissionEngine, write_paths
from harness.llm.providers.base import CACHE_AFFINITY, TokenUsage
from ..tools import ToolRegistry
from harness.integrations.web import PageAsker, make_web_fetch_tool, make_web_search_tool
from .file_writers import file_writers
from .files import apply_file_contracts, file_tools
from .search import search_tools
from .task_agents import AgentInfo, DEFAULTS, merge_rules
from .task_agents import rule_decider
from .undo import snapshot_listener

#: The end-of-rail directive a capped child gets: its REPORT, not a session handoff. The
#: parent cannot see the child's context, so "what remains / next action" is a status
#: update where the findings should be.
CAP_PROMPT = (
    "You have reached your step budget. Stop starting new work and reply NOW with your "
    "final answer to the task: the findings, the paths, and anything the caller must "
    "know. Do not ask questions and do not describe what you would do next."
)


async def _deny(request: Any) -> Any:
    """The approver an unattended child can have: refuse. The engine turns this into the
    tool error the child reads — it learns a rule blocked the action and reports that,
    instead of the call crashing on a missing approver."""
    return ApprovalOutcome.DENY


def _step_clause(budget: Optional[int]) -> str:
    if not budget:
        return ""
    return (
        f"\n\nYou have a budget of {budget} steps for this task. Work efficiently and "
        "reserve your last step for the final answer."
    )


def build_child_engine(
    *,
    workspace: str | Path,
    provider: Any,
    model: str,
    agent: AgentInfo,
    model_settings: Optional[dict[str, Any]] = None,
    max_iterations: Optional[int] = None,
    rules: Optional[Iterable[tuple[str, str, str]]] = None,
    undo: Optional[Any] = None,
    secrets: Optional[Any] = None,
    spill: Optional[Any] = None,
    approver: Optional[Any] = None,
) -> TurnEngine:
    """A child engine for one agent definition, with its tools filtered by its ruleset.

    The registry is built the way the parent's is (files, search, git, shell, web) and
    then the rules REMOVE tools — a wildcard deny.
    Removing at construction rather than refusing at call time is what makes
    the child's own model not waste steps proposing tools it may not use.

    This is the NO-MANAGER (CLI, TUI, direct engine callers) path, and its toolset is a
    fixed safe set composed here — it deliberately does NOT inherit the session's full
    registry. A manager-backed `task` spawn goes through `SessionManager.spawn_subagent`,
    whose child is seeded from the parent SESSION's registry (`ToolRegistry.filtered`)
    so MCP/connector tools, skills, memory, plan tools and `run_code` cross
    over; this fixed set is what those callers would otherwise lack.
    """
    from .shell import shell_tools
    from harness.orchestration.worlds import LocalWorld

    ws = str(Path(workspace).resolve())
    rules = list(rules or agent.permission)
    # Drop the parent's cache affinity: it is a routing hint for a session whose prefix
    # this child does not share (own instructions, own toolset).
    model_settings = {k: v for k, v in (model_settings or {}).items() if k != CACHE_AFFINITY}
    # One world per child: its own shell (a `cd` in a child must not move the parent's)
    # and the same managed environment. The world's executor is closed by the caller.
    world = LocalWorld(root=ws, spill=spill)
    registry = ToolRegistry()
    # The same composition a session gets (`catalog._toolkit_files`): our reader, our
    # writers (atomic, convention-keeping, one matcher), and no
    # `apply_unified_diff`, which failed 8 of its 9 real calls.
    replaced = {
        "search_files", "read_file", "read_file_lines", "apply_unified_diff",
        "write_file", "replace_in_file", "apply_patch",
    }
    registry.register_all(
        apply_file_contracts(
            [
                t
                for t in ai.toolkits.files(root=ws, allow_write=True)
                if getattr(t, "__name__", "") not in replaced
            ]
        )
    )
    registry.register_all(file_writers(ws))
    registry.register_all(file_tools(ws))
    registry.register_all(search_tools(ws))
    registry.register_all(shell_tools(world.executor))
    registry.register(make_web_search_tool(secrets))
    page_asker = PageAsker()
    page_asker.bind(provider, lambda: model)
    registry.register(make_web_fetch_tool(spill, asker=page_asker))

    from .task_agents import disabled_tools

    for name in disabled_tools(registry.names(), rules):
        registry.remove(name)

    # The engine's own floors stay: out-of-root writes, protected paths, deferred
    # execution. The ruleset is the DECISION above them: `allow` runs
    # with no prompt, `deny` refuses, and only an `ask` falls through to the approver —
    # which is why a child allowed to `web_search` never asks the person. The decider is
    # shared with the manager's session-child path (`task_agents.rule_decider`).
    rules = list(rules)

    permissions = PermissionEngine(
        workspace_root=Path(ws), rule_decider=rule_decider(rules), shell_cwd=lambda: world.executor.cwd
    )
    engine = TurnEngine(
        provider=provider,
        registry=registry,
        permissions=permissions,
        model=model,
        instructions=(agent.prompt or "") + _step_clause(max_iterations),
        # 0/None = uncapped (the default); a positive number is the rail.
        max_iterations=max_iterations or None,
        cap_prompt=CAP_PROMPT,
        model_settings=model_settings,
        approver=approver or _deny,
    )
    engine.executor = world.executor
    if undo is not None:
        engine.undo = undo
        engine.pipeline.on_pre_execute(snapshot_listener(undo, write_paths))
    return engine


def _run_child(coro: Any) -> Any:
    """Drive the child's loop with `asyncio.run`'s teardown minus one step.

    `asyncio.run` ends with `shutdown_default_executor`, which JOINS the executor's
    threads — and the engine's stream producer parks there for as long as the provider
    call runs. A hung provider would hold the parent's tool call hostage past the
    deadline, the exact thing the deadline exists to prevent. So: a private loop on a
    private pool, strays cancelled and settled, but `shutdown(wait=False)`.
    """
    loop = asyncio.new_event_loop()
    executor = ThreadPoolExecutor(max_workers=2)
    loop.set_default_executor(executor)
    try:
        return loop.run_until_complete(coro)
    finally:
        try:
            strays = [t for t in asyncio.all_tasks(loop) if not t.done()]
            for t in strays:
                t.cancel()
            if strays:
                loop.run_until_complete(asyncio.gather(*strays, return_exceptions=True))
            loop.run_until_complete(loop.shutdown_asyncgens())
        except Exception:
            pass  # teardown is best-effort; the outcome already returned
        executor.shutdown(wait=False)
        loop.close()


@dataclass
class ChildResult:
    """What one child run produced: its report, how it ended, and what it cost."""

    report: str = ""
    status: str = "unknown"
    #: `TokenUsage.total` summed over the child's own round-trips. The parent's turn
    #: budget folds this in, so a fan-out is spend the parent's own traffic does not show.
    tokens: int = 0
    #: Calls the child finished. This is what the `task` result's `steps` reports on the
    #: no-manager path (step E): the old run-store ledger counted the same event,
    #: and the manager-backed path reads the child session's transcript for it instead.
    steps: int = 0
    errors: list[str] = field(default_factory=list)


def drive(
    engine: TurnEngine,
    prompt: str,
    *,
    stop_events: Iterable[threading.Event],
    deadline: float,
) -> ChildResult:
    """Run the child to its report, racing the stop rails and the deadline.

    The report is the child's LAST assistant text ( reads the last text part
    of the final message) — including when a rail ended the run, because a partial answer
    the caller can weigh beats silence. A failed tool call is recorded: the task fails
    when the last tool part errored, and the caller sees that reason.
    """

    async def _run() -> ChildResult:
        out = ChildResult()

        async def consume() -> None:
            async for event in engine.run(prompt):
                if event.type == EventType.TOOL_FINISHED:
                    out.steps += 1
                    if str(event.data.get("status")) == "error":
                        out.errors.append(str(event.data.get("reason") or "tool failed"))
                elif event.type == EventType.ASSISTANT_MESSAGE:
                    if event.data.get("text"):
                        out.report = event.data["text"]
                    usage = event.data.get("usage")
                    if isinstance(usage, dict):
                        out.tokens += TokenUsage(
                            **{
                                key: int(usage.get(key) or 0)
                                for key in ("input", "output", "cache_read", "cache_write")
                            }
                        ).total
                elif event.type == EventType.TURN_END:
                    out.status = event.data.get("status", "unknown")
                elif event.type == EventType.ERROR:
                    out.status = f"error: {event.data.get('error', '')}"
                    return

        async def watch_stop() -> None:
            # Poll instead of parking a thread on `Event.wait()` — a waiter that never
            # fires would hold an executor thread until process exit.
            while not any(e.is_set() for e in stop_events):
                await asyncio.sleep(0.1)

        consumer = asyncio.ensure_future(consume())
        stopper = asyncio.ensure_future(watch_stop()) if stop_events else None
        done, pending = await asyncio.wait(
            {consumer, stopper} if stopper else {consumer},
            timeout=deadline,
            return_when=asyncio.FIRST_COMPLETED,
        )
        for w in pending:
            w.cancel()
        if consumer in done and consumer.exception() is not None:
            raise consumer.exception()
        if consumer not in done:
            out.status = (
                "interrupted" if stopper is not None and stopper in done else "timeout"
            )
        return out

    # Tools execute in a worker thread (no running loop), so a private loop is safe.
    return _run_child(_run())


def child_rules(agent: AgentInfo) -> list[tuple[str, str, str]]:
    """An agent's effective ruleset: the defaults, its own rules, in that order."""
    return merge_rules(DEFAULTS, agent.permission)


__all__ = ["CAP_PROMPT", "ChildResult", "build_child_engine", "child_rules", "drive"]
