"""`Agent`: what a session is started as, its prompt, its tools and what it needs to run.

Not to be confused with a skill: a skill is a SKILL.md that any agent can load when a task calls for
it (`harness.extensions.skills`); an agent is the one a session is.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Collection, Optional

from harness.tools.todo import TodoList


@dataclass
class AgentContext:
    """What a session gives an agent's tools to be built from."""

    workspace: Optional[Path] = None
    executor: Optional[Any] = None
    todo: Optional[TodoList] = None
    #: The persistent terminals this session owns. None where the session has no workspace to
    #: start one in.
    terminals: Optional[Any] = None
    # The shared, mutable list of RootDir the session may touch (the primary scratch plus added
    # folders). When None, tools fall back to the single `workspace`. It is held by reference, so
    # adding or removing a folder at run time is seen by the file tools built from it.
    roots: Optional[list] = None
    # The session's execution world: the ONE object that owns the filesystem tools, the shell
    # executor and the terminals. `executor` and `terminals` above are this world's own pieces,
    # kept as separate fields because every consumer already reads them. The world is where a
    # remote or container backend slots in without either of them moving.
    world: Optional[Any] = None


#: A piece of an agent's prompt and the tools it names. The piece is kept only when the
#: session holds every one of them, so a session without a tool is never told to use it.
PromptPiece = tuple[str, frozenset[str]]


def piece(text: str, *tools: str) -> PromptPiece:
    return (text, frozenset(tools))


def prompt_text(pieces: tuple[PromptPiece, ...], tools: Optional[Collection[str]] = None) -> str:
    """The pieces whose tools are all in `tools`, joined; every piece when `tools` is None."""
    held = None if tools is None else set(tools)
    return "".join(text for text, needs in pieces if held is None or needs <= held)


@dataclass
class Agent:
    name: str
    title: str
    system_prompt: str
    tool_factory: Optional[Callable[[AgentContext], list]] = None
    # The traits that replaced branching on the agent's name. `requires_folder`: the session
    # cannot start without a primary folder the person picked (the composer and the engine gate
    # on it; everything else starts on a scratch folder). `subagents`: read-only explorer
    # fan-out. `scheduling`: scheduled tasks and self-wake. `messaging`: exposes send_message.
    # `connectors`: loads the integration toolset, where True is every connected connector (the
    # general built-ins only), a tuple is an allowlist (the session gets those that are also
    # connected) and False is none. The defaults keep callers that are not personas behaving as
    # they always did.
    requires_folder: bool = False
    subagents: bool = False
    scheduling: bool = False
    messaging: bool = False
    connectors: bool | tuple[str, ...] = False
    # The catalog ids this agent asked for. They are carried because `tool_factory` is a closure
    # whose contents a caller cannot read, while one tool must be registered AFTER the rest:
    # `run_code`, whose description has to name the finished registry. Gating that on the agent's
    # NAME worked only for the two built-ins, since every manifest-backed persona is
    # `Agent(name=<its own id>)`, so Ops and every team worker silently never got it. The list of
    # capabilities is the thing they all share.
    capabilities: tuple[str, ...] = ()
    # A cap on the steps of one turn. None means no cap: the turn runs until the model stops, the
    # person aborts or the repeat guard trips. Reaching a set cap ends the turn on the graceful
    # checkpoint path. The built-ins are uncapped, and the knob is there per agent.
    steps: Optional[int] = None
    # Whether the project's AGENTS.md instructions are appended to the system prompt. It is True
    # for every session and every child today. A subagent definition may opt out with
    # `inject_agents: false` in its markdown (Settings ▸ Subagents ▸ "Inject AGENTS.md"), and
    # `build_engine` reads it through `getattr`.
    inject_agents: bool = True
    # The prompt as pieces that name tools. `system_prompt` is every piece joined; a
    # session gets only the pieces whose tools it holds (`prompt_for`). Empty: the prompt names no
    # tool conditionally and is `system_prompt` as written.
    prompt_pieces: tuple[PromptPiece, ...] = ()

    def build_tools(self, context: AgentContext) -> list:
        """The agent's own tools for this session; an agent with no factory has none."""
        return [] if self.tool_factory is None else [*self.tool_factory(context)]

    def prompt_for(self, tools: Optional[Collection[str]] = None) -> str:
        """This agent's prompt for a session holding `tools` (every tool when None)."""
        return prompt_text(self.prompt_pieces, tools) if self.prompt_pieces else self.system_prompt
