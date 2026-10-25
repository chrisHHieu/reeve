"""Subagent definitions and the permission ruleset.

Two ideas carry this module:

1. **A subagent is an agent definition**, not a "type" hard-coded into the tool. Built-ins
   (`general`, `explore`) plus whatever the config declares; each carries a description,
   an optional prompt that REPLACES the provider's base prompt, a model override, a step
   cap, and a **ruleset**.
2. **Permissions are rules** — `(permission, pattern, action)` triples evaluated
   last-match-wins, where the permission NAME is a tool name and the pattern matches the
   tool's argument (a path, a command, a URL). The same ruleset does two jobs: it removes
   tools outright (a wildcard deny) and it decides ask/allow/deny at call time.

Rules use this registry's tool names, because that is what it can filter;
`_ALIAS_TO_PERMISSION` maps the short names a person is likely to write (`edit`, `bash`,
`list`, ...) onto the tools that implement them here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from harness.orchestration.subagent_flags import load_disabled_subagents
from harness.orchestration.subagent_models import load_model_overrides
from harness.safety.readonly import is_readonly_command

MODE_SUBAGENT = "subagent"
MODE_PRIMARY = "primary"
MODE_ALL = "all"

ALLOW, DENY, ASK = "allow", "deny", "ask"

#: One rule: the permission (tool) name it is about, the argument pattern it matches, and
#: what happens. `pattern` may be `*`.
Rule = tuple[str, str, str]

#: What a `run_shell` command that only reads answers to as well as `run_shell`, the
#: permission of a read-only subagent's shell. Its `allow` hands the call to the engine, which runs
#: a read inside the roots and asks for one outside them; a command that writes is not named by it.
SHELL_READ = "shell_read"


def _wildcard(pattern: str) -> re.Pattern[str]:
    """A permission pattern as a regex: `*` → `.*`, `?` → `.`, backslashes normalized.

    Anchored, so `*.env` matches a name ENDING in `.env` and nothing else, which is how a
    person keeps a subagent away from a file.
    """
    out = re.escape(pattern).replace("\\*", ".*").replace("\\?", ".")
    return re.compile(f"^{out}$")


def _match(value: str, pattern: str) -> bool:
    return bool(_wildcard(pattern.replace("\\", "/")).match(value.replace("\\", "/")))


def reject_message(feedback: str = "") -> str:
    """What a REJECTED permission ask tells the model.

    A plain reject and a reject with feedback differ only by the feedback clause. The
    permission broker answers a reply of `reject` with exactly this string, and the task
    tool reuses it for a refused spawn ask.
    """
    if feedback:
        return (
            "The user rejected permission to use this specific tool call with the "
            f"following feedback: {feedback}"
        )
    return "The user rejected permission to use this specific tool call."


#: The default `subagent_depth`: one level, so a session can delegate but a child cannot.
DEFAULT_SUBAGENT_DEPTH = 1
#: Children one parent may run at once, when the config says nothing (the thread
#: harness's old `_MAX_LIVE_RUNS`). `0` in the config means no cap.
DEFAULT_SUBAGENT_PARALLEL = 4


def subagent_parallel_limit(config: Any = None) -> int:
    """The configured per-parent concurrent-child cap; `0` means no cap.

    Same degradation rule as `subagent_depth_limit`: a Config, a mapping, or None all
    work, and a missing/non-numeric value falls back to the default instead of raising
    mid-turn.
    """
    value = getattr(config, "subagent_max_parallel", None) if config is not None else None
    if value is None and isinstance(config, dict):
        value = config.get("subagent_max_parallel")
    if value is None:
        return DEFAULT_SUBAGENT_PARALLEL
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return DEFAULT_SUBAGENT_PARALLEL


def subagent_depth_limit(config: Any = None) -> int:
    """The configured depth limit, 1 when the config says nothing.

    `config` may be a `Config` (the normal case), a plain mapping, or None (CLI/test
    callers that never loaded one) — the last two fall back to the default 1. A missing
    or non-numeric value must not raise mid-turn, so it degrades to the default.
    """
    value = getattr(config, "subagent_depth", None) if config is not None else None
    if value is None and isinstance(config, dict):
        value = config.get("subagent_depth")
    if value is None:
        return DEFAULT_SUBAGENT_DEPTH
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return DEFAULT_SUBAGENT_DEPTH


def depth_limit_message(limit: int) -> str:
    """The refusal a child gets when it asks to delegate past the depth limit."""
    return (
        f'Subagent depth limit reached ({limit}). Increase "subagent_depth" to allow '
        "nested subagents."
    )


def mention_instruction(name: str, denied: bool = False) -> str:
    """The synthetic instruction an @-mention appends to the user's message.

    The text is appended AFTER the user's own text (and after the agent part), so the model
    knows the invocation came from the person. When the caller's own ruleset DENIES
    `task.<name>`, the extra clause tells the model the agent exists regardless: the mention
    bypasses the ask entirely, so nothing else would reassure it. Spacing is part of the
    string.
    """
    hint = " . Invoked by user; guaranteed to exist." if denied else ""
    return (
        " Use the above message and context to generate a prompt and call the task tool "
        f"with subagent: {name}{hint}"
    )



def ask_permission_covered(
    grants: Optional[Iterable[tuple[str, str]]],
    permission: str,
    patterns: Iterable[str],
) -> bool:
    """Whether accumulated grants already allow EVERY pattern of a would-be ask.

    A grant is a `(permission, pattern)` allow-rule, and coverage means each of the
    request's patterns evaluates to `allow` against the grants. An empty pattern list is
    vacuously covered, since there is nothing to ask about.
    """
    patterns = [str(p) for p in (patterns or [])]
    if not patterns:
        return True
    rules = [(p, pattern, ALLOW) for p, pattern in (grants or [])]
    return all(evaluate(permission, pattern, rules) == ALLOW for pattern in patterns)


def evaluate(permission: str, value: str, rules: Iterable[Rule]) -> str:
    """The action for one call, or `"ask"` when no rule matches.

    Last match wins, so a later specific rule overrides an earlier broad one — the reason
    rule order is part of the language and not an implementation detail.
    """
    return _decide(permission, value, rules)[0]


def _decide(permission: str, value: str, rules: Iterable[Rule]) -> tuple[str, str]:
    """`evaluate`'s action and the permission of the rule that gave it."""
    permission = canonical_permission(permission)
    reads = permission == "run_shell" and is_readonly_command(value)
    action, by = ASK, ""
    for rule_permission, pattern, rule_action in rules:
        named = _match(permission, rule_permission) or (reads and rule_permission == SHELL_READ)
        if named and _match(value, pattern):
            action, by = rule_action, rule_permission
    return action, by


