"""Slash commands a person defines, and those plugins bring (design §5.2).

A command is a Markdown file whose body is a prompt template: `/ship 1.2` expands the
body with the typed arguments (the GUI's `resolvePromptTemplate` rules) and is sent as an
ordinary user message, so it goes through every path a message does — the log,
compaction, undo. Nothing here grants anything: `allowed-tools` is shown, never applied.

One loader, several sources, LOW → HIGH (a later one wins a name):

    plugin (`<plugin>:<name>`) < ~/.claude/commands (compat, read-only)
    < state_dir()/commands < <ws>/.claude/commands (compat) < <ws>/.reeve/commands

A command in a subfolder is named with `:` — `commands/notes/draft.md` is `notes:draft`.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

#: A command body larger than this is not a prompt template; it is refused, not cut.
_MAX_BODY = 64_000


@dataclass(frozen=True)
class CommandSpec:
    name: str
    description: str
    argument_hint: str
    source: str  # "plugin" | "user" | "project" | "compat"
    path: str
    model: str = ""
    allowed_tools: tuple[str, ...] = ()
    plugin: str = ""
    body: str = field(default="", repr=False)

    def listing(self) -> dict[str, Any]:
        """The popup row: everything but the body."""
        return {
            "name": self.name,
            "description": self.description,
            "argument_hint": self.argument_hint,
            "source": self.source,
            **({"plugin": self.plugin} if self.plugin else {}),
            **({"model": self.model} if self.model else {}),
            **({"allowed_tools": list(self.allowed_tools)} if self.allowed_tools else {}),
        }


_ARGS_RE = re.compile(r"""(?:\[Image\s+\d+\]|"[^"]*"|'[^']*'|[^\s"']+)""", re.IGNORECASE)
_QUOTE_TRIM_RE = re.compile(r"""^["']|["']$""")
_PLACEHOLDER_RE = re.compile(r"\$(\d+)")


def expand(template: str, args: str) -> str:
    """A command body with its arguments filled in — the GUI's `resolvePromptTemplate`
 for the surfaces with no GUI: `$1`, `$2`… take one quote-aware word
    each and the highest takes the rest; `$ARGUMENTS` takes the text as typed; a body with
    neither gets the arguments appended as their own paragraph."""
    parsed = [_QUOTE_TRIM_RE.sub("", a) for a in _ARGS_RE.findall(args)]
    positions = [int(m) for m in _PLACEHOLDER_RE.findall(template)]
    last = max(positions, default=0)

    def fill(match: re.Match[str]) -> str:
        position = int(match.group(1))
        if position - 1 >= len(parsed):
            return ""
        if position == last:
            return " ".join(parsed[position - 1 :])
        return parsed[position - 1]

    resolved = _PLACEHOLDER_RE.sub(fill, template).replace("$ARGUMENTS", args)
    if not positions and "$ARGUMENTS" not in template and args.strip():
        resolved = f"{resolved}\n\n{args}"
    return resolved.strip()


def _sources(workspace: Optional[str | Path]) -> list[tuple[Path, str]]:
    from harness.safety.secrets import state_dir

    from .skills import roots as skills_store

    # The compat home anchor the skill roots use, so a test never reads the machine's own.
    out = [(skills_store.home_dir() / ".claude" / "commands", "compat"), (state_dir() / "commands", "user")]
    if workspace:
        ws = Path(workspace).expanduser()
        out += [(ws / ".claude" / "commands", "compat"), (ws / ".reeve" / "commands", "project")]
    return out


def _read(
    path: Path,
    name: str,
    source: str,
    *,
    plugin: str = "",
    transform=None,
    content: Optional[str] = None,
    meta: Optional[dict[str, Any]] = None,
) -> Optional[CommandSpec]:
    """A command from its file, or from `content` (a plugin's inline body), with `meta`
    (a plugin manifest's object form) winning over the frontmatter."""
    from .plugins import split_frontmatter

    try:
        if content is not None:
            text = content
        elif path.stat().st_size > _MAX_BODY:
            logger.warning("command %s ignored: larger than %d bytes", path, _MAX_BODY)
            return None
        else:
            text = path.read_text(encoding="utf-8")
        if len(text) > _MAX_BODY:
            logger.warning("command %s ignored: larger than %d bytes", name, _MAX_BODY)
            return None
        frontmatter, body, problem = split_frontmatter(text)
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning("command %s ignored: %s", path, exc)
        return None
    if problem:
        logger.warning("command %s: %s", path, problem)
    meta = meta or {}
    for key, front in (("description", "description"), ("argument_hint", "argument-hint"), ("model", "model")):
        if meta.get(key):
            frontmatter[front] = meta[key]
    if meta.get("allowed_tools"):
        frontmatter["allowed-tools"] = list(meta["allowed_tools"])
    tools = frontmatter.get("allowed-tools") or frontmatter.get("allowed_tools") or ()
    if isinstance(tools, str):
        tools = [part.strip() for part in tools.split(",") if part.strip()]
    body = body.strip()
    if transform is not None:
        body = transform(body)
    return CommandSpec(
        name=name,
        description=str(frontmatter.get("description") or "").strip()
        or (body.splitlines()[0][:80] if body else name),
        argument_hint=str(frontmatter.get("argument-hint") or frontmatter.get("argument_hint") or ""),
        source=source,
        path=str(path),
        model=str(frontmatter.get("model") or ""),
        allowed_tools=tuple(str(t) for t in tools),
        plugin=plugin,
        body=body,
    )


def _folder(base: Path, source: str) -> list[CommandSpec]:
    if not base.is_dir():
        return []
    out: list[CommandSpec] = []
    for path in sorted(base.rglob("*.md")):
        if not path.is_file():
            continue
        name = path.relative_to(base).with_suffix("").as_posix().replace("/", ":")
        spec = _read(path, name, source)
        if spec is not None:
            out.append(spec)
    return out


def load_commands(workspace: Optional[str | Path] = None) -> dict[str, CommandSpec]:
    """Every command in scope, by name, the higher source winning a collision. Never
    raises: a file that cannot be read is logged and left out."""
    from .plugins import resolve_plugins

    found: dict[str, CommandSpec] = {}
    try:
        plugins = resolve_plugins(workspace=workspace)
        for command in plugins.commands:
            spec = _read(
                command.path,
                command.name,
                "plugin",
                plugin=command.plugin_id or f"{command.plugin}@inline",
                transform=lambda body, c=command: _plugin_paths(body, c, workspace),
                content=command.content,
                meta=dict(command.meta),
            )
            if spec is not None:
                found[spec.name] = spec
    except Exception as exc:
        logger.warning("plugin commands not loaded: %s", exc)
    for base, source in _sources(workspace):
        for spec in _folder(base, source):
            found[spec.name] = spec
    return found


def _plugin_paths(body: str, command: Any, workspace: Any) -> str:
    from .plugins import substitute_body

    if command.root is None:
        return body
    return substitute_body(
        body,
        plugin_root=command.root,
        plugin_id=command.plugin_id or f"{command.plugin}@inline",
        workspace=workspace,
    )
