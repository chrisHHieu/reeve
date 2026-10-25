"""The risk class of a tool: its kind of side effect, which decides how the permission engine gates it.

One `classify` reads it, and every class has one declarer: a built-in is named in
`BUILTIN_RISK`, reads included; a connector tool's class is its catalogue kind; any other tool (MCP, a
plugin) is what its own metadata says. A tool nobody declared is treated as acting off the machine,
never as a read. An optional user override sits on top and may only tighten a declared class.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Callable, Optional


class RiskClass(str, Enum):
    """What a call can do beyond answering. The values are stored in grants and overrides."""

    READ = "read"  # changes nothing; never asks
    EGRESS = "egress"  # goes out to the network, and the request itself can carry data with it
    WRITE_LOCAL = "write_local"  # changes files: kept to the session's folders, and the mode decides
    EXEC = "exec"  # runs commands; the mode decides
    EXTERNAL = "external"  # acts off the machine; what an unattended run parks for the person


SHELL_TOOL = "run_shell"
WRITE_TOOLS = frozenset({"write_file", "replace_in_file", "apply_patch", "apply_unified_diff", "edit_notebook"})

# Every tool that puts a command in front of a shell: `run_shell`, the persistent terminals and code
# mode's programs.
EXEC_TOOLS = frozenset({SHELL_TOOL, "terminal_open", "terminal_send", "terminal_signal", "run_code"})

# Network egress the model chooses. `web_fetch` takes a URL from the model, and the URL's path and
# query can carry data out, so it is not a pure read and must reach the gate. `web_search` goes to a
# fixed destination (the configured provider) but its query is free text from the model, which is
# the same outbound channel, so it is gated too.
EGRESS_TOOLS = frozenset({"web_fetch", "web_search"})

# The research pair that read-only and plan modes let through to the consent stage: searching and
# fetching is how those modes do their work, and the card names the exact query or URL before
# anything leaves.
WEB_RESEARCH_TOOLS = frozenset({"web_search", "web_fetch"})

# Built-ins whose effect leaves the session: a skill saved for every conversation, a message or file
# sent out on a channel, a scheduled task that runs later without anyone watching.
EXTERNAL_TOOLS = frozenset({
    "save_skill", "send_message", "send_file",
    "create_scheduled_task", "update_scheduled_task", "delete_scheduled_task",
})

# Built-ins that change nothing outside the session's own bookkeeping, each named because someone
# decided it, not because nobody did. Until 2026-10-23 every tool missing from the tables above was a
# read by default, so a new tool that wrote or ran something and was not added here would have
# skipped the read-only modes and the approval card at once. Some are reads by what they act on:
# `shell_task_kill` and `terminal_close` stop a process this session started; `task`, `message_task`
# and `workflow` start or brief children, whose own calls pass this gate; `tool_call` runs another
# tool, which is judged as itself.
READ_TOOLS = frozenset({
    "read_file", "read_file_lines", "list_files", "search_files", "grep", "lsp", "search_history",
    "load_skill", "tool_search", "tool_describe", "tool_call",
    "present", "show_visual", "design_guide", "check_design", "compare_design",
    "todo_write", "update_plan", "propose_plan", "goal", "ask_user", "request_tool", "fold_context",
    "task", "message_task", "workflow", "list_subagent_models",
    "list_scheduled_tasks", "sleep_until", "wake_on", "wake_on_event",
    "shell_task_output", "shell_task_kill", "terminal_read", "terminal_list", "terminal_close",
})

# A later table wins a name that is in two, so the order is from least to most consequential.
BUILTIN_RISK: dict[str, RiskClass] = {
    name: risk
    for names, risk in (
        (READ_TOOLS, RiskClass.READ),
        (WRITE_TOOLS, RiskClass.WRITE_LOCAL),
        (EXEC_TOOLS, RiskClass.EXEC),
        (EGRESS_TOOLS, RiskClass.EGRESS),
        (EXTERNAL_TOOLS, RiskClass.EXTERNAL),
    )
    for name in names
}

# How closely each class is watched, for the rule that an override may only tighten. Commands and
# local writes rank highest: they are the ones scoped by path and gated by command.
_STRICTNESS: dict[RiskClass, int] = {RiskClass.READ: 0, RiskClass.EGRESS: 1, RiskClass.EXTERNAL: 2, RiskClass.WRITE_LOCAL: 3, RiskClass.EXEC: 3}

# The person's class for a tool name, or None to keep the declared one.
RiskOverrides = Callable[[str], Optional["RiskClass"]]


def _catalog_class(tool_name: str) -> Optional[RiskClass]:
    """A connector tool's class, from its catalogue kind: a read is a read, anything else acts on the
    person's account off the machine. None for a tool the catalogue does not list. The import is lazy
    so this module loads without the connectors package."""
    try:
        from harness.integrations.connectors.catalog import KIND_BY_NAME
    except ImportError:  # pragma: no cover - connectors always ship
        return None
    kind = KIND_BY_NAME.get(tool_name)
    if kind is None:
        return None
    return RiskClass.READ if kind == "read" else RiskClass.EXTERNAL


def declared_class(tool_name: str) -> Optional[RiskClass]:
    """The class a built-in or a connector tool is declared with, or None for any other tool."""
    return BUILTIN_RISK.get(tool_name) or _catalog_class(tool_name)


def classify(tool_name: str, metadata: Any = None, overrides: Optional[RiskOverrides] = None) -> RiskClass:
    """The effective risk of a tool call.

    An override may set the class of a tool nobody declared (quieting an over-cautious plug-in, which
    is its purpose) and may tighten a declared one, never loosen it: turning a write into a read would
    remove path scoping and the read-only gate together, so that is refused and the declaration
    stands. Otherwise: the declaration, then the tool's own metadata (`requires_approval` means
    external), and with neither, external."""
    declared = declared_class(tool_name)
    chosen = overrides(tool_name) if overrides is not None else None
    if chosen is not None and (declared is None or _STRICTNESS[chosen] >= _STRICTNESS[declared]):
        return chosen
    if declared is not None:
        return declared
    # Nobody declared it. A caller holding the tool's metadata passes it; with none, saying nothing
    # cannot be taken to mean "changes nothing", so it counts as acting off the machine.
    if metadata is None or getattr(metadata, "requires_approval", False):
        return RiskClass.EXTERNAL
    return RiskClass.READ


def is_consequential(risk: RiskClass) -> bool:
    """Whether the permission engine has to look at it: everything but a read."""
    return risk != RiskClass.READ