#: The tools whose permission is `edit` and the read tools that answer to `read`, so a
#: ruleset written with the short names filters the right tools here.
_EDIT_TOOLS = ("apply_patch", "apply_unified_diff", "replace_in_file", "write_file", "edit_notebook")
_READ_TOOLS = ("read_file", "read_image")

#: Handy names for the permission a tool answers to.
_ALIAS_TO_PERMISSION: dict[str, str] = {
    "write": "edit",
    "patch": "edit",
    "edit": "edit",
    "write_file": "edit",
    "replace_in_file": "edit",
    "apply_patch": "edit",
    "apply_unified_diff": "edit",
    "edit_notebook": "edit",
    "read": "read",
    "read_file": "read",
    "read_image": "read",
    "bash": "run_shell",
    "list": "list_files",
    "glob": "list_files",
    "webfetch": "web_fetch",
    "websearch": "web_search",
    "todowrite": "todo_write",
    "question": "ask_user",
    # The `skill` permission gates the skill surface; the tool that implements it is `load_skill`.
    "skill": "load_skill",
    # The read-only git tools, retired for the shell; a saved subagent still names them.
    "git_status": SHELL_READ,
    "git_diff": SHELL_READ,
    "git_log": SHELL_READ,
}


def canonical_permission(name: str) -> str:
    """The permission a tool (or a written name) answers to.

    ONE vocabulary in both directions: a rule written `edit = "deny"` removes every write
    tool because the filter maps each of them to `edit`, and a rule written `write_file =
    "deny"` means the same thing rather than quietly matching nothing.
    """
    return _ALIAS_TO_PERMISSION.get(name, name)


def normalize_permission_name(name: str) -> list[str]:
    """Kept for callers that want the concrete tools a written name stands for."""
    key = name.strip()
    if key == "edit" or key in _EDIT_TOOLS:
        return list(_EDIT_TOOLS)
    if key == "read" or key in _READ_TOOLS:
        return list(_READ_TOOLS)
    return [canonical_permission(key)]


def rules_from_config(config: Any, *, prefix: str = "") -> list[Rule]:
    """Parse a permission table into rules.

    `permission = "allow"` is a bare action for the named tool; a nested table is
    `{pattern: action}` — and because dict order is source order in TOML, a later key
    genuinely overrides an earlier one.
    """
    rules: list[Rule] = []
    if config is None:
        return rules
    if isinstance(config, str):
        action = config.strip().lower()
        if action in (ALLOW, DENY, ASK) and prefix:
            rules.append((prefix, "*", action))
        return rules
    if not isinstance(config, dict):
        return rules
    for key, value in config.items():
        name = canonical_permission(str(key))
        if isinstance(value, dict):
            for pattern, action in value.items():
                action_text = str(action).strip().lower()
                if action_text in (ALLOW, DENY, ASK):
                    rules.append((name, str(pattern), action_text))
        else:
            action_text = str(value).strip().lower()
            if action_text in (ALLOW, DENY, ASK):
                rules.append((name, "*", action_text))
    return rules


def merge_rules(*rulesets: Iterable[Rule]) -> list[Rule]:
    out: list[Rule] = []
    for rules in rulesets:
        out.extend(rules)
    return out


