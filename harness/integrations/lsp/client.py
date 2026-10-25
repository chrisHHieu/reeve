"""JSON-RPC over stdio — the transport half of the LSP client.

A language server is a long-lived child process spoken to in framed JSON-RPC
(`Content-Length: N\r\n\r\n<json>`), and every consumer of one faces the same four
problems. This module solves exactly those and nothing else:

- **Framing** (`encode_message` / `read_message`): tolerant of extra headers
  (`Content-Type`, `Content-Length` casing) and of bare-LF line endings, because real
  servers differ on both and a strict parser would fail on the first one.
- **Demultiplexing** (`LSPClient._read_stdout`): one background thread owns reading, so
  callers never race each other for the next frame. Responses settle the waiter with
  their id; notifications run handlers on the reader thread. A caller's timeout removes
  only its own waiter — a late response is dropped, never mis-delivered.
- **Lifecycle**: `start()` spawns with pipes; `initialize()` performs the handshake and
  records the advertised capabilities; `shutdown()` is the protocol's graceful route
  (`shutdown` request + `exit` notification) and `close()` the hard stop (terminate,
  then kill after a grace period). EOF or a write failure fails EVERY pending request
  and marks the client dead, so a crashed server surfaces as an error, not a hang.
- **Documents** (`ensure_document`): full-sync (`TextDocumentSyncKind.Full`) — the first
  sight of a URI is `didOpen`, every later call re-sends the whole document as
  `didChange` only when the text's hash actually changed. A call on an unchanged file
  makes no traffic at all.

Positions cross the tool boundary 1-based and become 0-based LSP positions here. The
`character` unit is UTF-16 code units — LSP's default negotiation — which matches every
Python character outside the astral plane; a character past U+FFFF counts as two. That
caveat is documented rather than papered over: a UTF-16 column is the only one a server
is guaranteed to understand, and converting to UTF-32 columns would make our answers
disagree with every other client talking to the same server.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import unquote, urlparse

from harness import __version__
from harness.platform.spawn import spawn

logger = logging.getLogger("harness.integrations.lsp")

#: One request waits this long before giving up. A cold server still indexing answers
#: definition in single-digit seconds; two minutes of silence is a broken server, and
#: the tool's own declared budget must stay below the runtime's deadline ceiling.
DEFAULT_REQUEST_TIMEOUT_MS = 20_000
#: After a request times out, the next requests fail at once for this long instead of
#: each waiting out the full budget. Measured by the 2026-09-25 audit (W3): pyright
#: 1.1.408 left every lookup unanswered, so every `lsp` call cost 20 s and the prompt
#: kept recommending it. A cooldown rather than a verdict: a large repo's first
#: `references` can outrun the budget while indexing, and must get another try.
STALL_COOLDOWN_S = 60.0
#: The handshake gets its own, larger budget: pyright compiles its bundle on first run.
INITIALIZE_TIMEOUT_MS = 60_000
#: `shutdown` is a courtesy; a server that ignores it is still torn down by `close()`.
SHUTDOWN_TIMEOUT_MS = 3_000
#: How long a terminated server may take to actually exit before SIGKILL (or Windows'
#: equivalent). `terminate()` cannot be ignored on Windows — it is already a kill — and
#: on POSIX this bounds a server that catches SIGTERM.
SHUTDOWN_GRACE_S = 2.0
#: How many stderr lines are kept for the failure message. A server that dies after
#: printing a stack trace should say WHY; a server that logs verbosely should not grow
#: this client's memory.
_STDERR_TAIL_LINES = 20

#: Which operation needs which advertised capability, as a path into `initialize`'s
#: result. Anything not listed (diagnostics) is push-model: there is no capability that
#: grants it, so the only gate is a live connection.
_OPERATION_CAPABILITIES: dict[str, tuple[str, ...]] = {
    # Every one of these is a TOP-LEVEL field of `ServerCapabilities` per the LSP spec
    # (pyright confirms: `workspaceSymbolProvider` arrives beside `definitionProvider`,
    # not under `workspace`). A wrong path here silently refuses every call, so each
    # entry names the exact field the spec defines — and the fake server advertises the
    # same shape a real server does, so a mismatch fails tests instead of production.
    "definition": ("definitionProvider",),
    "references": ("referencesProvider",),
    "hover": ("hoverProvider",),
    "document_symbol": ("documentSymbolProvider",),
    "workspace_symbol": ("workspaceSymbolProvider",),
}


class LSPError(Exception):
    """Any LSP failure that a caller should see as a message, not a traceback."""


class LSPProtocolError(LSPError):
    """The server's stream was not valid framed JSON-RPC."""


