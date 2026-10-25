"""Per-process LSP management — one client per (workspace root, language).

Why a manager between the tool and the client at all:

- **Process lifetime ≠ engine lifetime.** A session engine is rebuilt on resume, on
  workspace promotion, on a subagent spawn; a language server that took ten seconds to
  index a repository must not be restarted for each. One client per root and language is
  reused across every session and child engine that works there.
- **Spawn must be once even under parallel tool calls.** The turn engine overlaps
  low-risk reads, so two `lsp` calls can arrive together. `client_for` holds a lock and
  re-checks, so the second caller joins the first client instead of starting a twin.
- **The tool speaks operations, not JSON-RPC.** `request()` is the one place that knows
  which LSP method an operation maps to, how to feed the document in first, how the
  result normalizes, and how to phrase a missing server. The model never sees a
  protocol method name unless something has already gone wrong.

Lifecycle: `SessionManager` owns one manager per process and calls `close()` from
`aclose()`, so an app shutdown terminates every language server it started. Direct
callers (CLI, benchmarks, tests) use `default_manager()`, which registers the same
`close()` with `atexit`.
"""

from __future__ import annotations

import atexit
import logging
import threading
from pathlib import Path
from typing import Any, Optional

from harness.config.config import Config, load_config
from harness.config.roots import containing_root
from .client import (
    LSPClient,
    LSPError,
    to_lsp_position,
    uri_to_path,
)
from .servers import (
    ServerNotFound,
    known_languages,
    language_for,
    lsp_enabled,
    resolve_server,
    supported_extensions,
)

logger = logging.getLogger("harness.integrations.lsp")

#: How many rows one result may carry (the tool clamps its own `max_results` too, so the
#: manager is safe when called directly).
DEFAULT_MAX_RESULTS = 20
MAX_RESULTS_CAP = 50

#: Diagnostics settle window and deadline. See `LSPClient.wait_for_diagnostics` for why
#: both exist; these defaults are tuned for pyright's two passes (syntax, then types).
DIAGNOSTICS_SETTLE_MS = 400
DIAGNOSTICS_TIMEOUT_MS = 8_000
#: Workspace symbol search scans the whole project; it gets more room than one document.
WORKSPACE_SYMBOL_TIMEOUT_MS = 20_000

_SEVERITIES = {1: "error", 2: "warning", 3: "info", 4: "hint"}
_SEVERITY_RANK = {"error": 0, "warning": 1, "info": 2, "hint": 3}

_SYMBOL_KINDS = {
    1: "file", 2: "module", 3: "namespace", 4: "package", 5: "class", 6: "method",
    7: "property", 8: "field", 9: "constructor", 10: "enum", 11: "interface",
    12: "function", 13: "variable", 14: "constant", 15: "string", 16: "number",
    17: "boolean", 18: "array", 19: "object", 20: "key", 21: "null",
    22: "enum member", 23: "struct", 24: "event", 25: "operator", 26: "type parameter",
}

_OPERATION_METHODS = {
    "definition": "textDocument/definition",
    "references": "textDocument/references",
    "hover": "textDocument/hover",
    "document_symbol": "textDocument/documentSymbol",
    "workspace_symbol": "workspace/symbol",
}

_MAX_SNIPPET_CHARS = 160


