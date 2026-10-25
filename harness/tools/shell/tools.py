"""The shell tools bound to an executor, and swapping them for another owner's."""

from __future__ import annotations

from typing import Any, Optional

import aisuite as ai

from ..contract import attach_contract
from ..shell_kinds import default_kind, pick_default
from ..shell_present import RUN_SHELL_CONTRACT, SHELL_TASK_KILL_CONTRACT, SHELL_TASK_OUTPUT_CONTRACT
from .executor import Executor
from .limits import clamp_timeout
from .schemas import TASK_KILL_SCHEMA, TASK_OUTPUT_SCHEMA, run_shell_schema

# The names `shell_tools` registers, kept in one place. The owner swap below must name every one of
# them, or a closure survives that still points at the wrong shell.
SHELL_TOOL_NAMES = ("run_shell", "shell_task_output", "shell_task_kill")


def _register(func, schema: dict, contract, *, risk: str, approval: bool):
    tool = ai.tool(
        func,
        metadata=ai.ToolMetadata(
            category="shell",
            risk_level=risk,
            capabilities=["run_command"],
            requires_approval=approval,
        ),
    )
    tool.__tool_schema__ = schema
    attach_contract(tool, contract)
    return tool


def shell_tools(executor: Executor) -> list:
    """`run_shell` and the background-task helpers, bound to one executor."""
    # A world that is not an `Executor` subclass, such as a test double or a duck-typed one, has
    # one shell.
    shells = list(getattr(executor, "shells", lambda: [])())
    pinned = getattr(executor, "default_shell", None)
    if callable(pinned):
        default = pinned()  # the world's own, so the schema names the dialect it runs
    else:
        default = pick_default({shell.kind: shell for shell in shells}) if shells else default_kind()

    def run_shell(
        command: str,
        description: Optional[str] = None,
        timeout_seconds: Optional[int] = None,
        run_in_background: bool = False,
        shell: Optional[str] = None,
        notify_when: Optional[str] = None,
    ) -> dict:
        # `description` goes unused here on purpose. It rides along in the call arguments so approval
        # prompts and the audit log can show intent and not only the raw command. `shell` reaches
        # the world only when it offers a choice: a world with one shell, such as a remote POSIX
        # host, has no `shell` parameter to receive it.
        choice = {"shell": shell} if shell and shells else {}
        if run_in_background:
            if notify_when:
                choice["notify_when"] = str(notify_when)
            return executor.run_background(command, **choice)
        timeout = None
        if isinstance(timeout_seconds, (int, float)) and timeout_seconds > 0:
            timeout = clamp_timeout(timeout_seconds)
        return executor.run(command, timeout=timeout, **choice)

    def shell_task_output(
        task_id: str,
        wait: int = 0,
        pattern: Optional[str] = None,
        from_line: Optional[int] = None,
        ignore_case: bool = False,
    ) -> dict:
        if not pattern and not from_line:
            return executor.background_output(task_id, wait=wait)
        return executor.background_output(
            task_id,
            wait=wait,
            pattern=str(pattern) if pattern else None,
            from_line=from_line if isinstance(from_line, int) and from_line > 0 else None,
            ignore_case=ignore_case is True,
        )

    def shell_task_kill(task_id: str) -> dict:
        return executor.background_kill(task_id)

    return [
        _register(run_shell, run_shell_schema(shells, default), RUN_SHELL_CONTRACT, risk="high", approval=True),
        _register(shell_task_output, TASK_OUTPUT_SCHEMA, SHELL_TASK_OUTPUT_CONTRACT, risk="low", approval=False),
        _register(shell_task_kill, TASK_KILL_SCHEMA, SHELL_TASK_KILL_CONTRACT, risk="low", approval=False),
    ]


def replace_shell_tools(registry: Any, executor: Executor) -> bool:
    """Give a registry a second owner's shell, for owner isolation as terminals have it.

    A subagent's registry is a copy of its session's, so its `run_shell` closure still
    points at the parent's shell process. A child's `cd` moved the parent's shell. Two children that
    shared one REPL queued behind each other and swapped their trailer markers: one tool returned
    "(no output)" while its command still waited, and the other returned its neighbour's exit
    status hours late (two background agents and an 8 s sleep). The tools are rebound to the child's
    own world executor here. Returns whether the swap happened."""
    if registry.get("run_shell") is None:
        return False
    for name in SHELL_TOOL_NAMES:
        registry.remove(name)
    registry.register_all(shell_tools(executor))
    return True
