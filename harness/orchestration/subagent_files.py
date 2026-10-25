"""GUI-managed subagent files — validate, compose, scan, write atomically.

Settings ▸ Subagents edits per-agent markdown (`{agent,agents}/**/*.md`, F3) rather than
the user's `config.toml`: the GUI never rewrites TOML. Everything written here must
round-trip through `config.parse_agent_markdown`, so the frontmatter is limited to the
subset that parser accepts — scalars, a nested `permission` map, a `|` block for the
description — and the prompt is the body. No tabs.

Placement is the loader's own: user scope is `<state-dir>/agents/<name>.md` (the directory
`load_config` scans beside `config.toml`), project scope is
`<workspace>/.harness/modes/agents/<name>.md` (which merges AFTER `<workspace>/agents`, so the
GUI's project file wins a same-name collision). A nested name (`team/helper`) keeps the
loader's slash naming and creates the parent directories.

The scope directory is the only path the caller supplies; every name segment is validated
against `_NAME_RE`, so a name can never climb out of the agents directory.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Any, Iterable, Optional

from harness.config.config import parse_agent_markdown
from harness.tools.task_agents import _rules_from_tools, preset_rules, rules_from_config, summarize_rules

#: One path segment: lowercase, digits, dashes; starts with a letter or digit. A name is
#: one or more segments joined by `/` — nested agents (`team/helper`), validated
#: segment by segment so `..`, drive letters and absolute paths are impossible.
_SEGMENT = r"[a-z0-9][a-z0-9-]*"
_NAME_RE = re.compile(rf"^{_SEGMENT}(?:/{_SEGMENT})*$")
_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]*$")

MAX_NAME_LENGTH = 96

#: Both loader spellings (`config._AGENT_MD_DIRS`), in the loader's order.
_MD_DIRS = ("agent", "agents")


class SubagentError(ValueError):
    """A rejected subagent edit. The route turns it into `{"error": str, status}`."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def validate_name(name: Any) -> str:
    text = str(name or "").strip()
    if not text:
        raise SubagentError("a subagent name is required")
    if len(text) > MAX_NAME_LENGTH:
        raise SubagentError(
            f"a subagent name must be at most {MAX_NAME_LENGTH} characters"
        )
    if not _NAME_RE.match(text):
        raise SubagentError(
            "a subagent name must be lowercase letters, digits, and dashes "
            "(for example code-reviewer, or team/helper for a nested name)"
        )
    return text


def validate_model(model: Any) -> str:
    """One model id, trimmed; empty means "inherit". Raises for anything unparseable.

    Shared by the markdown PUT and the built-in model route, so both accept exactly the
    same ids.
    """
    text = str(model or "").strip()
    if text and not _MODEL_RE.match(text):
        raise SubagentError("model must be a provider/model id")
    return text


def agent_file(root: Path, name: str) -> Path:
    """`<root>/agents/<name>.md` — the file the loader resolves that name from.

    `root` is a scan root (`state_dir()` for user scope, `<workspace>/.reeve` for
    project scope), NOT the agents directory itself.
    """
    validate_name(name)
    base = (Path(root) / "agents").resolve()
    path = (base / f"{name}.md").resolve()
    if path.parent != base and base not in path.parents:
        raise SubagentError("that name is not allowed")
    return path


def compose_agent_file(root: Path, name: str, payload: Any) -> tuple[Path, str]:
    """Validate one PUT body and return the file to write plus its exact text.

    Raises `SubagentError` (400) for every rejected input: empty description/prompt, a
    bad color/model, an unknown tool preset or group.
    """
    body = payload if isinstance(payload, dict) else {}
    validated = validate_name(name)
    description = " ".join(str(body.get("description") or "").split())
    if not description:
        raise SubagentError("a description is required")
    prompt = str(body.get("prompt") or "").strip()
    if not prompt:
        raise SubagentError("a system prompt is required")
    color = str(body.get("color") or "").strip()
    if color and not _COLOR_RE.match(color):
        raise SubagentError("color must be a #rrggbb hex value")
    model = validate_model(body.get("model"))
    tools = body.get("tools") or {}
    if not isinstance(tools, dict):
        raise SubagentError("tools must be an object")
    try:
        rules = preset_rules(
            str(tools.get("preset") or "all"),
            tools.get("allow"),
            tools.get("deny"),
        )
    except ValueError as error:
        raise SubagentError(str(error)) from error
    inject = body.get("inject_agents")
    text = _render_markdown(
        description=description,
        prompt=prompt,
        color=color,
        model=model,
        rules=rules,
        inject_agents=None if inject is None else bool(inject),
    )
    return agent_file(root, validated), text


