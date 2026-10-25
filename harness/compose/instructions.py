"""The system prompt of a session, assembled from the agent's own and what the session stands on.

The pieces are in a fixed order, because the prompt is part of the cached request prefix and a
reordering would price the whole conversation again. They are also fixed for the life of a session:
edits to the person's rules or memory apply to new conversations and never to one in progress.
The features' sections (memory, the recap) come last, in their own fixed order.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Collection, Iterable, Optional

from harness.context.environment import environment_context
from harness.context.model_line import model_line
from harness.context.memory import format_user_rules
from harness.context.memory.outside import read_outside_content
from harness.context.project_rules import load_rules
from harness.modes.agents import Agent
from harness.session.project import load_agents_md
from harness.tools.shell_env import ShellEnv, describe as describe_shell_env

from .prompts import NARRATION_GUIDANCE, remote_environment_context


@dataclass
class MemoryWitnesses:
    """What the harness can say about the turn a memory is written in: which
    session and turn it is, whether the work is checked, and whether the turn read content from
    outside the machine. They are read when a write happens, so the engine behind `engine_of` is the
    live one, built after the closures."""

    stamp: Callable[[], tuple[str, Optional[int]]]
    unsettled: Callable[[], str]
    external: Callable[[], bool]


def memory_witnesses(session_id: Optional[str], engine_of: Callable[[], Any]) -> MemoryWitnesses:
    return MemoryWitnesses(
        stamp=lambda: (str(session_id or ""), getattr(engine_of(), "_turn_no", None)),
        unsettled=lambda: engine_of()._work_unsettled(),
        external=lambda: read_outside_content(engine_of().messages),
    )


def _append(instructions: str, block: str) -> str:
    return f"{instructions}\n\n{block}" if block else instructions


def compose_instructions(
    *,
    agent: Agent,
    model: str = "",
    user_rules: Optional[Any],
    remote: bool,
    environment: Optional[dict[str, Any]],
    remote_root: str,
    ws: Optional[Path],
    shell_env: ShellEnv,
    has_shell: bool,
    sections: Iterable[str] = (),
    tools: Optional[Collection[str]] = None,
) -> str:
    """The prompt: the agent's own, the session's ground, then the features' `sections`.

    `tools` are the names the session holds, deferred ones included since the model can reach
    them. The agent's prompt keeps only the pieces whose tools are among them, so a session is never
    told to use a tool it lacks; None keeps every piece."""
    text = agent.prompt_for(tools)
    # Which model this is, right after who it is (`context/model_line.py`): a switch rewrites it.
    text = _append(text, model_line(model) if model else "")
    text = _append(text, NARRATION_GUIDANCE)

    # The person's SOUL.md, their identity and standing instructions, goes in slot one so the durable
    # voice is read first, before the environment, the project conventions and memory. It is read once
    # here and is session-stable like the memories below.
    text = _append(text, format_user_rules((user_rules() if callable(user_rules) else user_rules) or ""))

    if remote:
        # The remote world's environment: the workspace anchor, the host, and the one boundary the
        # model must not forget. Every file and shell tool reaches the remote machine, so a
        # local-looking path it invents does not exist here.
        text = _append(text, remote_environment_context(environment or {}, remote_root))
    if ws is not None:
        text = _append(text, environment_context(ws))
        # The managed shell variables, listed beside the environment they describe. The list is empty
        # for a session with no shell, so nothing promises a variable that is not there.
        text = _append(text, describe_shell_env(shell_env) if has_shell else "")
        inject = getattr(agent, "inject_agents", True)
        text = _append(text, load_agents_md(ws) if inject else "")
        # Unconditional rules only (`.agents/rules` files without a `paths:` list). The ones scoped
        # to paths load on demand through the engine's nested pass.
        text = _append(text, load_rules(ws) if inject else "")

    for section in sections:
        text = _append(text, section)
    return text
