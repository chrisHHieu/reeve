"""Language-server resolution — which program serves which file, and where it lives.

Four decisions are encoded here, each with a reason:

- **A language is a table entry, not a search.** Python is `pyright-langserver --stdio`
  and nothing else ships as a fallback — deliberately no `pylsp` second attempt:
  pyright understands `didChange` full sync and hover/definition out of the box, and
  silently trying a second server after the first is missing would turn "you have not
  installed a server" into a slow mystery. The precise error below tells the user how to
  fix that instead.
- **Workspace-local binaries win over PATH.** A project that pins pyright in its dev
  dependencies or in its virtualenv means THAT server, with THAT version, for THAT
  project; `node_modules/.bin` and `.venv` are checked before `shutil.which`.
- **Config overrides are per language and per workspace.** `[lsp.servers.python] command
  = ["…"]` in the global or workspace `config.toml` replaces the default command
  verbatim; every token after the first is passed through as an argument.
- **A missing server is a sentence, not a stack trace.** The error names the file
  extension it was asked about, the program it looked for, and the two ways to fix it.
"""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from harness.config.config import Config, load_config
from .python_env import python_settings

logger = logging.getLogger("harness.integrations.lsp")

#: The shipped defaults, keyed by the language name config and errors speak.
_DEFAULT_SERVERS: dict[str, list[str]] = {
    "python": ["pyright-langserver", "--stdio"],
    "typescript": ["typescript-language-server", "--stdio"],
    "javascript": ["typescript-language-server", "--stdio"],
    "go": ["gopls"],
    "rust": ["rust-analyzer"],
    "bash": ["bash-language-server", "start"],
    "yaml": ["yaml-language-server", "--stdio"],
    "json": ["vscode-json-language-server", "--stdio"],
    "css": ["vscode-css-language-server", "--stdio"],
    "html": ["vscode-html-language-server", "--stdio"],
    "svelte": ["svelteserver", "--stdio"],
    "graphql": ["graphql-lsp", "server", "-m", "stream"],
    "php": ["intelephense", "--stdio"],
    "dockerfile": ["docker-langserver", "--stdio"],
}

#: extension -> (language key, LSP `languageId`). The two differ on the JS/TS dialects:
#: `typescriptreact`/`javascriptreact` are what a server expects in `didOpen`, while
#: `typescript`/`javascript` are what `[lsp.servers.*]` is keyed by (one TS server serves
#: both dialects, and keying config by dialect would make users configure the same
#: command twice).
_EXTENSIONS: dict[str, tuple[str, str]] = {
    ".py": ("python", "python"),
    ".pyi": ("python", "python"),
    ".ts": ("typescript", "typescript"),
    ".tsx": ("typescript", "typescriptreact"),
    ".mts": ("typescript", "typescript"),
    ".cts": ("typescript", "typescript"),
    ".js": ("javascript", "javascript"),
    ".jsx": ("javascript", "javascriptreact"),
    ".mjs": ("javascript", "javascript"),
    ".cjs": ("javascript", "javascript"),
    ".go": ("go", "go"),
    ".rs": ("rust", "rust"),
    ".sh": ("bash", "shellscript"),
    ".bash": ("bash", "shellscript"),
    ".zsh": ("bash", "shellscript"),
    ".yaml": ("yaml", "yaml"),
    ".yml": ("yaml", "yaml"),
    ".json": ("json", "json"),
    ".jsonc": ("json", "jsonc"),
    ".css": ("css", "css"),
    ".scss": ("css", "scss"),
    ".less": ("css", "less"),
    ".html": ("html", "html"),
    ".htm": ("html", "html"),
    ".svelte": ("svelte", "svelte"),
    ".graphql": ("graphql", "graphql"),
    ".gql": ("graphql", "graphql"),
    ".php": ("php", "php"),
}

#: Whole-name matches for languages whose files have no useful suffix.
_FILENAMES: dict[str, tuple[str, str]] = {
    "dockerfile": ("dockerfile", "dockerfile"),
}

#: Workspace-local bin directories, most-specific first. The `Scripts`/`bin` pair covers
#: both venv layouts; `node_modules/.bin` is where a pinned npm-based server lives.
_LOCAL_BIN_DIRS: tuple[Path, ...] = (
    Path("node_modules") / ".bin",
    Path(".venv") / "Scripts",
    Path(".venv") / "bin",
    Path("venv") / "Scripts",
    Path("venv") / "bin",
)


class ServerNotFound(Exception):
    """No executable can serve this language; the message states the fix."""


@dataclass(frozen=True)
class ServerSpec:
    """A resolved server: the command to spawn and where its program came from."""

    language: str
    language_id: str
    command: list[str] = field(default_factory=list)
    source: str = ""  # "config" | "plugin" | "workspace" | "path" | "default"
    #: `initializationOptions` for the handshake. Some servers take required settings
    #: here and nowhere else — `typescript-language-server` 6 reads its `tsserver.path`
    #: from this object (the old `--tsserver-path` CLI flag is gone).
    initialization_options: dict = field(default_factory=dict)
    #: The answer to the server's `workspace/configuration` requests (python_env.py).
    settings: dict = field(default_factory=dict)


