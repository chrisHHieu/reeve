"""UserTerminalMixin — the person's own terminals, beside the agent's.

`harness/tools/terminal.py` is the AGENT's persistent shell: its `read()` drains a
cursor, so two readers would steal each other's output. The terminal panel (`/terminal`)
therefore gets terminals of its OWN per session — real PTYs (`harness/tools/pty_session.py`,
ConPTY on Windows / pty on POSIX) that nothing but the panel reads. That is the honest
design: a separate terminal is what makes the two uses incapable of interfering, rather
than a promise about how they share one.

One session may hold several terminals: the client
mints a small `terminal_id` ("t1", "t2", …) and everything — the shell, its subscribers,
its scrollback — is keyed by `(session_id, terminal_id)`.

Live output is a fan-out of the terminal's `on_output` callback (fired on the reader
thread) to per-connection subscribers. Chunks carry their absolute `[start, end)`
positions so a subscriber that raced its attach can drop what its snapshot already
covered; the subscription set is guarded by the same lock that guards attach, so no chunk
can be lost between the two.
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Any, Callable, Optional

from harness.tools.pty_session import PtySession

#: A subscriber is `(chunk, start, end)` — called on the terminal's reader thread.
OutputSubscriber = Callable[[str, int, int], None]

#: The panel's launchable shells — the guide menu's catalogue (`/v1/sessions/{id}/terminal/
#: shells`), discovered at launch. `id` is the wire value the GUI
#: remembers, `name` is what the menu shows and `path` is what `PtySession` spawns. Only
#: what this machine can actually run is offered (probed with `shutil.which`).
_SHELL_CHOICES_WINDOWS: tuple[tuple[str, str], ...] = (
    ("cmd", "cmd.exe"),
    ("bash", "bash"),
    ("powershell", "powershell.exe"),
)
_SHELL_CHOICES_POSIX: tuple[tuple[str, str], ...] = (
    ("bash", "/bin/bash"),
    ("sh", "/bin/sh"),
)

#: One client-minted terminal id; the GUI uses small opaque ones ("t1", "t2", …).
_TERMINAL_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


def _shell_choices() -> tuple[tuple[str, str], ...]:
    return _SHELL_CHOICES_WINDOWS if os.name == "nt" else _SHELL_CHOICES_POSIX


def _resolve_shell(shell_id: str) -> Optional[str]:
    """The executable an id names, or None when this machine does not have it."""
    for candidate_id, path in _shell_choices():
        if candidate_id == shell_id:
            return shutil.which(path)
    return None


def _same_shell(current: Any, requested: str) -> bool:
    """Whether a running terminal is already the requested interpreter. A terminal that
    cannot say what it is (test doubles, future backends) is kept rather than churned."""
    existing = getattr(current, "shell_path", None)
    if not existing:
        return True
    return Path(str(existing)).name.lower() == Path(requested).name.lower()


class UserTerminalMixin:
    # -- lifecycle -----------------------------------------------------------------

    @staticmethod
    def _terminal_key(session_id: str, terminal_id: str) -> tuple[str, str]:
        return (session_id, terminal_id or "1")

    def user_terminal(
        self,
        session_id: str,
        *,
        cwd: Optional[str] = None,
        shell: Optional[str] = None,
        terminal_id: str = "1",
        cols: Optional[int] = None,
        rows: Optional[int] = None,
    ) -> dict[str, Any]:
        """Open or attach one of the session's terminals; return status + scrollback.

        Idempotent per (terminal, interpreter): the second call attaches to the live
        terminal. An EXITED terminal is replaced, so reopening after a crash starts fresh
        rather than reporting a dead panel. `shell` names one of `user_terminal_shells`
        (the guide menu's pick): asking for a DIFFERENT interpreter ends the old process
        first — a tab is one terminal, never two wearing one name.
        """
        if not self._SESSION_ID_RE.match(session_id or "") or session_id in {".", ".."}:
            return {"ok": False, "error": "invalid session id"}
        if not _TERMINAL_ID_RE.match(terminal_id or ""):
            return {"ok": False, "error": "invalid terminal id"}
        record = self.session_store.load(session_id)
        if record is not None and record.environment_id:
            # A local terminal under a remote session is two worlds wearing one name.
            # Refuse until the remote terminal lands rather than pretend.
            return {
                "ok": False,
                "error": (
                    "this session works on a remote host; the local terminal panel "
                    "cannot attach to it yet"
                ),
            }
        requested: Optional[str] = None
        if shell:
            requested = _resolve_shell(shell)
            if requested is None:
                return {"ok": False, "error": f"shell not available on this machine: {shell}"}
        key = self._terminal_key(session_id, terminal_id)
        replaced: Any = None
        with self._user_shell_lock:
            current = self._user_shells.get(key)
            if current is not None and not current.running:
                self._user_shells.pop(key, None)
                current = None
            if current is not None and requested is not None and not _same_shell(current, requested):
                self._user_shells.pop(key, None)
                self._user_shell_subs.pop(key, None)
                replaced = current
                current = None
            if current is None:
                target = cwd or self.engine_workspace(session_id) or self.default_workspace
                if not target or not Path(str(target)).is_dir():
                    target = self._provision_scratch(session_id)
                fanout = lambda chunk, start, end, _key=key: self._user_terminal_output(
                    _key, chunk, start, end
                )
                try:
                    current = PtySession(
                        session_id=f"user-{session_id}-{terminal_id}",
                        cwd=target,
                        name="user terminal",
                        on_output=fanout,
                        marks=True,
                        **({} if cols is None else {"cols": cols}),
                        **({} if rows is None else {"rows": rows}),
                        **({"shell_path": requested} if requested else {}),
                    )
                except OSError as exc:
                    return {"ok": False, "error": f"could not start a shell: {exc}"}
                self._user_shells[key] = current
            result = current
        # Closing the replaced terminal waits for its process; do it outside the lock so
        # other terminals are not held up behind a slow terminate.
        if replaced is not None:
            replaced.close()
        return {"ok": True, **result.snapshot()}

    def user_terminal_shells(self, session_id: str) -> dict[str, Any]:
        """The shells this machine can launch for the panel's menu."""
        if not self._SESSION_ID_RE.match(session_id or "") or session_id in {".", ".."}:
            return {"ok": False, "error": "invalid session id"}
        record = self.session_store.load(session_id)
        if record is not None and record.environment_id:
            return {
                "ok": False,
                "error": (
                    "this session works on a remote host; the local terminal panel "
                    "cannot attach to it yet"
                ),
            }
        shells: list[dict[str, str]] = []
        for shell_id, path in _shell_choices():
            resolved = shutil.which(path)
            if resolved:
                shells.append({"id": shell_id, "name": Path(resolved).name, "path": resolved})
        return {"ok": True, "shells": shells}

    def user_terminal_attach(
        self, session_id: str, subscriber: OutputSubscriber, terminal_id: str = "1"
    ) -> dict[str, Any]:
        """Ensure the terminal, subscribe, then snapshot — in that order.

        Subscribe-before-snapshot is deliberate. A chunk produced between a snapshot and
        a later subscribe would be in neither the snapshot nor the stream, and would be
        lost silently. The other order can only duplicate (a chunk both snapshotted and
        delivered), which the subscriber resolves with the chunk's positions.
        """
        if not self._SESSION_ID_RE.match(session_id or "") or session_id in {".", ".."}:
            return {"ok": False, "error": "invalid session id"}
        started = self.user_terminal(session_id, terminal_id=terminal_id)
        if not started.get("ok"):
            return started
        key = self._terminal_key(session_id, terminal_id)
        with self._user_shell_lock:
            shell = self._user_shells.get(key)
            if shell is None:
                return {"ok": False, "error": "could not start a shell"}
            self._user_shell_subs.setdefault(key, set()).add(subscriber)
            return {"ok": True, **shell.snapshot()}

    def user_terminal_unsubscribe(
        self, session_id: str, subscriber: OutputSubscriber, terminal_id: str = "1"
    ) -> None:
        key = self._terminal_key(session_id, terminal_id)
        with self._user_shell_lock:
            subscribers = self._user_shell_subs.get(key)
            if subscribers is not None:
                subscribers.discard(subscriber)
                if not subscribers:
                    self._user_shell_subs.pop(key, None)

    def _user_terminal_output(
        self, key: tuple[str, str], chunk: str, start: int, end: int
    ) -> None:
        """Fan a chunk out to this terminal's subscribers (reader thread)."""
        with self._user_shell_lock:
            subscribers = list(self._user_shell_subs.get(key, ()))
        for subscriber in subscribers:
            try:
                subscriber(chunk, start, end)
            except Exception:  # noqa: BLE001 — one bad subscriber must not kill the reader
                pass

    def user_terminal_write(
        self, session_id: str, text: str, terminal_id: str = "1"
    ) -> dict[str, Any]:
        """Type raw text into the terminal (the panel's input path; no waiting)."""
        key = self._terminal_key(session_id, terminal_id)
        with self._user_shell_lock:
            shell = self._user_shells.get(key)
        if shell is None or not shell.running:
            started = self.user_terminal(session_id, terminal_id=terminal_id)
            if not started.get("ok"):
                return started
            with self._user_shell_lock:
                shell = self._user_shells.get(key)
        if shell is None:
            return {"ok": False, "error": "no terminal"}
        return shell.write(text)

    def user_terminal_interrupt(self, session_id: str, terminal_id: str = "1") -> dict[str, Any]:
        key = self._terminal_key(session_id, terminal_id)
        with self._user_shell_lock:
            shell = self._user_shells.get(key)
        if shell is None:
            return {"ok": False, "error": "no terminal"}
        return shell.interrupt()

    def user_terminal_resize(
        self, session_id: str, cols: int, rows: int, terminal_id: str = "1"
    ) -> dict[str, Any]:
        """The emulator's window size (xterm's fit reports it on mount and on resize)."""
        key = self._terminal_key(session_id, terminal_id)
        with self._user_shell_lock:
            shell = self._user_shells.get(key)
        if shell is None:
            return {"ok": False, "error": "no terminal"}
        return shell.resize(cols, rows)

    def user_terminal_close(self, session_id: str, terminal_id: str = "1") -> dict[str, Any]:
        key = self._terminal_key(session_id, terminal_id)
        with self._user_shell_lock:
            shell = self._user_shells.pop(key, None)
            self._user_shell_subs.pop(key, None)
        if shell is None:
            return {"ok": True, "closed": False}
        shell.close()
        return {"ok": True, "closed": True}

    def close_user_terminals(self) -> None:
        """Close every user terminal (server shutdown, or a workspace move)."""
        with self._user_shell_lock:
            shells = list(self._user_shells.values())
            self._user_shells.clear()
            self._user_shell_subs.clear()
        for shell in shells:
            try:
                shell.close()
            except Exception:  # noqa: BLE001 — shutdown closes what it can
                pass