class LSPManager:
    """Owns the language-server clients of one process (or one SessionManager)."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._clients: dict[tuple[str, str], LSPClient] = {}
        self._closed = False

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def snapshot(self, config: Optional[Config] = None) -> dict[str, Any]:
        """The pool's live state, for the GUI's Status popover (GET /v1/lsp).

        Live clients only: language servers spawn lazily on the first `lsp` call, so
        "configured but idle" is not a state this pool can observe — `enabled` carries
        the config half and `known_languages` says what could be spawned. `alive`
        separates a healthy server from one whose process died (the next call replaces
        it), which is the one failure worth a dot.
        """
        with self._lock:
            clients = list(self._clients.values())
        servers = [
            {
                "language": client.language or "",
                "language_id": client.language_id or client.language or "",
                "workspace": str(client.roots[0]) if client.roots else "",
                "command": " ".join(client.command),
                "alive": client.alive,
            }
            for client in clients
        ]
        servers.sort(key=lambda row: (row["language"], row["workspace"]))
        return {
            "enabled": lsp_enabled(config if config is not None else load_config()),
            "servers": servers,
            "known_languages": known_languages(),
        }

    def client_for(
        self,
        workspace: str | Path,
        language: str,
        *,
        language_id: str = "",
        extension: str = "",
        roots: Optional[list[str | Path]] = None,
        config: Optional[Any] = None,
    ) -> LSPClient:
        """The live client for `(workspace, language)`, spawned on first use."""
        workspace_path = Path(workspace).expanduser().resolve()
        key = (str(workspace_path), language)
        with self._lock:
            if self._closed:
                raise LSPError("the LSP manager was closed")
            client = self._clients.get(key)
            if client is not None and not client.alive:
                # A crashed server must not poison every later call: drop it and let
                # this call start a fresh one. The old client is closed to reap its pipes.
                self._clients.pop(key, None)
                client.close()
                client = None
            if client is None:
                spec = resolve_server(
                    language,
                    workspace_path,
                    config=config,
                    extension=extension,
                    language_id=language_id,
                )
                client = LSPClient(
                    spec.command,
                    workspace_path,
                    language=language,
                    language_id=language_id or spec.language_id,
                    roots=[workspace_path, *(roots or [])],
                    initialization_options=spec.initialization_options,
                    settings=spec.settings,
                )
                client.start()
                try:
                    client.initialize()
                except Exception:
                    client.close()
                    raise
                logger.info(
                    "lsp: started %s server for %s (%s)",
                    language,
                    workspace_path,
                    spec.source,
                )
                self._clients[key] = client
            elif roots:
                client.sync_workspace_folders([workspace_path, *roots])
            return client

    def request(
        self,
        operation: str,
        path: str | Path,
        *,
        workspace: str | Path,
        roots: Optional[list[str | Path]] = None,
        line: Optional[int] = None,
        character: Optional[int] = None,
        query: Optional[str] = None,
        max_results: int = DEFAULT_MAX_RESULTS,
        #: Diagnostics timing overrides. None uses the module defaults (tuned for the
        #: `lsp` tool); the post-edit note asks for a tighter budget so a note never
        #: holds a turn while a server indexes.
        settle_ms: Optional[int] = None,
        timeout_ms: Optional[int] = None,
    ) -> dict[str, Any]:
        """Run one operation and return its normalized value, or `{"error": …}`.

        Every failure the user can act on is an error dict: a missing server, a missing
        capability, a dead process, a timeout. Nothing here raises for a foreseeable
        condition, because the caller is a model-facing tool whose contract is fail-soft.
        """
        target = Path(str(path)).expanduser()
        root_list = [Path(workspace).expanduser().resolve()]
        for root in roots or []:
            resolved = Path(root).expanduser().resolve()
            if resolved not in root_list:
                root_list.append(resolved)
        try:
            config = load_config(workspace)
            # Config is loaded BEFORE the language lookup: a language the shipped table
            # does not know is routable through `[lsp.servers.<lang>] extensions`.
            mapping = language_for(target, config)
            if mapping is None:
                return {
                    "error": (
                        f"`lsp` has no language server for "
                        f"{target.suffix or 'that file type'}; supported: "
                        f"{supported_extensions(config)}"
                    )
                }
            language, language_id = mapping
            if not lsp_enabled(config):
                return {
                    "error": "`lsp` is disabled by `[lsp] enabled = false` in config.toml"
                }
            client = self.client_for(
                workspace,
                language,
                language_id=language_id,
                extension=target.suffix.lower(),
                roots=root_list,
                config=config,
            )
            unsupported = _capability_error(client, operation)
            if unsupported:
                return {"error": unsupported}
            limit = _clamp_results(max_results)

            if operation == "workspace_symbol":
                text = str(query or "").strip()
                if not text:
                    return {"error": "workspace_symbol needs `query`"}
                raw = client.request(
                    "workspace/symbol",
                    {"query": text},
                    timeout_ms=WORKSPACE_SYMBOL_TIMEOUT_MS,
                )
                return _shape_workspace_symbol(raw, text, root_list, limit)

            if operation == "document_symbol":
                uri = client.ensure_document(target, language_id)
                raw = client.request(
                    "textDocument/documentSymbol", {"textDocument": {"uri": uri}}
                )
                return _shape_document_symbol(raw, target, root_list, limit)

            if operation == "diagnostics":
                uri = client.ensure_document(target, language_id)
                client.wait_for_diagnostics(
                    uri,
                    settle_ms=(
                        settle_ms if settle_ms is not None else DIAGNOSTICS_SETTLE_MS
                    ),
                    timeout_ms=(
                        timeout_ms if timeout_ms is not None else DIAGNOSTICS_TIMEOUT_MS
                    ),
                )
                return _shape_diagnostics(
                    target, client.diagnostics(uri), root_list, limit
                )

            if line is None:
                return {"error": f"{operation} needs `line` (a 1-based line number)"}
            position = to_lsp_position(line, character or 1)
            uri = client.ensure_document(target, language_id)

            if operation == "definition":
                raw = client.request(
                    "textDocument/definition",
                    {"textDocument": {"uri": uri}, "position": position},
                )
                return _shape_locations("definition", target, raw, root_list, limit)
            if operation == "references":
                raw = client.request(
                    "textDocument/references",
                    {
                        "textDocument": {"uri": uri},
                        "position": position,
                        "context": {"includeDeclaration": True},
                    },
                )
                return _shape_locations("references", target, raw, root_list, limit)
            if operation == "hover":
                raw = client.request(
                    "textDocument/hover",
                    {"textDocument": {"uri": uri}, "position": position},
                )
                return _shape_hover(
                    target, raw, root_list, int(line), int(character or 1)
                )
            return {"error": f"unknown `lsp` operation {operation!r}"}
        except ServerNotFound as exc:
            return {"error": str(exc)}
        except LSPError as exc:
            return {"error": str(exc)}
        except (OSError, ValueError) as exc:
            return {"error": f"lsp {operation} failed: {exc}"}

    def notify_files_changed(
        self, paths: "list[str | Path]", kind: int = 2
    ) -> None:
        """Tell every live server that owns one of `paths` that it changed on disk.

        Called by the write tools after a successful edit. Without it the server keeps a
        pre-write view of files it does not have open — and for a CONFIG file that view is
        permanent: pyright, measured 2026-09-20, never notices a new `pyrightconfig.json`
        on its own, so every import in the workspace stays "could not be resolved" until a
        `workspace/didChangeWatchedFiles` arrives. `kind` is the LSP `FileChangeType`
        (1 created, 2 changed, 3 deleted); a write tool cannot tell a create from a
        change after the fact, and pyright reloads on either.
        """
        if not paths:
            return
        with self._lock:
            clients = list(self._clients.values())
        if not clients:
            return
        for path in paths:
            try:
                target = Path(str(path)).expanduser().resolve()
            except OSError:
                continue
            for client in clients:
                if containing_root(target, client.roots) is None:
                    continue
                client.notify_files_changed([(target, kind)])

    def close(self) -> None:
        """Shut down every client. Idempotent; safe to call from a shutdown hook."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            clients = list(self._clients.values())
            self._clients.clear()
        for client in clients:
            try:
                client.shutdown()
            except Exception:
                # Teardown is best-effort by definition: the next line still runs, and
                # `close()` inside `shutdown()` is what guarantees no orphan.
                logger.warning("lsp: error shutting a server down", exc_info=True)
                client.close()


