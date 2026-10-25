"""What in a plugin runs code — hooks, MCP servers, language servers, `bin/` — converted
from Claude Code's formats and fingerprinted for consent (design §5.4-§5.7, §9.3).

Nothing here runs anything. It reads a plugin's declarations and returns what the
existing runners would run, plus a digest of it: `executable_digest` covers every hook's
event, matcher and command, every server's command/args/URL (env NAMES, never values), every
`bin/` file's bytes, and every file inside the plugin those commands name. A person
consents to that digest; a changed script is a changed digest, and code whose digest was
not consented to never runs (design §9.3).

A plugin never grants (design §9.5): a hook's `allow` and `updatedInput` are ignored by the
runner, `ask` only ever adds a human, and a plugin's MCP server always asks.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .compat import TOOL_ALIASES

#: Claude Code's hook events with an honest Reeve equivalent; any other is skipped.
EVENT_MAP = {
    "SessionStart": "session_start",
    "UserPromptSubmit": "user_prompt_submit",
    "PreToolUse": "pre_tool_use",
    "PostToolUse": "post_tool_use",
    "PostToolUseFailure": "post_tool_use_failure",
    "Stop": "stop",
    "SessionEnd": "session_end",
}
MAX_TIMEOUT_SECONDS = 120.0
_NAME_LIST = re.compile(r"^[A-Za-z0-9_\-, |]*$")
#: A Claude Code tool that is two Reeve tools: a matcher naming it matches both.
_ALSO_MATCHES = {"Edit": ("apply_patch",)}
_ROOT_VARS = re.compile(r"\$\{(?:CLAUDE|REEVE)_PLUGIN_ROOT\}")
_ANY_VAR = re.compile(r"\$\{(?:CLAUDE|REEVE)_(PLUGIN_ROOT|PLUGIN_DATA|PROJECT_DIR)\}")
_USER_CONFIG = re.compile(r"\$\{user_config\.([A-Za-z0-9_]+)\}")


@dataclass
class Executable:
    """A plugin's code-running parts, converted, with what was left out."""

    hooks: list[dict[str, Any]] = field(default_factory=list)
    mcp: dict[str, dict[str, Any]] = field(default_factory=dict)
    lsp: dict[str, dict[str, Any]] = field(default_factory=dict)  # extension -> server
    bin_dir: Optional[Path] = None
    skipped: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.hooks or self.mcp or self.lsp or self.bin_dir)


def _json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _declared(root: Path, raw: dict[str, Any], key: str, default: str) -> list[Any]:
    """Every declaration of a component: its default file, then the manifest's (a path to a
    JSON file, an inline object, or a list of either). A later one wins a name."""
    found: list[Any] = []
    if (root / default).is_file():
        found.append(_json(root / default))
    value = raw.get(key)
    for item in value if isinstance(value, list) else ([value] if value is not None else []):
        if isinstance(item, str) and item.startswith("./") and ".." not in Path(item).parts:
            found.append(_json(root / item))
        elif isinstance(item, dict):
            found.append(item)
    return [f for f in found if isinstance(f, dict)]


def _matcher(value: Any) -> Optional[str]:
    """Claude Code's matcher as a Reeve regex: a list of names (aliased, anchored), a
    regex kept as written, or match-all. None: it cannot be translated."""
    text = str(value or "").strip()
    if text in ("", "*"):
        return ""
    if _NAME_LIST.match(text):
        names = [n.strip() for n in re.split(r"[|,]", text) if n.strip()]
        mapped: list[str] = []
        for name in names:
            reeve = TOOL_ALIASES.get(name, name)
            if reeve is None:
                continue
            mapped.append(re.escape(reeve))
            # Claude Code's Edit covers what Reeve splits between two tools.
            mapped += [re.escape(extra) for extra in _ALSO_MATCHES.get(name, ())]
        return f"^(?:{'|'.join(dict.fromkeys(mapped))})$" if mapped else None
    try:
        re.compile(text)
    except re.error:
        return None
    return text


def shell_argv(command: str, shell: str = "") -> Optional[list[str]]:
    """The argv that runs a shell-form hook: sh on POSIX; on Windows Git Bash when present,
    else PowerShell; or the declared `shell`."""
    # `shutil.which("bash")` on Windows is usually the WSL launcher (System32\bash.exe), which runs
    # a Linux filesystem the hook's paths do not exist in: the finder never accepts it.
    from harness.tools.shell_kinds import BASH, POWERSHELL, PWSH, local_shells

    found = local_shells()
    if shell == "powershell" or (os.name == "nt" and shell != "bash" and BASH not in found):
        # 7 when the machine has it (a hook author who wrote `pwsh` meant it), else Windows PowerShell.
        exe = next((found[kind].path for kind in (PWSH, POWERSHELL) if kind in found), None)
        exe = exe or shutil.which("pwsh") or shutil.which("powershell")
        return [exe, "-NoProfile", "-Command", command] if exe else None
    if os.name == "nt" or shell == "bash":
        exe = found[BASH].path if BASH in found else None
    else:
        exe = shutil.which("sh")
    return [exe, "-c", command] if exe else None