class LSPTimeout(LSPError):
    """A request did not answer inside its budget."""


class LSPUnavailable(LSPError):
    """The client is dead or closed and cannot carry the request."""


# -- framing --------------------------------------------------------------------


def encode_message(message: dict[str, Any]) -> bytes:
    """One JSON-RPC message in the wire frame every language server expects."""
    body = json.dumps(message, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body


def _read_exactly(stream: Any, length: int) -> Optional[bytes]:
    """Exactly `length` bytes, or None when the stream ends first."""
    chunks: list[bytes] = []
    remaining = length
    while remaining > 0:
        chunk = stream.read(remaining)
        if not chunk:
            return None
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def read_message(stream: Any) -> Optional[dict[str, Any]]:
    """One framed message, or None at a clean end of stream.

    Header parsing is case-insensitive and skips anything it does not understand, so a
    `Content-Type` header or an unusual spelling cannot desynchronize the stream.
    """
    headers: dict[str, str] = {}
    while True:
        raw = stream.readline()
        if not raw:
            return None
        line = raw.decode("ascii", "replace").strip()
        if not line:
            break
        name, separator, value = line.partition(":")
        if separator:
            headers[name.strip().lower()] = value.strip()
    raw_length = headers.get("content-length")
    if raw_length is None:
        raise LSPProtocolError("message had no Content-Length header")
    try:
        length = int(raw_length)
    except ValueError as exc:
        raise LSPProtocolError(f"bad Content-Length {raw_length!r}") from exc
    body = _read_exactly(stream, length)
    if body is None:
        return None
    try:
        message = json.loads(body.decode("utf-8", "replace"))
    except ValueError as exc:
        raise LSPProtocolError(f"message body was not JSON: {exc}") from exc
    if not isinstance(message, dict):
        raise LSPProtocolError("message body was not a JSON object")
    return message


# -- URIs and positions ----------------------------------------------------------


def path_to_uri(path: str | Path) -> str:
    """`pathlib`'s `as_uri`, resolved first so `file:///D:/x` is stable on Windows."""
    return Path(path).resolve().as_uri()


def uri_to_path(uri: str) -> str:
    """The local path a `file:` URI names; the URI itself when it names something else.

    Handles the Windows shapes a server sends back: `file:///C:/x` → `C:\\x` and a UNC
    `file://server/share/x` → `\\\\server\\share\\x`. A URI that is not `file:` (an
    `untitled:` buffer, a virtual document) has no local path to read, so it is returned
    verbatim and callers keep it as a display string.
    """
    parsed = urlparse(str(uri or ""))
    if parsed.scheme != "file":
        return str(uri or "")
    path = unquote(parsed.path or "")
    if parsed.netloc:
        path = f"//{parsed.netloc}{path}"
    if os.name == "nt":
        if len(path) > 2 and path[0] == "/" and path[2] == ":":
            path = path[1:]
        path = path.replace("/", "\\")
    return path


def uri_key(uri: str) -> str:
    """A comparable key for a `file:` URI — the PATH, normcased.

    Servers spell the same file differently: pyright answers with the drive colon
    percent-encoded and lowercased (`file:///c%3A/...`) while we sent `file:///C:/...`.
    Keying diagnostics on the server's chosen spelling made every publish land under a
    key nobody read. Non-file URIs (virtual documents) have no path to normalize and
    keep their verbatim form.
    """
    parsed = urlparse(str(uri or ""))
    if parsed.scheme != "file":
        return str(uri or "")
    path = unquote(parsed.path or "")
    if parsed.netloc:
        path = f"//{parsed.netloc}{path}"
    return os.path.normcase(path)


def to_lsp_position(line: int, character: int) -> dict[str, int]:
    """1-based tool coordinates into 0-based LSP positions.

    Clamped at zero rather than raising: a caller that passes a position before the
    start of the file gets the file's start, which is what the model meant.
    """
    return {"line": max(0, int(line) - 1), "character": max(0, int(character) - 1)}


# -- client ----------------------------------------------------------------------


class LSPClient:
    """One language-server subprocess. Safe for concurrent tool worker threads."""

    def __init__(
        self,
        command: list[str],
        workspace: str | Path,
        *,
        language: str = "",
        language_id: str = "",
        roots: Optional[list[str | Path]] = None,
        request_timeout_ms: int = DEFAULT_REQUEST_TIMEOUT_MS,
        initialize_timeout_ms: int = INITIALIZE_TIMEOUT_MS,
        initialization_options: Optional[dict] = None,
        settings: Optional[dict] = None,
    ) -> None:
        self.command = [str(part) for part in command]
        self.workspace = Path(workspace).expanduser().resolve()
        self.language = language
        self.language_id = language_id or language
        self.roots: list[Path] = []
        for root in roots or [self.workspace]:
            resolved = Path(root).expanduser().resolve()
            if resolved not in self.roots:
                self.roots.append(resolved)
        self._request_timeout_ms = request_timeout_ms
        self._initialize_timeout_ms = initialize_timeout_ms
        #: `time.monotonic()` until which requests fail fast (see STALL_COOLDOWN_S).
        self._stalled_until = 0.0
        #: Handshake options a server requires (`typescript-language-server` reads its
        #: `tsserver.path` here — the CLI flag for it was removed in 6.0).
        self._initialization_options = dict(initialization_options or {})
        #: What `workspace/configuration` answers, by dotted section (pyright's
        #: `python.pythonPath`, python_env.py). Unknown sections get an empty object.
        self._settings = dict(settings or {})

        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.RLock()
        self._next_id = 0
        self._pending: dict[int, dict[str, Any]] = {}
        self._handlers: dict[str, Callable[[Any], None]] = {}
        self._capabilities: dict[str, Any] = {}
        self.server_info: dict[str, Any] = {}
        self._dead_reason = ""
        self._closed = False
        self._stderr_tail: deque[str] = deque(maxlen=_STDERR_TAIL_LINES)

        # Diagnostics are PUSHED, not requested. The latest set per URI is kept here and
        # every publish wakes `wait_for_diagnostics` — a condition variable would be the
        # same thing with more ceremony.
        self._diagnostics: dict[str, list[dict[str, Any]]] = {}
        self._diag_published_at: dict[str, float] = {}
        self._diag_changed = threading.Event()

        # Documents this client has opened, by URI -> hash of the text last sent. The
        # hash, not the text: it is compared on every call and a full document copy per
        # open document would be wasted memory.
        self._doc_hashes: dict[str, str] = {}
        self._doc_versions: dict[str, int] = {}
        #: When this URI was last SENT to the server (didOpen/didChange). A publish that
        #: predates it describes text the server no longer holds — returning it would serve
        #: the caller a previous edit's diagnostics as if they were current.
        self._doc_sent_at: dict[str, float] = {}

    # -- process -----------------------------------------------------------------

    @property
    def alive(self) -> bool:
        proc = self._proc
        return proc is not None and proc.poll() is None

    @property
    def returncode(self) -> Optional[int]:
        """The exit status once the process is gone; None while it lives or never ran."""
        proc = self._proc
        return proc.returncode if proc is not None else None

    def start(self) -> None:
        if self._proc is not None:
            return
        command = list(self.command)
        if os.name == "nt" and command and command[0].lower().endswith((".cmd", ".bat")):
            # npm and pip install launchers as `.cmd`/`.bat` shims, which CreateProcess
            # does not execute directly; cmd.exe is the documented way to run them.
            command = ["cmd.exe", "/c", *command]
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        try:
            self._proc = spawn(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(self.workspace),
                creationflags=creationflags,
            )
        except OSError as exc:
            raise LSPError(
                f"could not start the {self.language or 'language'} server "
                f"{command[0]!r}: {exc}"
            ) from exc
        threading.Thread(
            target=self._read_stdout, name="lsp-stdout", daemon=True
        ).start()
        # stderr is drained, not shown: a server that writes a warning per file would
        # otherwise fill the pipe and block on its next write. The tail is kept only so a
        # crash can name its reason.
        threading.Thread(target=self._read_stderr, name="lsp-stderr", daemon=True).start()

    def _crash_reason(self, message: str) -> str:
        tail = "".join(self._stderr_tail).strip()
        return f"{message}\nserver stderr:\n{tail}" if tail else message

    def _read_stderr(self) -> None:
        proc = self._proc
        stream = proc.stderr if proc is not None else None
        if stream is None:
            return
        try:
            while True:
                raw = stream.readline()
                if not raw:
                    return
                self._stderr_tail.append(raw.decode("utf-8", "replace"))
        except (OSError, ValueError):
            return

    def _read_stdout(self) -> None:
        proc = self._proc
        stream = proc.stdout if proc is not None else None
        if stream is None:
            return
        reason = ""
        try:
            while True:
                message = read_message(stream)
                if message is None:
                    reason = "the language server closed its output"
                    return
                self._dispatch(message)
        except LSPProtocolError as exc:
            reason = f"the language server sent a malformed message: {exc}"
        except (OSError, ValueError) as exc:
            reason = f"could not read from the language server: {exc}"
        finally:
            self._fail_all(self._crash_reason(reason or "the language server stopped"))

    # -- dispatch ------------------------------------------------------------------

    def _dispatch(self, message: dict[str, Any]) -> None:
        if "id" in message and ("result" in message or "error" in message):
            self._settle(message)
            return
        method = message.get("method")
        if not isinstance(method, str):
            return
        params = message.get("params")
        if "id" in message:
            self._answer(message.get("id"), method, params)
            return
        self._notify(method, params)

    def _settle(self, message: dict[str, Any]) -> None:
        with self._lock:
            waiter = self._pending.pop(message.get("id"), None)
        if waiter is None:
            return  # a response to an already-timed-out request; nothing waits on it
        waiter["result"] = message.get("result")
        waiter["error"] = message.get("error")
        waiter["event"].set()

    def _answer(self, request_id: Any, method: str, params: Any) -> None:
        """Reply to a server → client request so the server never waits forever.

        `workspace/configuration` gets, per requested item, the settings under its section
        (the interpreter pyright needs, python_env.py) or an empty object: the LSP default
        is `null`, but an empty object is the shape servers actually index into, and
        refusing to answer would park initialization.
        """
        if method == "workspace/configuration":
            items = params.get("items") if isinstance(params, dict) else None
            result: Any = [self._section(item) for item in items] if isinstance(items, list) else []
        elif method == "workspace/workspaceFolders":
            result = [
                {"uri": path_to_uri(root), "name": root.name or str(root)}
                for root in self.roots
            ]
        else:
            result = None
        try:
            self._send({"jsonrpc": "2.0", "id": request_id, "result": result})
        except LSPUnavailable:
            pass

    def _section(self, item: Any) -> dict:
        node: Any = self._settings
        section = item.get("section") if isinstance(item, dict) else None
        for part in str(section).split(".") if section else []:
            node = node.get(part) if isinstance(node, dict) else None
        return dict(node) if isinstance(node, dict) else {}

    def _notify(self, method: str, params: Any) -> None:
        if method == "textDocument/publishDiagnostics":
            self._publish_diagnostics(params)
        handler = self._handlers.get(method)
        if handler is None:
            return
        try:
            handler(params)
        except Exception:
            logger.warning("lsp: handler for %s raised", method, exc_info=True)

    def _publish_diagnostics(self, params: Any) -> None:
        if not isinstance(params, dict):
            return
        uri = params.get("uri")
        diagnostics = params.get("diagnostics")
        if not isinstance(uri, str) or not isinstance(diagnostics, list):
            return
        key = uri_key(uri)
        with self._lock:
            self._diagnostics[key] = [d for d in diagnostics if isinstance(d, dict)]
            self._diag_published_at[key] = time.monotonic()
        self._diag_changed.set()

    # -- wire ----------------------------------------------------------------------

    def _send(self, message: dict[str, Any]) -> None:
        with self._lock:
            proc = self._proc
            if self._closed:
                raise LSPUnavailable(f"the {self.language or 'language'} server was closed")
            if proc is None or proc.poll() is not None:
                raise LSPUnavailable(
                    self._dead_reason
                    or self._crash_reason("the language server is not running")
                )
            try:
                assert proc.stdin is not None
                proc.stdin.write(encode_message(message))
                proc.stdin.flush()
            except (OSError, ValueError) as exc:
                raise LSPUnavailable(
                    self._crash_reason(f"could not write to the language server: {exc}")
                ) from exc

    def notify(self, method: str, params: Any = None) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def request(
        self,
        method: str,
        params: Any = None,
        *,
        timeout_ms: Optional[int] = None,
    ) -> Any:
        """Send a request and block the CALLING thread until its own response arrives.

        The waiter is registered before the write, so a response cannot arrive between
        the two and be dropped. On timeout only this request's waiter is removed; the id
        is never reused, so a late response is matched to nothing and ignored.
        """
        lookup = method not in ("initialize", "shutdown")
        if lookup and time.monotonic() < self._stalled_until:
            raise LSPTimeout(
                f"{method} skipped: the {self.language or 'language'} server stopped "
                "answering a moment ago; use grep and read_file for now"
            )
        with self._lock:
            if self._closed:
                raise LSPUnavailable(f"the {self.language or 'language'} server was closed")
            self._next_id += 1
            request_id = self._next_id
            waiter: dict[str, Any] = {
                "event": threading.Event(),
                "result": None,
                "error": None,
            }
            self._pending[request_id] = waiter
            try:
                self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
            except Exception:
                self._pending.pop(request_id, None)
                raise
        budget_ms = timeout_ms or self._request_timeout_ms
        if not waiter["event"].wait(budget_ms / 1000):
            with self._lock:
                self._pending.pop(request_id, None)
            if lookup:
                self._stalled_until = time.monotonic() + STALL_COOLDOWN_S
            raise LSPTimeout(
                f"{method} timed out after {budget_ms} ms; the language server did not "
                f"answer. Further lsp calls fail at once for {STALL_COOLDOWN_S:.0f} s — "
                "use grep and read_file meanwhile"
            )
        error = waiter["error"]
        if error is not None:
            if isinstance(error, dict):
                text = str(error.get("message") or error)
                code = error.get("code")
                if code is not None:
                    text = f"{text} (code {code})"
            else:
                text = str(error)
            raise LSPError(f"{method} failed: {text}")
        return waiter["result"]

    # -- lifecycle -------------------------------------------------------------------

    def initialize(self) -> dict[str, Any]:
        """Handshake, then `initialized`; returns the advertised capabilities."""
        folders = [
            {"uri": path_to_uri(root), "name": root.name or str(root)}
            for root in self.roots
        ]
        params = {
            "processId": os.getpid(),
            "clientInfo": {"name": "reeve", "version": __version__},
            "rootUri": path_to_uri(self.workspace),
            "rootPath": str(self.workspace),
            "workspaceFolders": folders,
            "capabilities": {
                "workspace": {
                    "workspaceFolders": True,
                    "symbol": {"symbolKind": {"valueSet": list(range(1, 27))}},
                    # Declared so a server MAY register watchers; the notifications our
                    # write tools emit are accepted regardless (pyright honours them
                    # without registering anything — measured 2026-09-20).
                    "didChangeWatchedFiles": {"dynamicRegistration": True},
                    # Without it pyright never asks for its settings, and the project's
                    # interpreter (python_env.py) is never used — the first live check of
                    # that change still read "sqlalchemy could not be resolved".
                    **({"configuration": True} if self._settings else {}),
                },
                "textDocument": {
                    "synchronization": {
                        "dynamicRegistration": False,
                        "willSave": False,
                        "didSave": False,
                    },
                    "definition": {"linkSupport": True},
                    "references": {},
                    "hover": {"contentFormat": ["markdown", "plaintext"]},
                    "documentSymbol": {"hierarchicalDocumentSymbolSupport": True},
                    "publishDiagnostics": {"relatedInformation": True},
                },
                "window": {"workDoneProgress": True},
            },
            "trace": "off",
            **(
                {"initializationOptions": self._initialization_options}
                if self._initialization_options
                else {}
            ),
        }
        result = self.request("initialize", params, timeout_ms=self._initialize_timeout_ms)
        capabilities = result.get("capabilities") if isinstance(result, dict) else None
        self._capabilities = capabilities if isinstance(capabilities, dict) else {}
        info = result.get("serverInfo") if isinstance(result, dict) else None
        self.server_info = info if isinstance(info, dict) else {}
        self.notify("initialized", {})
        return self._capabilities

    def capability(self, *path: str) -> Any:
        node: Any = self._capabilities
        for key in path:
            if not isinstance(node, dict):
                return None
            node = node.get(key)
        return node

    def supports(self, operation: str) -> bool:
        keys = _OPERATION_CAPABILITIES.get(operation)
        return True if keys is None else bool(self.capability(*keys))

    def shutdown(self, *, timeout_ms: int = SHUTDOWN_TIMEOUT_MS) -> None:
        """The protocol's graceful stop, then `close()` regardless of the outcome."""
        if self._proc is None:
            return
        if self.alive and not self._closed and not self._dead_reason:
            try:
                self.request("shutdown", None, timeout_ms=timeout_ms)
            except LSPError:
                pass
            try:
                self.notify("exit", None)
            except LSPUnavailable:
                pass
        self.close()

    def close(self) -> None:
        """Hard stop: fail every waiter, terminate, force after the grace period.

        Idempotent by design — this runs on process teardown, where an exception would
        be swallowed and leave an orphan language server behind.
        """
        proc = self._proc
        if proc is None:
            return
        with self._lock:
            self._closed = True
        self._fail_all("the language server was closed")
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=SHUTDOWN_GRACE_S)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=SHUTDOWN_GRACE_S)
                except subprocess.TimeoutExpired:
                    logger.warning(
                        "lsp: %s server pid %s did not exit", self.language or "", proc.pid
                    )
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            try:
                if stream is not None:
                    stream.close()
            except (OSError, ValueError):
                pass

    def _fail_all(self, reason: str) -> None:
        with self._lock:
            if reason:
                self._dead_reason = self._dead_reason or reason
            reason = self._dead_reason or reason
            pending = list(self._pending.values())
            self._pending.clear()
        for waiter in pending:
            if waiter["error"] is None and waiter["result"] is None:
                waiter["error"] = reason
            waiter["event"].set()
        # Wake a diagnostics waiter: a dead server cannot publish again.
        self._diag_changed.set()

    # -- documents -------------------------------------------------------------------

    def ensure_document(self, path: str | Path, language_id: str = "") -> str:
        """Make the server hold this file's CURRENT text; returns the document URI.

        Full sync: `didOpen` the first time a URI is seen, `didChange` with the whole
        document whenever the on-disk text's hash differs from what was last sent. An
        unchanged file sends nothing — the common case of two asks about one file in a
        turn must not cost a full document re-upload.
        """
        target = Path(path)
        uri = path_to_uri(target)
        try:
            text = target.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise LSPError(f"could not read {target}: {exc}") from exc
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        with self._lock:
            if self._doc_hashes.get(uri) == digest:
                return uri
            version = self._doc_versions.get(uri, 0) + 1
            self._doc_versions[uri] = version
            # Stamped BEFORE the notify: a fast server may publish in response while this
            # thread is still inside `notify`, and the stamp must not postdate the answer.
            # Keyed like the publishes (`uri_key`), so the wait finds it.
            self._doc_sent_at[uri_key(uri)] = time.monotonic()
            if uri in self._doc_hashes:
                self.notify(
                    "textDocument/didChange",
                    {
                        "textDocument": {"uri": uri, "version": version},
                        "contentChanges": [{"text": text}],
                    },
                )
            else:
                self.notify(
                    "textDocument/didOpen",
                    {
                        "textDocument": {
                            "uri": uri,
                            "languageId": language_id or self.language_id or self.language,
                            "version": version,
                            "text": text,
                        }
                    },
                )
            self._doc_hashes[uri] = digest
        return uri

    def notify_files_changed(self, changes: list[tuple[str | Path, int]]) -> None:
        """Tell the server about files that changed ON DISK and are not otherwise open.

        The client is the only one who can: pyright, measured 2026-09-20, does NOT notice
        a new `pyrightconfig.json` on its own — start it without one and it reports every
        third-party import unresolved for the rest of the session, through diagnostics and
        hover alike, until a `workspace/didChangeWatchedFiles` arrives. `type` is the LSP
        `FileChangeType` (1 created, 2 changed, 3 deleted); pyright re-reads its config on
        either of the first two.
        """
        if not changes or self._closed or self._dead_reason:
            return
        try:
            self.notify(
                "workspace/didChangeWatchedFiles",
                {
                    "changes": [
                        {"uri": path_to_uri(Path(path)), "type": int(kind)}
                        for path, kind in changes
                    ]
                },
            )
        except LSPUnavailable:
            pass  # a dead server has no view left to correct

    def did_close(self, uri: str) -> None:
        with self._lock:
            self._doc_hashes.pop(uri, None)
        try:
            self.notify("textDocument/didClose", {"textDocument": {"uri": uri}})
        except LSPUnavailable:
            pass  # closing a document on a dead server has nothing left to tell it

    def sync_workspace_folders(self, roots: list[str | Path]) -> None:
        """Tell the server about roots it has not seen (multi-root sessions).

        Add-only by design: sessions sharing a workspace have different scratch roots,
        and removing one session's folder whenever another session runs would flap the
        server for no benefit. Only sent when the server advertised workspace folders.
        """
        unique: list[Path] = []
        for root in roots or []:
            resolved = Path(root).expanduser().resolve()
            if resolved not in unique:
                unique.append(resolved)
        added = [root for root in unique if root not in self.roots]
        if not added:
            return
        self.roots.extend(added)
        if not self.capability("workspace", "workspaceFolders"):
            return  # remembered for our own `workspace/workspaceFolders` reply
        try:
            self.notify(
                "workspace/didChangeWorkspaceFolders",
                {
                    "event": {
                        "added": [
                            {"uri": path_to_uri(root), "name": root.name or str(root)}
                            for root in added
                        ],
                        # Removals are never sent — see the docstring.
                        "removed": [],
                    }
                },
            )
        except LSPUnavailable:
            pass

    # -- diagnostics -----------------------------------------------------------------

    def diagnostics(self, uri: str) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._diagnostics.get(uri_key(uri), ()))

    def wait_for_diagnostics(
        self,
        uri: str,
        *,
        settle_ms: int = 400,
        timeout_ms: int = 8_000,
    ) -> list[dict[str, Any]]:
        """The diagnostics for `uri` once the server stops revising them.

        A push-model server has no "done" signal: a syntax pass may arrive first and the
        type pass a moment later. So "settled" is defined as no publish for this URI for
        `settle_ms`, and the timeout bounds a server that never publishes at all. The
        wait is event-driven — the reader thread sets the event on each publish — so no
        polling loop ever spins.

        A publish only counts when it is NEWER than the last text this client sent for the
        URI (`_doc_sent_at`): after a didChange, the previous publish describes a file the
        server no longer holds, and returning it immediately (the old behaviour, measured
        2026-09-19: a `pyrightconfig.json` written beside an open file left diagnostics
        stale for the whole session) served last edit's errors as if they were current.

        The timeouts are per-URI (`_diag_published_at`): a chatty server publishing other
        files must not keep this wait alive to the deadline.
        """
        deadline = time.monotonic() + max(0, timeout_ms) / 1000
        settle_s = max(0, settle_ms) / 1000
        key = uri_key(uri)
        with self._lock:
            required = self._doc_sent_at.get(key, 0.0)
        while True:
            now = time.monotonic()
            with self._lock:
                published_at = self._diag_published_at.get(key, 0.0)
            if published_at and published_at >= required and now - published_at >= settle_s:
                break
            remaining = deadline - now
            if remaining <= 0:
                break
            self._diag_changed.wait(min(max(settle_s, 0.05), remaining))
            self._diag_changed.clear()
            if self._closed or self._dead_reason:
                break  # a dead server cannot publish again; return what it published
        return self.diagnostics(uri)

    def on_notification(self, method: str, handler: Callable[[Any], None]) -> None:
        """Register a handler for a server notification (runs on the reader thread)."""
        self._handlers[method] = handler