def _default_manager_factory() -> LSPManager:
    manager = LSPManager()
    atexit.register(manager.close)
    return manager


_default_manager: Optional[LSPManager] = None
_default_lock = threading.Lock()


def default_manager() -> LSPManager:
    """The process-wide manager for callers with no `SessionManager` (CLI, benchmarks).

    The server path never uses this: `SessionManager` owns a manager and closes it in
    `aclose()`, so a running app terminates its servers deterministically. This fallback
    exists so a direct `build_engine()` still gets the tool, and `atexit` keeps it from
    leaking a child process after the interpreter's work is done.
    """
    global _default_manager
    with _default_lock:
        if _default_manager is None or _default_manager.closed:
            _default_manager = _default_manager_factory()
        return _default_manager


# -- normalization ----------------------------------------------------------------


def _clamp_results(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return DEFAULT_MAX_RESULTS
    return max(1, min(number, MAX_RESULTS_CAP))


def _clip(text: Any, limit: int = _MAX_SNIPPET_CHARS) -> str:
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _display_path(path: str | Path, roots: list[Path]) -> str:
    """A location as the model should read it: relative inside a session root, else absolute.

    A URI that names no local file (an `untitled:` buffer) is returned verbatim — there
    is nothing to relativize, and the string is the only handle the caller has.
    """
    raw = str(path)
    if "://" in raw:
        return raw
    target = Path(raw)
    home = containing_root(target, roots)
    if home is not None:
        try:
            return str(target.relative_to(home))
        except ValueError:  # pragma: no cover - containing_root already checked
            return raw
    return raw


def _line_snippet(path: str | Path, line: int) -> str:
    """The target line's text, clipped — one line of context for a jump target."""
    raw = str(path)
    if "://" in raw or line < 1:
        return ""
    try:
        with open(raw, "r", encoding="utf-8", errors="replace") as handle:
            for index, text in enumerate(handle, 1):
                if index == line:
                    return _clip(text.strip())
                if index > line:
                    break
    except OSError:
        return ""
    return ""


def _start_position(range_: Any) -> tuple[int, int]:
    """1-based `(line, character)` from an LSP range's start; (1, 1) when malformed."""
    if not isinstance(range_, dict):
        return 1, 1
    start = range_.get("start")
    if not isinstance(start, dict):
        return 1, 1
    try:
        return int(start.get("line", 0)) + 1, int(start.get("character", 0)) + 1
    except (TypeError, ValueError):
        return 1, 1


def _end_position(range_: Any) -> tuple[int, int]:
    if not isinstance(range_, dict):
        return 1, 1
    end = range_.get("end")
    if not isinstance(end, dict):
        return 1, 1
    try:
        return int(end.get("line", 0)) + 1, int(end.get("character", 0)) + 1
    except (TypeError, ValueError):
        return 1, 1


def _as_list(raw: Any) -> list[Any]:
    if raw is None:
        return []
    return raw if isinstance(raw, list) else [raw]


def _location_from(item: Any, roots: list[Path]) -> Optional[dict[str, Any]]:
    """One `Location` or `LocationLink` as a display-ready row, or None."""
    if not isinstance(item, dict):
        return None
    uri = item.get("uri") or item.get("targetUri")
    if not isinstance(uri, str) or not uri:
        return None
    # LocationLink prefers the name's range (`targetSelectionRange`); a plain Location
    # has only `range`. `targetRange` is the fallback when no selection range was sent.
    range_ = (
        item.get("targetSelectionRange")
        or item.get("targetRange")
        or item.get("range")
    )
    line, character = _start_position(range_)
    end_line, end_character = _end_position(range_)
    path = uri_to_path(uri)
    return {
        "path": _display_path(path, roots),
        "line": line,
        "character": character,
        "end_line": end_line,
        "end_character": end_character,
        "snippet": _line_snippet(path, line),
    }


def _shape_locations(
    operation: str,
    target: Path,
    raw: Any,
    roots: list[Path],
    limit: int,
) -> dict[str, Any]:
    seen: set[tuple[str, int, int]] = set()
    found: list[dict[str, Any]] = []
    for item in _as_list(raw):
        location = _location_from(item, roots)
        if location is None:
            continue
        key = (location["path"], location["line"], location["character"])
        if key in seen:
            continue
        seen.add(key)
        found.append(location)
    return {
        "operation": operation,
        "file": _display_path(target, roots),
        "locations": found[:limit],
        "count": min(len(found), limit),
        "total": len(found),
        "truncated": len(found) > limit,
    }


def _hover_text(contents: Any) -> str:
    """LSP `Hover.contents` in whichever of its three shapes a server chose."""
    if isinstance(contents, str):
        return contents
    if isinstance(contents, dict):
        value = contents.get("value")
        return value if isinstance(value, str) else ""
    if isinstance(contents, list):
        return "\n\n".join(part for part in (_hover_text(item) for item in contents) if part)
    return ""


def _shape_hover(
    target: Path, raw: Any, roots: list[Path], line: int, character: int
) -> dict[str, Any]:
    text = ""
    if isinstance(raw, dict):
        text = _hover_text(raw.get("contents")).strip()
    return {
        "operation": "hover",
        "file": _display_path(target, roots),
        "line": line,
        "character": character,
        "contents": text[:2000] + ("…" if len(text) > 2000 else ""),
        "found": bool(text),
    }


def _symbol_kind(value: Any) -> str:
    try:
        return _SYMBOL_KINDS.get(int(value), "symbol")
    except (TypeError, ValueError):
        return "symbol"


def _symbol_node(item: Any) -> Optional[dict[str, Any]]:
    """One document symbol, nested — handles both `DocumentSymbol` and flat shapes."""
    if not isinstance(item, dict):
        return None
    name = str(item.get("name") or "").strip()
    if not name:
        return None
    line, character = 1, 1
    path = ""
    range_ = item.get("selectionRange") or item.get("range")
    if isinstance(item.get("location"), dict):
        location = item["location"]
        uri = location.get("uri")
        if isinstance(uri, str):
            path = uri_to_path(uri)
        range_ = location.get("range") or range_
    if isinstance(range_, dict):
        line, character = _start_position(range_)
    children = [
        node
        for child in item.get("children") or []
        if (node := _symbol_node(child)) is not None
    ]
    return {
        "name": name,
        "kind": _symbol_kind(item.get("kind")),
        "detail": _clip(item.get("detail"), 80),
        "path": path,
        "line": line,
        "character": character,
        "children": children,
    }


def _count_symbols(nodes: list[dict[str, Any]]) -> int:
    return sum(1 + _count_symbols(node.get("children") or []) for node in nodes)


def _shape_document_symbol(
    raw: Any, target: Path, roots: list[Path], limit: int
) -> dict[str, Any]:
    nodes = [
        node
        for item in _as_list(raw)
        if (node := _symbol_node(item)) is not None
    ]
    kept = nodes[:limit]
    return {
        "operation": "document_symbol",
        "file": _display_path(target, roots),
        "symbols": kept,
        "count": _count_symbols(kept),
        "total": _count_symbols(nodes),
        "truncated": len(nodes) > limit,
    }


def _shape_workspace_symbol(
    raw: Any, query: str, roots: list[Path], limit: int
) -> dict[str, Any]:
    found: list[dict[str, Any]] = []
    for item in _as_list(raw):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        path = ""
        line, character = 1, 1
        location = item.get("location")
        if isinstance(location, dict):
            uri = location.get("uri")
            if isinstance(uri, str):
                path = uri_to_path(uri)
            range_ = (
                location.get("range")
                or location.get("targetSelectionRange")
                or location.get("targetRange")
            )
            line, character = _start_position(range_)
        found.append(
            {
                "name": name,
                "kind": _symbol_kind(item.get("kind")),
                "container": str(item.get("containerName") or "").strip(),
                "path": _display_path(path, roots) if path else "",
                "line": line,
                "character": character,
            }
        )
    return {
        "operation": "workspace_symbol",
        "query": query,
        "symbols": found[:limit],
        "count": min(len(found), limit),
        "total": len(found),
        "truncated": len(found) > limit,
    }


def _shape_diagnostics(
    target: Path, raw: Any, roots: list[Path], limit: int
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for item in _as_list(raw):
        if not isinstance(item, dict):
            continue
        range_ = item.get("range")
        line, character = _start_position(range_)
        end_line, end_character = _end_position(range_)
        code = item.get("code")
        rows.append(
            {
                # LSP leaves a missing severity to the client; treat it as an error, the
                # way every diagnostic a compiler omits one for usually is.
                "severity": _SEVERITIES.get(item.get("severity"), "error"),
                "line": line,
                "character": character,
                "end_line": end_line,
                "end_character": end_character,
                "message": _clip(item.get("message"), 400),
                "source": str(item.get("source") or ""),
                "code": str(code) if code is not None else "",
            }
        )
    rows.sort(key=lambda row: (_SEVERITY_RANK.get(row["severity"], 9), row["line"], row["character"]))
    counts = {"error": 0, "warning": 0, "info": 0, "hint": 0}
    for row in rows:
        counts[row["severity"]] = counts.get(row["severity"], 0) + 1
    return {
        "operation": "diagnostics",
        "file": _display_path(target, roots),
        "diagnostics": rows[:limit],
        "count": min(len(rows), limit),
        "total": len(rows),
        "truncated": len(rows) > limit,
        "severity_counts": counts,
    }


def _capability_error(client: LSPClient, operation: str) -> str:
    """Why the server cannot answer this operation, or "" when it advertised it.

    Gated BEFORE the document is sent: a server that cannot answer references will not
    answer it more usefully for having received the file, and the error is identical
    whether the file exists or not. Ops with no capability (diagnostics) always pass.
    """
    if client.supports(operation):
        return ""
    method = _OPERATION_METHODS.get(operation, operation)
    return (
        f"the {client.language or 'language'} language server does not support "
        f"{method} — it did not advertise that capability during initialize"
    )