def _render_markdown(
    *,
    description: str,
    prompt: str,
    color: str = "",
    model: str = "",
    rules: Iterable[tuple[str, str, str]] = (),
    inject_agents: Optional[bool] = None,
) -> str:
    """One definition as markdown in the loader's accepted subset.

    The description is a folded-free `|` block so `:` and quotes inside it cannot break
    the line parser; `color`/`model` are quoted scalars; the rules are a nested
    `permission` map whose keys are the canonical permission names (`"*"` quoted for the
    deny-all). `inject_agents` is written only when the answer is False — absent means the
    default (inject), exactly what `resolve_agents` reads.
    """
    lines = ["---", "description: |"]
    for line in str(description).splitlines() or [""]:
        lines.append(f"  {line}".rstrip())
    lines.append("mode: subagent")
    if color:
        lines.append(f'color: "{color}"')
    if model:
        lines.append(f'model: "{model}"')
    if inject_agents is False:
        lines.append("inject_agents: false")
    frozen = list(rules)
    if frozen:
        lines.append("permission:")
        for permission, _pattern, action in frozen:
            key = '"*"' if permission == "*" else permission
            lines.append(f'  {key}: "{action}"')
    lines.append("---")
    return "\n".join(lines) + "\n\n" + str(prompt).strip() + "\n"


def write_agent_file(path: Path, text: str) -> None:
    """Replace `path` atomically: write a sibling temp file, then `os.replace`.

    A reader (the next `load_config`) therefore sees either the old file or the complete
    new one — never a half-written definition the parser would skip.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(
        dir=str(path.parent), prefix=".subagent-", suffix=".md.tmp"
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def scan_agent_files(
    sources: Iterable[tuple[Path, str]],
) -> dict[str, dict[str, Any]]:
    """The markdown files under each `(root, scope)` pair, later sources winning.

    Mirrors `config.read_agent_files`'s naming exactly (both `agent/` and `agents/`,
    recursive, the path relative to the root minus the prefix and `.md`). A file whose
    frontmatter does not parse is SKIPPED, like the loader skips it with a warning — a
    hand-edited file with a stray tab must not turn the listing into a 500.
    """
    found: dict[str, dict[str, Any]] = {}
    for root, scope in sources:
        root = Path(root)
        if not root.is_dir():
            continue
        for folder in _MD_DIRS:
            base = root / folder
            if not base.is_dir():
                continue
            for path in sorted(base.rglob("*.md")):
                if not path.is_file():
                    continue
                relative = path.relative_to(root).as_posix()
                prefix = f"{folder}/"
                name = (
                    relative[len(prefix) :]
                    if relative.startswith(prefix)
                    else path.name
                )
                if name.endswith(".md"):
                    name = name[:-3]
                if not name:
                    continue
                try:
                    entry = parse_agent_markdown(path)
                except Exception:
                    continue
                found[name] = {
                    "scope": scope,
                    "file": str(path),
                    "entry": entry,
                }
    return found


def editable_fields(entry: dict[str, Any]) -> dict[str, Any]:
    """The GUI's editable view of one parsed markdown definition.

    The rules are rebuilt the same way `resolve_agents` reads them — the deprecated
    `tools` booleans first, the `permission` table over them — then summarized into the
    `{preset, allow, deny}` shape the form understands.
    """
    data = entry if isinstance(entry, dict) else {}
    return {
        "prompt": str(data.get("prompt") or ""),
        "tools": summarize_rules(rules_for_entry(data)),
        # Absent = the default (inject); only an explicit False turns it off.
        "inject_agents": data.get("inject_agents") is not False,
    }


def rules_for_entry(entry: dict[str, Any]) -> list[tuple[str, str, str]]:
    """The raw rules of one parsed definition (the order `resolve_agents` merges)."""
    data = entry if isinstance(entry, dict) else {}
    tools = data.get("tools")
    return [
        *_rules_from_tools(tools if isinstance(tools, dict) else {}),
        *rules_from_config(data.get("permission")),
    ]


__all__ = [
    "MAX_NAME_LENGTH",
    "SubagentError",
    "agent_file",
    "compose_agent_file",
    "editable_fields",
    "rules_for_entry",
    "scan_agent_files",
    "validate_model",
    "validate_name",
    "write_agent_file",
]