#: The GUI's tool groups → the canonical permission names a rule is written against.
#: Every name is exactly what `canonical_permission` yields for the tools in the group
#: (`list` → `list_files`, `write`/`edit` → `edit`, `shell` → `run_shell`, …), so the
#: frontmatter the Settings ▸ Subagents form writes speaks the SAME vocabulary
#: `resolve_agents` consumes — there is no second tool-name map anywhere.
GROUP_PERMISSIONS: dict[str, tuple[str, ...]] = {
    "read": ("read",),
    "grep": ("grep",),
    "list": ("list_files",),
    "shell-read": (SHELL_READ,),
    "write": ("edit",),
    "edit": ("edit",),
    "shell": ("run_shell",),
    "web": ("web_fetch", "web_search"),
    "skills": ("load_skill",),
    "memory": (
        "remember",
        "memory_read",
        "memory_search",
        "memory_update",
        "memory_forget",
    ),
    "task": ("task",),
}
GROUP_ORDER: tuple[str, ...] = tuple(GROUP_PERMISSIONS)

#: What `preset = "readonly"` re-allows after its deny-all, spelled out so the claim is
#: auditable: `read` (read_file, read_image), `grep`, `list` (list_files), `shell-read`
#: (`run_shell` for a command that only reads), `web` (web_fetch, web_search).
#: Everything else is denied — notably no `write`/`edit`, no full `shell`, no `skills`, no
#: `memory`, no `task`. This mirrors `explore`'s spirit (a search/read specialist) minus the
#: shell that can write, which the GUI's read-only preset deliberately withholds.
READONLY_GROUPS: tuple[str, ...] = ("read", "grep", "list", "shell-read", "web")

_TOOL_PRESETS = ("all", "readonly", "custom")


def preset_rules(
    preset: str,
    allow: Optional[Iterable[str]] = None,
    deny: Optional[Iterable[str]] = None,
) -> list[Rule]:
    """The rules one GUI tool preset stands for, in the canonical vocabulary.

    `all`: no wildcard deny — the definition inherits whatever its layers already had;
    only an explicit `deny` list is written (so "inherit, except shell" is expressible).
    `readonly`: deny everything, then re-allow `READONLY_GROUPS`. `custom`: deny
    everything, then re-allow the given groups. A `deny` group is written AFTER the
    allows in every preset, so a group named by both resolves to deny (last match wins).
    An unknown preset or group raises `ValueError`; the server turns that into a 400.
    """
    key = str(preset or "all").strip().lower()
    if key not in _TOOL_PRESETS:
        raise ValueError(
            f"tools.preset must be one of {', '.join(_TOOL_PRESETS)} (got {preset!r})"
        )
    if key == "readonly":
        allow_groups = list(READONLY_GROUPS)
    elif key == "custom":
        allow_groups = [str(group) for group in (allow or [])]
    else:
        allow_groups = []
    deny_groups = [str(group) for group in (deny or [])]
    for group in [*allow_groups, *deny_groups]:
        if group not in GROUP_PERMISSIONS:
            raise ValueError(f"unknown tool group: {group}")
    rules: list[Rule] = []
    if key != "all":
        rules.append(("*", "*", DENY))
    for group in allow_groups:
        for permission in GROUP_PERMISSIONS[group]:
            rules.append((permission, "*", ALLOW))
    for group in deny_groups:
        for permission in GROUP_PERMISSIONS[group]:
            rules.append((permission, "*", DENY))
    return rules


def summarize_rules(rules: Iterable[Rule]) -> dict[str, Any]:
    """The GUI's `{preset, allow, deny}` view of a ruleset — `preset_rules`' inverse.

    A group whose permissions come out mixed (some allowed, some denied — only
    hand-written rules can do that) is omitted from both lists: the form then claims the
    preset it can honestly claim. `deny` is only reported for the `all` preset, where it
    is the whole point ("inherit, except shell"): under `readonly`/`custom` every group
    outside `allow` is denied by the preset's own deny-all.
    """
    frozen = list(rules)
    deny_all = any(
        permission == "*" and pattern == "*" and action == DENY
        for permission, pattern, action in frozen
    )
    allow: list[str] = []
    deny: list[str] = []
    seen: set[str] = set()
    for group in GROUP_ORDER:
        permissions = GROUP_PERMISSIONS[group]
        actions = [evaluate(permission, "", frozen) for permission in permissions]
        if all(action == ALLOW for action in actions):
            # Aliased groups (`write` and `edit` are the same permission) collapse to
            # the first spelling, so the chips never light two names for one rule.
            if not any(permission in seen for permission in permissions):
                allow.append(group)
            seen.update(permissions)
        elif all(action == DENY for action in actions):
            deny.append(group)
    if not deny_all:
        return {"preset": "all", "allow": [], "deny": deny}
    if tuple(allow) == READONLY_GROUPS:
        # The read-only set under a deny-all IS the preset, whatever the derived deny
        # list says (every other group is denied by construction).
        return {"preset": "readonly", "allow": list(READONLY_GROUPS), "deny": []}
    return {"preset": "custom", "allow": allow, "deny": []}


def group_tool_count(group: str) -> int:
    """How many concrete tools one GUI group stands for — `read` is read_file + read_image,
    `shell-read` is the shell's reads, `memory` is five tools, … The badge the list shows."""
    return sum(
        len(normalize_permission_name(permission))
        for permission in GROUP_PERMISSIONS.get(group, ())
    )


def preset_tool_count(groups: Iterable[str]) -> int:
    """The list badge's number: the concrete tools the allowed groups carry."""
    return sum(group_tool_count(group) for group in groups)


