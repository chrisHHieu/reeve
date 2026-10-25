"""The tool catalog: each id an agent may list in its `tools:`, and what that id builds.

An entry (a `Capability`) groups tools under an id that does not change, says which parts of the
session it cannot do without (`requires`) and what kinds of risk its tools carry (`risk`, which the
install consent screen shows). `expand(ids, context)` builds the tools of a `tools:` list and
leaves out an entry the session cannot support: with no executor there is no shell.

Only this app adds entries. Breadth from outside comes as entries added here after review, or as
MCP servers, whose tools are not in the catalog at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

import aisuite as ai

from harness.safety.risk import RiskClass
from harness.tools.file_writers import file_writers
from harness.tools.files import apply_file_contracts, file_tools
from harness.tools.listing import list_files_tools
from harness.tools.notebook_tool import notebook_tools
from harness.tools.search import search_tools
from harness.tools.shell import shell_tools
from harness.tools.terminal import terminal_tools
from harness.tools.todo import todo_tools

from .agents.base import AgentContext


def _has_workspace(ctx: AgentContext) -> bool:
    # A local folder, or a World: a session bound over SSH has no local `workspace` (its anchor is
    # a POSIX path on the other host) yet carries the RemoteWorld every file and shell tool is
    # built from.
    return ctx.workspace is not None or ctx.world is not None


#: What `requires` may name, each as a test of the session's context.
_REQUIREMENTS: dict[str, Callable[[AgentContext], bool]] = {
    "workspace": _has_workspace,
    "executor": lambda ctx: ctx.executor is not None,
    "todo": lambda ctx: ctx.todo is not None,
    "terminals": lambda ctx: ctx.terminals is not None,
}

# Toolkit names this app builds its own version of. `apply_unified_diff` is not offered at all: it
# failed 8 of its 9 real calls, since a unified diff needs line numbers from a reader built to
# show none, and `apply_patch` and `replace_in_file` cover every edit it could make. Worlds keep
# the method, and the safety layers still classify the name should some path expose it.
_REPLACED_READERS = {"search_files", "read_file", "read_file_lines", "list_files", "apply_unified_diff"}
# On a local session the writers are this app's as well: write to a temp file and rename, keep a
# BOM and CRLF as they were, one matcher behind every edit tool.
_REPLACED_WRITERS = {"write_file", "replace_in_file", "apply_patch"}


def _anchor(context: AgentContext) -> str:
    """What relative paths are relative to: the world's root when there is a world (a local
    folder resolved, or the remote workspace's POSIX path), else the older `workspace` field."""
    root = getattr(context.world, "root", None) if context.world is not None else None
    return str(root) if root is not None else str(context.workspace)


def _remote_backend(context: AgentContext) -> Optional[Any]:
    """An SSH world's read backend; None for everything else.

    Writes and edits already come from `world.fs_tools()`, but the reader, grep and image tools go
    to the disk themselves, so over SSH they are built on this. A local world has no `is_remote`,
    which leaves the local tools exactly as they were."""
    world = context.world
    if world is None or not getattr(world, "is_remote", False):
        return None
    backend = getattr(world, "read_backend", None)
    return None if backend is None else backend(context.roots)


@dataclass(frozen=True)
class Capability:
    id: str
    name: str  # what the consent screen calls it
    description: str
    build: Callable[[AgentContext], list]
    requires: tuple[str, ...] = ()
    risk: tuple[RiskClass, ...] = (RiskClass.READ,)

    def available(self, context: AgentContext) -> bool:
        """Whether this session has everything the entry needs."""
        return all(_REQUIREMENTS[need](context) for need in self.requires)


# -- what each entry builds -------------------------------------------------------------------


def _toolkit_files(context: AgentContext) -> list:
    """The session WORLD's write and edit tools, less the names replaced above. They come from the
    world rather than an import of the toolkit here, so a remote or container world brings its own
    and nothing below changes. A context with no world (made before the field, in old tests) gets
    the toolkit directly."""
    replaced = set(_REPLACED_READERS)
    if context.world is not None:
        tools = context.world.fs_tools(roots=context.roots)
    else:
        # Only here are the writers replaced too: a remote world's write on its own host, and
        # swapping them would write to this machine while the session thinks it edits the other.
        replaced |= _REPLACED_WRITERS
        where = {"roots": context.roots} if context.roots else {"root": str(context.workspace), "allow_write": True}
        tools = ai.toolkits.files(**where)
    return [tool for tool in tools if getattr(tool, "__name__", "") not in replaced]


