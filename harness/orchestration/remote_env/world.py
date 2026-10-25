"""``RemoteWorld`` — the ADR-0003 World protocol over SSH.

One remote workspace, one SSH session, three answers:

- ``executor`` → ``RemoteExecutor`` (see ``executor.py``).
- ``fs_tools()`` → the remote file closures (see ``files.py``), mirroring
  ``LocalWorld.fs_tools`` names/signatures/refusals.
- ``terminals`` → ``RemoteTerminals`` below.

The World contracts hold: executor, terminals and file tools all read the same
``RemoteSession`` and root, and policy stays outside — nothing here decides what the
agent *may* do, only how it reaches the host.

**Deliberate phase-2a limitation (stated, not hidden): there is no persistent remote
PTY.** ``RemoteTerminals`` presents the SAME surface as ``TerminalSessions`` (open /
get / list / close, sessions with send / read / write / interrupt / close) but each
``send`` runs one whole command through its terminal's own ``RemoteExecutor``. State
that a live shell would hold between sends — exported variables, an activated venv, a
REPL's in-memory context, a running process — does not survive, so ``write`` (raw
keystrokes) and ``interrupt`` (killing a running foreground process) are answered with
an explicit message rather than faked. The working directory IS tracked per terminal,
because the executor's frame reports ``$PWD``. The persistent PTY-backed terminal and
the GUI's environment pick are phase 2b-2; session binding and read/search/git/observed
tool routing shipped in phase 2b-1 (see ``read_backend``).
"""

from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Optional

from harness.tools.terminal import DEFAULT_READ_COUNT, DEFAULT_SEND_TIMEOUT, IDLE_SECONDS, MAX_SESSIONS, _Buffer, page_lines
from .executor import RemoteExecutor
from .files import RemoteFileBackend, _canonical, remote_file_tools
from .transport import RemoteSession

_TERMINAL_LIMITATION = (
    "this remote terminal runs one whole command per send; a persistent PTY-backed "
    "session (raw input, interrupt of a running process, state between sends) ships "
    "later. Nothing was sent."
)


class RemoteWorld:
    """The world made of one SSH host: remote fs closures, remote shell, remote terminals.

    ``root`` is a remote POSIX path (a plain string — the controller may be Windows and
    must never run it through local path semantics); it is both the relative-path anchor
    for the file tools and the working directory of the remote shell.
    """

    #: The catalog and the engine's local-path bookkeeping ask this to tell a world made
    #: of local disks from one made of SSH — local paths must never be resolved for this.
    is_remote = True

    def __init__(
        self,
        session: RemoteSession,
        workspace: str,
        *,
        spill: Optional[Any] = None,
        session_id: str = "",
        executor: Optional[RemoteExecutor] = None,
        terminals: Optional[Any] = None,
    ) -> None:
        self.session = session
        self.root = _canonical(workspace)
        self.workspace = self.root
        self.session_id = session_id or uuid.uuid4().hex[:12]
        self._spill = spill
        self._executor = executor
        self._terminals = terminals
        self._read_backend: Optional[RemoteFileBackend] = None
        self._read_backend_roots: Optional[list] = None

    @property
    def spill(self) -> Optional[Any]:
        """The session's spill store, shared by the executor and every terminal."""
        return self._spill

    @property
    def executor(self) -> RemoteExecutor:
        if self._executor is None:
            self._executor = RemoteExecutor(
                self.session,
                cwd=self.root,
                workspace=self.root,
                session_id=self.session_id,
                spill=self._spill,
            )
        return self._executor

    @property
    def terminals(self) -> "RemoteTerminals":
        if self._terminals is None:
            self._terminals = RemoteTerminals(self)
        return self._terminals

    def close_terminals(self) -> None:
        """Dispose this world's terminals with their owner (only if one was ever built)."""
        if self._terminals is not None:
            self._terminals.close_all()

    def fs_tools(self, roots: Optional[list] = None) -> list:
        """The remote file closures over this world's storage. ``roots`` (a shared,
        possibly-mutable list of remote paths) extends the reach exactly as the catalog
        passes it today; without it the remote workspace is the single writable root."""
        return remote_file_tools(self.read_backend(roots))

    def read_backend(self, roots: Optional[list] = None) -> RemoteFileBackend:
        """The RemoteFileBackend the READ-side tools (our windowed read, grep, image,
        observed-file bookkeeping) share with ``fs_tools``.

        Cached per roots-list identity: the catalog passes the engine's one shared list,
        and the manager's observed-files checker passes the same object, so one SSH
        backend serves the whole session instead of one per call.
        """
        if self._read_backend is None or self._read_backend_roots is not roots:
            self._read_backend = RemoteFileBackend(self.session, self.root, roots=roots)
            self._read_backend_roots = roots
        return self._read_backend