def disabled_tools(tool_names: Iterable[str], rules: Iterable[Rule]) -> set[str]:
    """Tools removed outright by a rule.

    Only a wildcard deny removes a tool. A specific-pattern deny is NOT a removal: the tool
    stays and the deny answers when it is called, which is how `read_file: {"*.env": "ask"}`
    can still offer the tool for every other file.
    """
    rules = list(rules)
    removed: set[str] = set()
    for tool in tool_names:
        permission = canonical_permission(tool)
        action = None
        pattern = ""
        for rule_permission, rule_pattern, rule_action in rules:
            # The shell stays for a ruleset that allows its reads (`shell-read`).
            if _match(permission, rule_permission) or (permission == "run_shell" and rule_permission == SHELL_READ):
                pattern, action = rule_pattern, rule_action
        if action == DENY and pattern == "*":
            removed.add(tool)
    return removed


def rule_value(tool_name: str, arguments: dict[str, Any]) -> str:
    """What a rule's pattern is matched against, per tool: a path, a command or a URL."""
    arguments = arguments or {}
    if tool_name == "run_shell":
        return str(arguments.get("command") or "")
    for key in ("url", "query", "path", "file_path", "filename"):
        value = arguments.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def rule_decider(
    rules: Iterable[Rule],
) -> Callable[[str, dict[str, Any]], Optional[str]]:
    """The `PermissionEngine.rule_decider` for one ruleset — ONE implementation, shared
    by the thread harness (`task_runtime.build_child_engine`) and the manager's
    `spawn_subagent`, so a CLI child and a session child cannot disagree about what a
    rule means.

    `allow` and `deny` are decisions; `ask` comes back as None so the call falls through
    to the engine's normal approval path.
    """
    frozen = list(rules)

    def decide(tool_name: str, arguments: dict[str, Any]) -> Optional[str]:
        action, by = _decide(tool_name, rule_value(tool_name, arguments), frozen)
        if by == SHELL_READ and action == ALLOW:
            return None  # the engine runs a read inside the roots and asks for one outside
        return action if action in (ALLOW, DENY) else None

    return decide


# -- the agents ---------------------------------------------------------------

#: The default ruleset, in this registry's tool vocabulary: everything is
#: allowed unless a rule says otherwise, the two "ask the human" escapes are denied for
#: children, and `.env` files are the one read people expect to be gated.
DEFAULTS: list[Rule] = [
    ("*", "*", ALLOW),
    ("ask_user", "*", DENY),
    ("read", "*.env", ASK),
    ("read", "*.env.*", ASK),
    ("read", "*.env.example", ALLOW),
]

#: Read-only external directories. These rules match tool ARGUMENTS, not resolved
#: paths, so out-of-root reach is not a rule here: reads were never path-scoped for the
#: agent's own tools, and an out-of-root WRITE is an ordinary preset decision (an ask in
#: `workspace-write`, whose card names the folder — `permissions.guards.out_of_root_writes`). `.env` reads stay the one argument-shaped gate, in DEFAULTS above.

GENERAL_DESCRIPTION = (
    "General-purpose agent for researching a question or carrying out a multi-step piece "
    "of work on its own, with the tools this session has."
)

EXPLORE_DESCRIPTION = (
    "Read-only search of a folder's files: finds files by pattern, searches their text, "
    "and answers where something is or how it works there. Say how thorough to be: quick, "
    "medium or very thorough."
)

#: The explorer's prompt names this registry's tools (list_files, grep, read_file,
#: run_shell): it once named four tools it does not have (2026-09-24 audit). An agent prompt
#: REPLACES the provider's base prompt, which is what makes `explore` a file-search
#: specialist instead of a general assistant with fewer tools.
EXPLORE_PROMPT = """You are a file search specialist. You excel at thoroughly navigating and exploring codebases.

Your strengths:
- Rapidly finding files using glob patterns
- Searching code and text with powerful regex patterns
- Reading and analyzing file contents

Guidelines:
- Use list_files for broad file pattern matching
- Use grep for searching file contents with regex
- Use read_file when you know the specific file path you need to read
- Use run_shell for file operations like copying, moving, or listing directory contents
- Adapt your search approach based on the thoroughness level specified by the caller
- Return file paths as absolute paths in your final response
- For clear communication, avoid using emojis
- Do not create any files, or run bash commands that modify the user's system state in any way

Complete the user's search request efficiently and report your findings clearly.
"""


VERIFY_DESCRIPTION = (
    "Independent verifier with a fresh context. Give it the original request, what was "
    "changed and the checks that should pass; it inspects the actual changes, runs the "
    "checks itself and reports VERDICT: PASS, FAIL or UNVERIFIED with evidence. It never "
    "edits. Use it before calling multi-file or multi-step work done — the agent that did "
    "the work is the worst judge of it."
)