def _files(context: AgentContext) -> list:
    """Files in every folder the session holds.

    There is one reader: the windowed, line-numbered `read_file` stands in for the toolkit's
    `read_file` and `read_file_lines`, and `grep` for its slow `search_files`. Over SSH the read
    and image tools go through the remote backend."""
    anchor, backend = _anchor(context), _remote_backend(context)
    tools = [
        *apply_file_contracts(_toolkit_files(context)),
        *file_writers(anchor, roots=context.roots, backend=backend),
        *file_tools(anchor, roots=context.roots, backend=backend),
        # This app's listing: it follows .gitignore, puts the newest first, says when it stopped.
        *list_files_tools(anchor, roots=context.roots, backend=backend),
    ]
    if backend is None:
        # Notebooks a cell at a time, on local sessions only: over SSH a write must be
        # the remote world's, and this one would land on this machine's disk.
        tools += notebook_tools(anchor, roots=context.roots)
    return tools


def _search(context: AgentContext) -> list:
    """`grep` over every folder the session holds, not just its first, so a folder added mid-way
    is searchable at once; over SSH, through the same backend as the reader."""
    return search_tools(_anchor(context), roots=context.roots, backend=_remote_backend(context))


def _shell(context: AgentContext) -> list:
    """`run_shell` and the tools that manage background commands."""
    return shell_tools(context.executor)


def _terminals(context: AgentContext) -> list:
    """Terminals that stay open: the interactive part `run_shell` says it lacks. An entry of their
    own, so an agent can have one-shot commands without a shell it can keep open."""
    return terminal_tools(context.terminals)


def _todo(context: AgentContext) -> list:
    """`todo_write`, which the Progress panel shows."""
    return todo_tools(context.todo)


_READS = (RiskClass.READ,)
_EDITS = (RiskClass.READ, RiskClass.WRITE_LOCAL)
_RUNS = (RiskClass.EXEC,)

_CAPABILITIES: list[Capability] = [
    # `code_files` is still listed by the team-worker manifests (`tools: [code_files, …]`). It
    # builds what `files` builds, as it has since there was one reader; only the old Code
    # persona's wording set it apart.
    Capability("code_files", "Code files", "Read and change the files of one repository, read with line numbers.", _files, ("workspace",), _EDITS),
    Capability("files", "Files", "Read and change files in the folders this conversation holds.", _files, ("workspace",), _EDITS),
    Capability("search", "Search", "Search file contents quickly (grep).", _search, ("workspace",), _READS),
    Capability("shell", "Shell", "Run shell commands, one process each, in a working folder that carries over.", _shell, ("executor",), _RUNS),
    Capability("todo", "Task list", "Keep a task list the person can watch.", _todo, ("todo",), _READS),
    # Builds nothing. `run_code`'s description must name every tool a program may call, a list
    # that exists only once the registry is complete, so it is registered last somewhere else and
    # this entry is how an agent asks for it. EXEC, because the program is any Python at all, as
    # `run_shell` already allows, and the consent screen has to say so.
    Capability("code_mode", "Programs", "Write a short program that calls your own tools, instead of calling them one at a time.", lambda context: [], (), _RUNS),
    # EXEC, necessarily: these type into a live shell, and a terminal shown as low risk on the
    # consent screen would hand over a shell without saying so.
    Capability(
        "terminals", "Terminals", "Open terminals that stay open, keep their state between calls, and answer a command that asks something.",
        _terminals, ("terminals",), _RUNS,
    ),
]

CATALOG: dict[str, Capability] = {entry.id: entry for entry in _CAPABILITIES}


def capability(cap_id: str) -> Capability:
    try:
        return CATALOG[cap_id]
    except KeyError:
        raise KeyError(f"Unknown capability id: {cap_id!r}") from None


def expand(ids: list[str], context: AgentContext) -> list:
    """The tools of a `tools:` list for this session. An entry the session cannot support is left
    out, with no error: no shell without an executor, no files without a workspace."""
    return [tool for entry in map(capability, ids) if entry.available(context) for tool in entry.build(context)]


def replace_path_tools(registry: Any, context: AgentContext) -> bool:
    """Put a SECOND owner's file and search tools in a registry, so each owner keeps to its own
    folders, as `replace_shell_tools` and `replace_terminal_tools` do for theirs.

    A subagent's registry starts as a copy of its session's, and the copied `write_file`,
    `read_file` and `grep` still resolved paths against the PARENT's roots, while the child's plan
    file, spill store, shell variables and permission boundary were all the CHILD's. The child's
    own scratch folder, where its plan already was, came back as "Path escapes allowed roots",
    and a write to the parent's scratch got past the tool only to be stopped at the boundary: no
    scratch path worked for the result. Here they are built again for the child. Returns whether
    anything was swapped."""
    fresh = expand(["code_files", "search"], context)
    names = [name for name in (getattr(tool, "__name__", "") for tool in fresh) if name]
    if all(registry.get(name) is None for name in names):
        return False
    for name in names:
        registry.remove(name)
    registry.register_all(fresh)
    return True


def risk_summary(ids: list[str]) -> set[RiskClass]:
    """Every risk class the entries of `ids` carry, for the install consent screen."""
    return {risk for entry in map(capability, ids) for risk in entry.risk}