def hooks_from_claude(doc: dict[str, Any], *, plugin: str, skipped: list[str]) -> list[dict[str, Any]]:
    """`hooks/hooks.json` → Reeve hook declarations (`event`, `name`, `command`,
    `matcher`, `timeout_seconds`, `shell`). What cannot be honoured is skipped, not
    approximated: an unknown event, a non-command handler, an `if` condition (dropping it
    would run the hook more widely than its author meant), and `${user_config.*}` in a
    shell string (the value would be re-parsed by the shell)."""
    out: list[dict[str, Any]] = []
    events = doc.get("hooks") if isinstance(doc.get("hooks"), dict) else {}
    for claude_event, groups in events.items():
        event = EVENT_MAP.get(claude_event)
        if event is None:
            skipped.append(f"hook {claude_event}: event not supported")
            continue
        for group in groups if isinstance(groups, list) else []:
            if not isinstance(group, dict):
                continue
            matcher = _matcher(group.get("matcher"))
            if matcher is None:
                skipped.append(f"hook {claude_event}: matcher {group.get('matcher')!r} cannot be translated")
                continue
            for handler in group.get("hooks") or []:
                index = len(out) + 1
                if not isinstance(handler, dict):
                    continue
                if handler.get("type", "command") != "command":
                    skipped.append(f"hook {claude_event}: a {handler.get('type')} handler is not supported")
                    continue
                if handler.get("if"):
                    skipped.append(f"hook {claude_event}: an `if` condition is not supported, so the hook is left out")
                    continue
                command = str(handler.get("command") or "").strip()
                if not command:
                    continue
                args = handler.get("args")
                if isinstance(args, list):
                    argv = [command, *map(str, args)]
                    shell = ""
                else:
                    if _USER_CONFIG.search(command):
                        skipped.append(
                            f"hook {claude_event}: `${{user_config.*}}` in a shell command would be re-read "
                            "by the shell; use args or the environment"
                        )
                        continue
                    argv = [command]
                    shell = str(handler.get("shell") or "auto")
                try:
                    timeout = float(handler.get("timeout", 60))
                except (TypeError, ValueError):
                    timeout = 60.0
                out.append(
                    {
                        "event": event,
                        "name": f"{plugin}:{claude_event}#{index}",
                        "command": argv,
                        "shell": shell,
                        "matcher": matcher,
                        "timeout_seconds": max(0.1, min(timeout, MAX_TIMEOUT_SECONDS)),
                    }
                )
    return out


def _loopback(url: str) -> bool:
    from urllib.parse import urlparse

    host = (urlparse(url).hostname or "").lower()
    return host in ("localhost", "127.0.0.1", "::1")


def mcp_from_plugin(root: Path, raw: dict[str, Any], *, plugin: str, skipped: list[str]) -> dict[str, dict[str, Any]]:
    """The plugin's MCP servers, renamed `plugin_<plugin>_<server>` (the name Claude Code
    gives them, so its matchers keep working) and always asking before a call."""
    servers: dict[str, dict[str, Any]] = {}
    for doc in _declared(root, raw, "mcpServers", ".mcp.json"):
        table = doc.get("mcpServers") if isinstance(doc.get("mcpServers"), dict) else doc
        for name, spec in table.items():
            if not isinstance(spec, dict):
                continue
            key = re.sub(r"[^A-Za-z0-9_-]", "_", f"plugin_{plugin}_{name}")
            url = str(spec.get("url") or "")
            if str(spec.get("type") or "").lower() in ("mcpb", "dxt") or url.endswith((".mcpb", ".dxt")):
                skipped.append(f"mcp {name}: a bundle is not supported")
                continue
            if url and not (url.startswith("https://") or (url.startswith("http://") and _loopback(url))):
                skipped.append(f"mcp {name}: {url} must be https:// (or http:// to this machine)")
                continue
            if not url and not spec.get("command"):
                skipped.append(f"mcp {name}: no command or url")
                continue
            servers[key] = {**spec, "requires_approval": True}
    return servers


def lsp_from_plugin(root: Path, raw: dict[str, Any], *, skipped: list[str]) -> dict[str, dict[str, Any]]:
    """Extension → language server, from `.lsp.json` and the manifest."""
    table: dict[str, dict[str, Any]] = {}
    for doc in _declared(root, raw, "lspServers", ".lsp.json"):
        servers = doc.get("lspServers") if isinstance(doc.get("lspServers"), dict) else doc
        for name, spec in servers.items():
            if not isinstance(spec, dict):
                continue
            mapping = spec.get("extensionToLanguage")
            if not spec.get("command") or not isinstance(mapping, dict):
                skipped.append(f"lsp {name}: needs command and extensionToLanguage")
                continue
            for ext, language in mapping.items():
                table[str(ext)] = {"name": name, "language": str(language), **spec}
    return table