def known_languages() -> list[str]:
    """The language keys the shipped table serves, sorted — for status surfaces that say
    what CAN run before anything has started."""
    return sorted(_DEFAULT_SERVERS)


def supported_extensions(config: Optional[Config] = None) -> str:
    """The extensions this module can route, for an error that can name them.

    Configured languages count: `[lsp.servers.<lang>] extensions = […]` makes a new
    language routable without touching this table.
    """
    extensions = set(_EXTENSIONS)
    extensions.update(_configured_languages(config))
    return ", ".join(sorted(extensions))


def _configured_languages(config: Optional[Config]) -> dict[str, tuple[str, str]]:
    """`{extension: (language, language_id)}` from `[lsp.servers.<lang>] extensions`.

    A shipped language keeps its built-in routing (override its COMMAND, not its
    extensions); this is how a language the table has never heard of — Ruby, Lua, C —
    becomes routable from config alone.
    """
    cfg = config if config is not None else load_config()
    table = cfg.lsp if isinstance(cfg.lsp, dict) else {}
    servers = table.get("servers")
    out: dict[str, tuple[str, str]] = {}
    if not isinstance(servers, dict):
        return out
    for language, entry in servers.items():
        if not isinstance(entry, dict):
            continue
        raw = entry.get("extensions")
        if not isinstance(raw, list):
            continue
        language_id = str(entry.get("language_id") or language)
        for item in raw:
            extension = str(item).strip().lower()
            if not extension:
                continue
            if not extension.startswith("."):
                extension = f".{extension}"
            out[extension] = (str(language), language_id)
    return out


def language_for(
    path: str | Path, config: Optional[Config] = None
) -> Optional[tuple[str, str]]:
    """`(language, language_id)` for a file's extension, or None when unsupported.

    Shipped routing wins for the extensions it owns (config changes the command, not the
    language); a configured `extensions` list covers everything else. A whole-name match
    (`Dockerfile`) is checked when there is no useful suffix.
    """
    file = Path(str(path))
    extension = file.suffix.lower()
    builtin = _EXTENSIONS.get(extension)
    if builtin is not None:
        return builtin
    name = file.name.lower()
    if name in _FILENAMES:
        return _FILENAMES[name]
    if name.endswith(".dockerfile"):
        return _FILENAMES["dockerfile"]
    configured = _configured_languages(config).get(extension)
    if configured is not None:
        return configured
    # A language only a plugin knows. No workspace reaches this lookup, so it sees the
    # plugins on for every workspace (session and person-installed ones).
    plugin = _plugin_servers(None).get(extension)
    return (plugin["language"], plugin["language"]) if plugin else None


def _plugin_servers(workspace: Optional[str | Path]) -> dict[str, dict]:
    """Extension -> an enabled plugin's language server (design §5.6), already
    cleared by the plugin's consented code digest. Never raises."""
    try:
        from harness.extensions.plugins import resolve_plugins

        table = resolve_plugins(workspace=workspace).lsp
    except Exception:
        return {}
    out: dict[str, dict] = {}
    for ext, spec in table.items():
        key = str(ext).strip().lower()
        out[key if key.startswith(".") else f".{key}"] = spec
    return out


def lsp_enabled(config: Optional[Config] = None, workspace: Optional[str | Path] = None) -> bool:
    """Whether `[lsp] enabled` allows the tool at all (default true)."""
    cfg = config if config is not None else load_config(workspace)
    table = cfg.lsp if isinstance(cfg.lsp, dict) else {}
    return bool(table.get("enabled", True))


def _candidate_names(name: str) -> list[str]:
    """The names an executable may have on this OS (`pyright` → `pyright.cmd` on Windows)."""
    if os.name != "nt":
        return [name]
    return [name, f"{name}.exe", f"{name}.cmd", f"{name}.bat"]


def _executable_in(directory: Path, name: str) -> Optional[str]:
    if not name or "/" in name or "\\" in name:
        return None
    for candidate in _candidate_names(name):
        path = directory / candidate
        try:
            if not path.is_file():
                continue
            # POSIX also demands the executable bit; Windows has no mode bits to read.
            if os.name != "nt" and not os.access(path, os.X_OK):
                continue
        except OSError:
            continue
        return str(path)
    return None


def _locate(name: str, workspace: Path) -> Optional[tuple[str, str]]:
    """`(path, source)` for one binary token: workspace-local first, then PATH."""
    if not name:
        return None
    expanded = Path(name).expanduser()
    if expanded.is_absolute():
        return (str(expanded), "workspace") if expanded.is_file() else None
    if "/" in name or "\\" in name:
        candidate = workspace / expanded
        return (str(candidate), "workspace") if candidate.is_file() else None
    for relative in _LOCAL_BIN_DIRS:
        found = _executable_in(workspace / relative, name)
        if found:
            return found, "workspace"
    found = shutil.which(name)
    return (found, "path") if found else None