#: The checker half of a maker/checker pair ("a model is its own output's best defense attorney"; an independent session judges the
#: work). Denial-first like `explore`: it may read, search and run the
#: project's checks, never write — a verifier that can edit is a second maker.
VERIFY_PROMPT = """You are an independent verifier. Another agent did some work and believes it is done. Your job is to find out whether it actually is. You did not write it and you have none of its reasoning — only the request, the repository and what the caller tells you.

How to work:
1. Read the request. Then inspect the ACTUAL changes (git diff and git status with run_shell, read_file) — never trust the caller's summary of them.
2. Run the checks yourself with run_shell: the commands the caller names, or the ones the project documents (AGENTS.md, README, package or build config). Record each exact command and its result.
3. Where you can, exercise the changed behaviour end to end — run it, call it, open the output — not only its unit tests.
4. Look for what is missing: parts of the request not done, edge cases, error handling, tests that assert nothing, changes outside the task.

Rules:
- Do not create or modify files, and do not run commands that change the user's system state. Do not fix anything — report it.
- Every finding needs evidence: file:line or the command output that shows it.
- What you could not check is UNVERIFIED, never passed.

Answer in this shape, first line exactly one of the three verdicts:
VERDICT: PASS | FAIL | UNVERIFIED
Findings (most severe first): severity, where, what is wrong, evidence.
Checks run: each command and its result.
"""


@dataclass
class AgentInfo:
    """One agent definition."""

    name: str
    description: str = ""
    mode: str = MODE_ALL
    #: When set, this REPLACES the base system prompt rather than adding to it.
    prompt: str = ""
    permission: list[Rule] = field(default_factory=list)
    #: "provider/model", when the agent runs on something other than the parent's model.
    model: str = ""
    #: Provider variant (thinking level / reasoning effort), passed through untouched.
    variant: str = ""
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    #: Iteration cap; None = the harness default (uncapped).
    steps: Optional[int] = None
    hidden: bool = False
    native: bool = False
    #: Display colour: a `#rrggbb` hex or a client theme literal.
    #: Data only — clients draw it (the task card, ledger 2.10); the harness never reads it.
    color: str = ""
    #: Unknown config keys land here and are merged onto the child engine's `model_settings` LAST, so a passthrough key
    #: can override a named knob like `temperature`.
    options: dict[str, Any] = field(default_factory=dict)
    #: Whether the project's AGENTS.md instructions are appended to this agent's system
    #: prompt. True everywhere a workspace exists (the behaviour every engine already
    #: had); the GUI's "Inject AGENTS.md" toggle writes `inject_agents: false` to opt a
    #: subagent out. Honored by `build_engine` through the `Agent.inject_agents` field
    #: (`_child_agent` copies it); the no-manager thread harness never injected AGENTS.md,
    #: so the key only suppresses the manager/spawn path.
    inject_agents: bool = True
    #: `"worktree"`: every delegation to this agent works in its own git worktree (ADR
    #: 0111). "" = where the caller works, unless the
    #: call or `[delegation] worktree_isolation` asks otherwise.
    isolation: str = ""
    #: Every delegation to this agent runs in the background where the session can be told it
    #: finished (a plugin agent's `background: true`); elsewhere it runs in the foreground.
    background: bool = False
    #: External provider id ("codex" | "claude-code" | ""). When set, the child runs as a
    #: real CLI process instead of an in-process engine (harness/orchestration/subagent_external.py);
    #: `model` is then the CLI's own model name and `permission_mode` its non-interactive
    #: sandbox/approval preset.
    provider: str = ""
    provider_permission: str = ""
    #: Turned off in Settings ▸ Subagents (`disabled_subagents.json`). The row still
    #: exists — the GUI switch must be able to flip it back — but nothing may delegate to
    #: it: `describe_task` hides it, and the `task` tool / `spawn_subagent` refuse it.
    disabled: bool = False

    def describes(self) -> str:
        return self.description or "This subagent should only be called manually by the user."


def _builtin_agents() -> dict[str, AgentInfo]:
    """The two built-in subagents (`general`, `explore`), plus
    `verify` — the independent checker a maker/checker pair needs.

    `general` gets the full default toolset minus `todo_write`; `explore` is denial-first —
    `*` denied, then the read/search/shell/web tools allowed back — and carries the
    file-search prompt. `explore` may also run shell commands (the prompt forbids modifying
    ones).
    """
    general = AgentInfo(
        name="general",
        description=GENERAL_DESCRIPTION,
        mode=MODE_SUBAGENT,
        permission=merge_rules(DEFAULTS, [("todo_write", "*", DENY)]),
        native=True,
    )
    explore = AgentInfo(
        name="explore",
        description=EXPLORE_DESCRIPTION,
        mode=MODE_SUBAGENT,
        prompt=EXPLORE_PROMPT,
        permission=merge_rules(
            DEFAULTS,
            [
                ("*", "*", DENY),
                ("grep", "*", ALLOW),
                ("list_files", "*", ALLOW),
                ("run_shell", "*", ALLOW),
                ("web_fetch", "*", ALLOW),
                ("web_search", "*", ALLOW),
                ("read", "*", ALLOW),
            ],
        ),
        native=True,
    )
    verify = AgentInfo(
        name="verify",
        description=VERIFY_DESCRIPTION,
        mode=MODE_SUBAGENT,
        prompt=VERIFY_PROMPT,
        # explore's read/run set, and no web: a verdict rests on this repository.
        permission=merge_rules(
            DEFAULTS,
            [
                ("*", "*", DENY),
                ("grep", "*", ALLOW),
                ("list_files", "*", ALLOW),
                ("run_shell", "*", ALLOW),
                ("read", "*", ALLOW),
            ],
        ),
        native=True,
    )
    return {"general": general, "explore": explore, "verify": verify}