def read_executable(root: Path, raw: dict[str, Any], *, plugin: str) -> Executable:
    """Everything that would run, from a plugin's folder and manifest. Never raises."""
    out = Executable()
    try:
        for doc in _declared(root, raw, "hooks", "hooks/hooks.json"):
            out.hooks += hooks_from_claude(doc, plugin=plugin, skipped=out.skipped)
        out.mcp = mcp_from_plugin(root, raw, plugin=plugin, skipped=out.skipped)
        out.lsp = lsp_from_plugin(root, raw, skipped=out.skipped)
        if (root / "bin").is_dir() and any(p.is_file() for p in (root / "bin").iterdir()):
            out.bin_dir = root / "bin"
    except Exception as exc:  # a malformed declaration costs its plugin's code, not the session
        out = Executable(skipped=[f"code-running parts could not be read: {exc}"])
    return out


def _referenced_files(root: Path, parts: list[str]) -> list[Path]:
    """Files inside the plugin that a command names through `${CLAUDE_PLUGIN_ROOT}`."""
    out = []
    for part in parts:
        for match in re.finditer(r"\$\{(?:CLAUDE|REEVE)_PLUGIN_ROOT\}([^\s\"']*)", part):
            path = (root / match.group(1).lstrip("/\\")).resolve()
            if path.is_file() and root.resolve() in path.parents:
                out.append(path)
    return out


def executable_digest(root: Path, exe: Executable) -> str:
    """`sha256:` over what runs (design §9.3). A comment or key order does not change it;
    a changed command, matcher, server, `bin/` file or referenced script does. Env VALUES
    are left out (they may be secrets); env NAMES are in."""
    if exe.empty:
        return ""
    items: list[str] = []
    files: list[Path] = []
    for hook in exe.hooks:
        items.append(json.dumps(["hook", hook["event"], hook["matcher"], hook["command"], hook["shell"]]))
        files += _referenced_files(root, hook["command"])
    for name, spec in sorted(exe.mcp.items()):
        argv = [str(spec.get("command") or ""), *map(str, spec.get("args") or [])]
        env_names = sorted((spec.get("env") or {}).keys()) if isinstance(spec.get("env"), dict) else []
        items.append(json.dumps(["mcp", name, spec.get("type") or "", argv, spec.get("url") or "", env_names]))
        files += _referenced_files(root, argv)
    for ext, spec in sorted(exe.lsp.items()):
        argv = [str(spec.get("command") or ""), *map(str, spec.get("args") or [])]
        items.append(json.dumps(["lsp", ext, spec["name"], argv]))
        files += _referenced_files(root, argv)
    if exe.bin_dir is not None:
        files += [p for p in exe.bin_dir.rglob("*") if p.is_file()]
    from .node import node_setup

    node = node_setup(root)
    if node and "argv" in node:
        # The packages its code will load are pinned by these two files (design §8.5).
        items.append(json.dumps(["node", node["argv"]]))
        files += [root / "package.json", root / node["lockfile"]]
    base = root.resolve()
    for path in sorted({p.resolve() for p in files}):
        rel = path.relative_to(base).as_posix()
        items.append(json.dumps(["file", rel, hashlib.sha256(path.read_bytes()).hexdigest()]))
    digest = hashlib.sha256("\n".join(sorted(items)).encode("utf-8")).hexdigest()
    return "sha256:" + digest


def resolve_vars(value: Any, *, root: Path, data: Path, workspace: Any, config: dict[str, Any]) -> Any:
    """`${CLAUDE_PLUGIN_ROOT}`/`DATA`/`PROJECT_DIR` and `${user_config.KEY}` in a runnable
    declaration (argv, env, url, headers), with `/` separators (design §4.6). A secret may
    appear here: this is a process's argv or environment, never text the model reads."""
    if isinstance(value, str):

        def path_var(match: re.Match[str]) -> str:
            which = match.group(1)
            if which == "PLUGIN_ROOT":
                return root.resolve().as_posix()
            if which == "PLUGIN_DATA":
                data.mkdir(parents=True, exist_ok=True)
                return data.as_posix()
            return Path(workspace).resolve().as_posix() if workspace else match.group(0)

        value = _ANY_VAR.sub(path_var, value)
        return _USER_CONFIG.sub(lambda m: str(config.get(m.group(1), m.group(0))), value)
    if isinstance(value, list):
        return [resolve_vars(v, root=root, data=data, workspace=workspace, config=config) for v in value]
    if isinstance(value, dict):
        return {k: resolve_vars(v, root=root, data=data, workspace=workspace, config=config) for k, v in value.items()}
    return value