def _missing_message(language: str, extension: str, binary: str, workspace: Path) -> str:
    where = f"{extension} ({language})" if extension else (language or "this file")
    local = ", ".join(str(workspace / relative) for relative in _LOCAL_BIN_DIRS[:3])
    return (
        f"no language server found for {where}: '{binary}' is not on PATH or under "
        f"{local}. Install it, or point `[lsp.servers.{language}] command` at one in "
        f'config.toml (e.g. command = ["{binary}", "--stdio"]).'
    )


def _config_command(
    cfg: Config, language: str
) -> tuple[Optional[list[str]], Optional[str]]:
    """The `[lsp.servers.<language>] command` override, if it is usable.

    A `command` that is not a non-empty list of strings is REPORTED and ignored: falling
    back to the default server is recoverable, while a half-parsed command line would
    spawn something unexpected. `None` means "no override", the empty list means
    "override present but unusable" and is treated the same way after the warning.
    """
    table = cfg.lsp if isinstance(cfg.lsp, dict) else {}
    servers = table.get("servers")
    entry = servers.get(language) if isinstance(servers, dict) else None
    if not isinstance(entry, dict) or "command" not in entry:
        return None, None
    raw = entry.get("command")
    if not isinstance(raw, list) or not raw:
        logger.warning(
            "config: [lsp.servers.%s] command must be a non-empty list of strings",
            language,
        )
        return [], None
    command = [str(part) for part in raw if str(part).strip()]
    if not command:
        logger.warning("config: [lsp.servers.%s] command named no program", language)
        return [], None
    return command, "config"


def resolve_server(
    language: str,
    workspace: str | Path,
    *,
    config: Optional[Config] = None,
    extension: str = "",
    language_id: str = "",
) -> ServerSpec:
    """The command that serves `language` in `workspace`, or `ServerNotFound`.

    Resolution order: the config override, then the shipped default for the language;
    either way the program itself is located workspace-locally before PATH.
    """
    workspace_path = Path(workspace).expanduser().resolve()
    cfg = config if config is not None else load_config(workspace_path)
    command, source = _config_command(cfg, language)
    plugin_options: dict = {}
    if command is None:
        # A plugin's server outranks the shipped default and yields to the person's own
        # override (design §5.6).
        plugin = _plugin_servers(workspace_path).get(extension.lower()) if extension else None
        if plugin is None:
            plugin = next((p for p in _plugin_servers(workspace_path).values() if p["language"] == language), None)
        if plugin is not None:
            command = [str(plugin["command"]), *map(str, plugin.get("args") or [])]
            source = "plugin"
            plugin_options = dict(plugin.get("initializationOptions") or {})
    if command is None:
        command = list(_DEFAULT_SERVERS.get(language) or [])
        source = "default"
    if not command:
        raise ServerNotFound(
            _missing_message(language, extension, "<none>", workspace_path)
        )
    binary = command[0]
    located = _locate(binary, workspace_path)
    if located is None:
        raise ServerNotFound(_missing_message(language, extension, binary, workspace_path))
    path, binary_source = located
    # Only the program is re-rooted: every other token is an argument the user (or
    # the default table) wrote and must stay exactly as written.
    final = [path, *command[1:]]
    options: dict = {}
    if language in ("typescript", "javascript"):
        # `typescript-language-server` refuses to start when it cannot find a
        # `typescript` package in the workspace, which is the normal state of a repo
        # that has not run `npm install` yet. Point it at whatever typescript this
        # machine does have; `initializationOptions` is the only channel TSLS 6
        # still reads (`--tsserver-path` was removed).
        tsserver = _tsserver_path(workspace_path)
        if tsserver is not None:
            options = {"tsserver": {"path": str(tsserver)}}
    return ServerSpec(
        language=language,
        language_id=language_id or language,
        command=final,
        source=source if source in ("config", "plugin") else binary_source,
        initialization_options=options or plugin_options,
        settings=python_settings(workspace_path) if language == "python" else {},
    )


def _tsserver_path(workspace: Path) -> Optional[Path]:
    """The `typescript` package's tsserver entry point this machine can see."""
    candidates = [workspace / "node_modules" / "typescript" / "lib" / "tsserver.js"]
    appdata = os.environ.get("APPDATA")
    if appdata:
        candidates.append(
            Path(appdata) / "npm" / "node_modules" / "typescript" / "lib" / "tsserver.js"
        )
    node = shutil.which("node")
    if node:
        node_dir = Path(node).resolve().parent
        candidates.append(
            node_dir / "node_modules" / "typescript" / "lib" / "tsserver.js"
        )
        candidates.append(
            node_dir.parent / "lib" / "node_modules" / "typescript" / "lib" / "tsserver.js"
        )
    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate
        except OSError:  # an unreadable candidate is simply not it
            continue
    return None