class RemoteTerminal:
    """One named remote terminal: buffered output, per-terminal remote cwd, one command
    per send. Not a live shell — see the module docstring for the exact limitation."""

    def __init__(
        self,
        *,
        session_id: str,
        name: str,
        executor: RemoteExecutor,
    ) -> None:
        self.id = session_id
        self.name = name or session_id
        self.executor = executor
        self.cwd = executor.cwd
        self.opened_at = time.time()
        self._buffer = _Buffer()
        self._closed = False

    @property
    def running(self) -> bool:
        return not self._closed

    def status(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "cwd": self.cwd,
            "shell": "ssh",
            "type": "shell",
            "running": self.running,
            "opened_at": round(self.opened_at, 3),
        }

    def send(
        self,
        text: str,
        *,
        enter: bool = True,
        timeout: float = DEFAULT_SEND_TIMEOUT,
        idle: float = IDLE_SECONDS,
    ) -> dict[str, Any]:
        """Run one command remotely and report why the wait ended.

        ``idle`` means the command finished and printed something; ``timeout`` means the
        remote budget expired; ``exited`` means no exit status came back at all.
        """
        if self._closed:
            return {"error": "terminal has been closed", "running": False}
        if not enter:
            return {"error": _TERMINAL_LIMITATION, "running": True}
        result = self.executor.run(text, timeout=float(timeout or DEFAULT_SEND_TIMEOUT))
        output = str(result.get("output") or "")
        if output:
            self._buffer.append(output)
        if result.get("cwd"):
            self.cwd = str(result["cwd"])
        out, lost = self._buffer.drain()
        code = result.get("exit_code")
        reason = (
            "timeout"
            if result.get("timed_out")
            else "idle"
            if isinstance(code, int)
            else "exited"
        )
        payload: dict[str, Any] = {
            "id": self.id,
            "output": out,
            "reason": reason,
            "running": True,
        }
        if isinstance(code, int):
            payload["exit_code"] = code
        if lost:
            payload["dropped_chars"] = lost
        if result.get("error"):
            payload["error"] = result["error"]
        return payload

    def read(self, *, offset: int = 0, count: int = DEFAULT_READ_COUNT) -> dict[str, Any]:
        """The same newest-relative page contract as the local terminal (`page_lines`)."""
        text = self._buffer.snapshot()
        page, begin, end, total = page_lines(text, offset, count)
        payload: dict[str, Any] = {
            "id": self.id,
            "type": "shell",
            "output": page,
            "reason": "read",
            "running": self.running,
            "total_lines": total,
            "line_begin": begin,
            "line_end": end,
        }
        lost = self._buffer.produced - len(text)
        if lost:
            payload["dropped_chars"] = lost
        return payload

    def write(self, text: str) -> dict[str, Any]:
        return {"error": _TERMINAL_LIMITATION, "running": self.running}

    def interrupt(self) -> dict[str, Any]:
        return {"error": _TERMINAL_LIMITATION, "running": self.running}

    def signal(self, name: str) -> dict[str, Any]:
        """One command per send means there is no live foreground group to signal: the
        honest answer is the same limitation `interrupt` gives, not a guessed kill."""
        return {"error": _TERMINAL_LIMITATION, "running": self.running}

    def close(self) -> dict[str, Any]:
        self._closed = True
        return {"id": self.id, "closed": True}

    def snapshot(self) -> dict[str, Any]:
        text, _start, produced = self._buffer.snapshot_with_cursor()
        return {**self.status(), "output": text, "produced": produced}


class RemoteTerminals:
    """Every remote terminal one world owns — the surface ``terminal_tools`` expects."""

    def __init__(self, world: RemoteWorld) -> None:
        self._world = world
        self._sessions: dict[str, RemoteTerminal] = {}
        self._lock = threading.Lock()

    def open(
        self, *, name: str = "", cwd: Optional[str] = None, type: str = "shell"
    ) -> dict[str, Any]:
        kind = str(type or "shell").strip().lower()
        if kind == "pipe":
            kind = "shell"
        if kind != "shell":
            return {
                "error": (
                    "remote terminals do not support type='pty' yet: a persistent"
                    "  PTY on the remote host ships later. Open a "
                    "type='shell' terminal instead."
                )
            }
        with self._lock:
            if len(self._sessions) >= MAX_SESSIONS:
                return {
                    "error": (
                        f"already holding {len(self._sessions)} terminals (the limit is "
                        f"{MAX_SESSIONS}) — close one before opening another"
                    ),
                    "terminals": [s.status() for s in self._sessions.values()],
                }
            session_id = f"term-{uuid.uuid4().hex[:8]}"
            # Each terminal gets its OWN executor: its tracked cwd must not move when
            # another terminal cd's, and the scratch session id stays distinguishable.
            executor = RemoteExecutor(
                self._world.session,
                cwd=cwd or self._world.root,
                workspace=self._world.root,
                session_id=f"{self._world.session_id}:{session_id}",
                spill=self._world.spill,
            )
            terminal = RemoteTerminal(
                session_id=session_id, name=name, executor=executor
            )
            self._sessions[session_id] = terminal
        return terminal.status()

    def get(self, session_id: str) -> Optional[RemoteTerminal]:
        with self._lock:
            return self._sessions.get(session_id)

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return [s.status() for s in self._sessions.values()]

    def close(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            session = self._sessions.pop(session_id, None)
        if session is None:
            return {"error": f"no terminal {session_id!r}"}
        return session.close()

    def close_all(self) -> None:
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            session.close()


__all__ = ["RemoteTerminal", "RemoteTerminals", "RemoteWorld"]