def external_agent_infos() -> dict[str, AgentInfo]:
    """The external CLI agents this machine can actually run, by agent type.

    Availability is the installed binary on PATH (the CLI's own `codex login` /
    `claude login` is the auth). A missing CLI means the agent type simply does not
    exist: the model is never offered a delegation it cannot run, and a host being present
    grants no tool by itself.
    """
    from harness.orchestration.subagent_external import available

    out: dict[str, AgentInfo] = {}
    if available("codex"):
        out["codex"] = AgentInfo(
            name="codex",
            description=(
                "Run the task in a real OpenAI Codex CLI session (its own tools and "
                "ChatGPT/API-key auth) in this workspace, sandboxed to it (workspace-write, "
                "approvals never) — on Windows its shell is PowerShell 5.1. One "
                "self-contained text task in, its final answer out; the Codex transcript "
                "stays out of this conversation."
            ),
            mode=MODE_SUBAGENT,
            provider="codex",
            native=True,
        )
    if available("claude-code"):
        out["claude-code"] = AgentInfo(
            name="claude-code",
            description=(
                "Run the task in a real Claude Code CLI session (its own tools and Claude "
                "subscription/API-key auth) in this workspace, headless with permissions "
                "bypassed (no approval prompts; the user's own hooks still apply) — on "
                "Windows its Bash tool is Git Bash, so give it POSIX commands. One "
                "self-contained text task in, its final answer out; the Claude Code "
                "transcript stays out of this conversation."
            ),
            mode=MODE_SUBAGENT,
            provider="claude-code",
            native=True,
        )
    return out


def _config_agents(config: Any) -> dict[str, Any]:
    """The `agents` table from the TOML config."""
    if isinstance(config, dict):
        table = config.get("agents") or config.get("agent") or {}
        return table if isinstance(table, dict) else {}
    return getattr(config, "agents", None) or {}


def _config_tools(config: Any) -> dict[str, Any]:
    """The config's top-level `tools` map, in either config shape (`Config` or a dict)."""
    value = getattr(config, "tools", None) if config is not None else None
    if not value and isinstance(config, dict):
        value = config.get("tools")
    return value if isinstance(value, dict) else {}


def global_rules(config: Any) -> list[Rule]:
    """The config's top-level `tools` + `permission` tables, in either config shape.

    `tools` folds into the global permission first and the written `permission` merges over
    it, then the result merges into EVERY agent's ruleset; `resolve_agents` does that for
    the definitions it returns, and the `task` tool's caller check reads it through here: a
    PRIMARY caller (one of our
    personas) is not a subagent definition, so without it the caller's ruleset would be
    empty and `evaluate` would default to `ask`.
    """
    permission = getattr(config, "permission", None) if config is not None else None
    if not permission and isinstance(config, dict):
        permission = config.get("permission")
    return merge_rules(
        _rules_from_tools(_config_tools(config)), rules_from_config(permission)
    )


def caller_task_rules(agent: AgentInfo, config: Any = None) -> list[Rule]:
    """The ruleset a CALLER's `task` permission is evaluated against.

    The shape is defaults + the config's global permission + the agent's own. The two
    built-in subagents already carry that layering
    when used as callers; a persona caller does not, and an empty ruleset would make the
    default `ask` fire on every spawn in a stock session. Same layers here, so a config
    `task = "ask"` prompts and out of the box the spawn runs without one.
    """
    return merge_rules(DEFAULTS, agent.permission, global_rules(config))


#: Every key `resolve_agents` reads itself; anything else in a table is a passthrough
#: option. `name` is not listed because a table's name IS the
#: key — a `name` field would be an option.
_AGENT_KEYS = frozenset(
    {
        "model",
        "variant",
        "temperature",
        "top_p",
        "steps",
        "max_steps",
        "hidden",
        "description",
        "mode",
        "prompt",
        "prompt_file",
        "tools",
        "options",
        "permission",
        "disable",
        "color",
        "inject_agents",
        "isolation",
        "background",
        "provider",
        "permission_mode",
        # A plugin agent's tool lists and its origin (`plugins.compat`).
        "_allow",
        "_deny",
        "_plugin",
    }
)


def _default_agent_name(config: Any) -> str:
    """The configured `default_agent`, whitespace-trimmed, from either config shape."""
    value = getattr(config, "default_agent", "") if config is not None else ""
    if not value and isinstance(config, dict):
        value = config.get("default_agent", "")
    return str(value or "").strip()


def _validate_default_agent(agents: dict[str, AgentInfo], config: Any = None) -> None:
    """Guards on `default_agent`, checked at resolve time.

    Three cases are refused with a message: an unknown name, a
    `subagent` (a task-only definition cannot own the primary slot), and a hidden one.
    Raised here rather than at session start so a bad config is loud the moment agents
    are resolved, not one turn later.
    """
    name = _default_agent_name(config)
    if not name:
        return
    agent = agents.get(name)
    if agent is None:
        raise ValueError(f'default agent "{name}" not found')
    if agent.mode == MODE_SUBAGENT:
        raise ValueError(f'default agent "{name}" is a subagent')
    if agent.hidden:
        raise ValueError(f'default agent "{name}" is hidden')


def default_agent(config: Any = None) -> Optional[AgentInfo]:
    """The agent a new session starts on, or None when unset.

    Resolution goes through `resolve_agents`, so an invalid config raises there with
    a message naming the problem; an unset key is None. Personas
    own the runtime default for a session, so this is
    the data-side answer for any surface that wants the configured primary.
    """
    agents = resolve_agents(config)
    name = _default_agent_name(config)
    return agents.get(name) if name else None


def mentionable_agents(agents: dict[str, AgentInfo]) -> list[AgentInfo]:
    """The agents an `@` mention may invoke: not hidden, and not primary-only.

    `session/prompt.ts` resolves an agent part by name against the full map, but the
    composer's suggestion list filters
    hidden agents and primaries out; the server validates against the same set so a
    hand-crafted frame cannot reach an agent the picker would never offer.
    """
    return [
        agent
        for agent in sorted(agents.values(), key=lambda a: a.name)
        if not agent.hidden and agent.mode != MODE_PRIMARY
    ]


def resolve_agents(config: Any = None) -> dict[str, AgentInfo]:
    """Built-ins, then the config overlay.

    A config entry for an unknown name ADDS an agent (mode `all`, defaults + user rules);
    `disable = true` removes one; every other key overrides its counterpart.
    """
    agents = _builtin_agents()
    # External CLI agents exist only where their binary does (see external_agent_infos).
    agents.update(external_agent_infos())
    user_rules = global_rules(config)
    for name, value in agents.items():
        value.permission = merge_rules(value.permission, user_rules)

    configured = _config_agents(config)
    for key, raw in configured.items():
        if not isinstance(raw, dict):
            continue
        name = str(key)
        if raw.get("disable"):
            agents.pop(name, None)
            continue
        item = agents.get(name)
        if item is None:
            item = agents[name] = AgentInfo(name=name, mode=MODE_ALL, permission=[])
            item.permission = merge_rules(DEFAULTS, user_rules)
        item.model = str(raw.get("model") or item.model)
        item.variant = str(raw.get("variant") or item.variant)
        item.temperature = _number(raw, "temperature", item.temperature)
        item.top_p = _number(raw, "top_p", item.top_p)
        item.steps = _int(raw, "steps", _int(raw, "max_steps", item.steps))
        item.hidden = bool(raw.get("hidden", item.hidden))
        item.description = str(raw.get("description") or item.description)
        item.mode = str(raw.get("mode") or item.mode)
        item.color = str(raw.get("color") or item.color)
        item.inject_agents = bool(raw.get("inject_agents", item.inject_agents))
        isolation = str(raw.get("isolation") or item.isolation).strip().lower()
        item.isolation = isolation if isolation == "worktree" else ""
        if "background" in raw:
            item.background = raw["background"] is True or str(raw["background"]).strip().lower() == "true"
        item.provider = str(raw.get("provider") or item.provider)
        item.provider_permission = str(
            raw.get("permission_mode") or item.provider_permission
        )
        prompt = raw.get("prompt")
        if prompt:
            item.prompt = str(prompt)
        prompt_file = raw.get("prompt_file")
        if prompt_file and not prompt:
            item.prompt = _read_prompt_file(prompt_file)
        # The deprecated `tools: {write: false}` form folds into the ruleset.
        tools = raw.get("tools")
        if isinstance(tools, dict):
            item.permission = merge_rules(
                item.permission, _rules_from_tools(tools)
            )
        # Anything the schema does not name is a passthrough OPTION: the run's provider settings, not a
        # dropped key. Explicit `options` wins over a stray key of the same name.
        stray = {key: value for key, value in raw.items() if key not in _AGENT_KEYS}
        item.options = {**item.options, **stray, **(raw.get("options") or {})}
        item.permission = merge_rules(
            item.permission, rules_from_config(raw.get("permission"))
        )
        if raw.get("_allow") or raw.get("_deny"):
            # A plugin agent's `tools`/`disallowedTools`: they may only narrow what this
            # agent could already do — never lift an ask to an allow.
            item.permission = merge_rules(
                item.permission,
                narrowing_rules(raw.get("_allow") or [], raw.get("_deny") or [], item.permission),
            )
    # Settings ▸ Subagents' built-in model store (`subagent_models.json`). It merges over
    # the BUILT-INS only: a name the config defines (TOML table or markdown file) owns its
    # `model` outright and is skipped, so the store can never shadow a definition. A
    # missing/torn store means no overrides.
    for name, model in load_model_overrides().items():
        item = agents.get(name)
        if item is None or not item.native or name in configured:
            continue
        item.model = model
    # Settings ▸ Subagents' enable switches (`disabled_subagents.json`): a disabled name
    # stays in the map so its row can be switched back on, but the listing, the `task`
    # tool and `spawn_subagent` all refuse it.
    for name in load_disabled_subagents():
        item = agents.get(name)
        if item is not None:
            item.disabled = True
    # `default_agent` is validated when the map is resolved;
    # doing it here means every consumer (task tool, subagent listing, spawn) fails loud
    # and with the same three messages.
    _validate_default_agent(agents, config)
    return agents


def _number(raw: dict[str, Any], key: str, fallback: Optional[float]) -> Optional[float]:
    value = raw.get(key)
    if value is None:
        return fallback
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _int(raw: dict[str, Any], key: str, fallback: Optional[int]) -> Optional[int]:
    value = raw.get(key)
    if value is None:
        return fallback
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _read_prompt_file(path: Any) -> str:
    try:
        return Path(str(path)).expanduser().read_text(encoding="utf-8")
    except OSError:
        return ""


def narrowing_rules(
    allowed: Iterable[str], denied: Iterable[str], base: Iterable[Rule]
) -> list[Rule]:
    """Rules that only NARROW `base` (last match wins): deny everything, restore `base`'s
    own verdict for each allowed tool, then deny the disallowed ones.

    A plugin agent's `tools` is an allowlist: writing it as
    `allow` rules would lift a tool the session asks about to one that runs unasked.
    """
    base = list(base)
    rules: list[Rule] = []
    allowed = [canonical_permission(t) for t in allowed]
    if allowed:
        rules.append(("*", "*", DENY))
        for tool in allowed:
            for permission, pattern, action in base:
                if _match(tool, permission):
                    rules.append((tool, pattern, action))
    for tool in denied:
        rules.append((canonical_permission(tool), "*", DENY))
    return rules


def _rules_from_tools(tools: dict[str, Any]) -> list[Rule]:
    """`tools = { write_file = false }` → permission rules.

    The name is CANONICALIZED before it becomes a rule, which is what makes the alias fold
    real: `write`/`edit`/`patch`/`write_file` all answer to `edit`, so the deny reaches
    `disabled_tools` for every write tool; `canonical_permission` is the one vocabulary in
    both directions.
    """
    rules: list[Rule] = []
    for name, value in (tools or {}).items():
        if isinstance(value, bool):
            action = ALLOW if value else DENY
            rules.append((canonical_permission(str(name)), "*", action))
    return rules


def describe_task(agents: dict[str, AgentInfo], caller: AgentInfo) -> str:
    """The agent list appended to the task tool's description.

    Only the agents the CALLER may use are listed, and a caller whose ruleset denies
    `task.<name>` never sees that name — the listing is a permission decision, not a
    documentation detail.
    """
    items = [a for a in agents.values() if a.mode != MODE_PRIMARY]
    visible = [
        a
        for a in items
        if not a.disabled and evaluate("task", a.name, caller.permission) != DENY
    ]
    lines = [
        f"- {a.name}: {a.describes()}"
        for a in sorted(visible, key=lambda a: a.name)
    ]
    return "\n".join(
        ["Subagent types:"] + lines
    )


def relevant_rules(rules: Iterable[Rule], permission: str) -> list[Rule]:
    """The rules a refusal should quote: the ones that mention this permission."""
    canonical = canonical_permission(permission)
    return [r for r in rules if _match(canonical, r[0])]


def child_permission(
    parent_session_rules: Iterable[Rule],
    subagent: AgentInfo,
) -> list[Rule]:
    """The child session's own rules — `agent/subagent-permissions.ts`, verbatim.

    Only the parent's DENIES and its `external_directory` rules cross over: a child is not
    handed the parent's allowances (a permission granted for one turn must not silently
    widen a delegation), but a refusal must not be laundered by delegating to a child.
    `todo_write` and `task` are denied in the child unless the subagent's ruleset itself
    says something about them — which is how a nested `general` is possible at all.
    """
    rules = list(parent_session_rules)
    out = [r for r in rules if r[0] == "external_directory" or r[2] == DENY]
    can_todo = any(r[0] == "todo_write" for r in subagent.permission)
    can_task = any(r[0] == "task" for r in subagent.permission)
    if not can_todo:
        out.append(("todo_write", "*", DENY))
    if not can_task:
        out.append(("task", "*", DENY))
    return out


__all__ = [
    "ALLOW",
    "ASK",
    "DENY",
    "AgentInfo",
    "DEFAULTS",
    "EXPLORE_DESCRIPTION",
    "EXPLORE_PROMPT",
    "GENERAL_DESCRIPTION",
    "GROUP_ORDER",
    "GROUP_PERMISSIONS",
    "MODE_ALL",
    "MODE_PRIMARY",
    "MODE_SUBAGENT",
    "READONLY_GROUPS",
    "Rule",
    "child_permission",
    "default_agent",
    "depth_limit_message",
    "describe_task",
    "disabled_tools",
    "evaluate",
    "ask_permission_covered",
    "caller_task_rules",
    "global_rules",
    "group_tool_count",
    "mention_instruction",
    "mentionable_agents",
    "merge_rules",
    "normalize_permission_name",
    "preset_rules",
    "preset_tool_count",
    "relevant_rules",
    "reject_message",
    "resolve_agents",
    "rule_decider",
    "rule_value",
    "rules_from_config",
    "subagent_depth_limit",
    "subagent_parallel_limit",
    "summarize_rules",
]
