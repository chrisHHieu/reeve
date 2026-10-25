"""Real terminals — a pseudoconsole (ConPTY) on Windows, a pty on POSIX.

The terminal panel used to be a pipe-backed shell (`TerminalSession`): honest for the
agent's tool calls, wrong for a person. A pipe has no terminal on the other end, so
PowerShell runs non-interactively (no prompt to answer), cmd has no line editing, and no
program can paint a screen. A sidebar terminal is a real PTY with xterm.js on top;
this module is the backend half of that parity (`TerminalPanel.tsx` hosts the emulator).

Windows rides `pywinpty` (`winpty.PtyProcess`), which wraps the same ConPTY API node-pty
uses; hand-rolling the ctypes attribute-list plumbing was tried and the child silently
kept the parent console, which is exactly the class of bug a maintained binding prevents.
POSIX uses the stdlib `pty` module. Both give the child a real tty, so prompts, colours,
arrow keys, Ctrl-C and full-screen programs behave like a terminal.

The interface mirrors `TerminalSession` where `UserTerminalMixin` touches it (`running`,
`write`, `interrupt`, `close`, `snapshot`, `shell_path`) and adds `resize` for the
emulator's window size. It is ALSO the agent tool's `type="pty"` backend:
`TerminalSession` wraps a `PtySession` and reads the SAME buffer this module's reader
thread fills — one reader per stream, which is why it must never be wrapped twice. A
pipe-backed `type="shell"` session stays the default (clean text, no tty), so no program
that speaks escape codes can hide behind "the agent has a shell". Shells launch
interactive: PowerShell gets `-NoLogo` (not `-Command -`, which is non-interactive by
design); cmd and POSIX shells need no special argv once they own a tty.
"""

from __future__ import annotations

import codecs
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

from .shell_kinds import default_shell_path
from .terminal import _Buffer, _READ_CHUNK
from harness.platform.spawn import spawn

_IS_WINDOWS = sys.platform == "win32"

if _IS_WINDOWS:
    try:
        from winpty import PtyProcess as _PtyProcess  # pywinpty
    except ImportError:  # pragma: no cover - exercised by the error path below
        _PtyProcess = None
else:
    _PtyProcess = None

#: The emulator's opening window size (xterm's fit sends its real size right after mount).
DEFAULT_COLS = 120
DEFAULT_ROWS = 30


def pty_argv(shell_path: str) -> list[str]:
    """argv for an INTERACTIVE shell owning a real terminal.

    Different from `shell_argv` (the pipe-backed tool shell): there, PowerShell's
    `-Command -` is what makes a stdin pipe runnable at all; here it would suppress the
    prompt the pty exists to show. cmd and POSIX shells behave interactive on their own
    once their stdio is a tty.
    """
    name = Path(shell_path).name.lower()
    if name in {"powershell.exe", "powershell", "pwsh", "pwsh.exe"}:
        return [shell_path, "-NoLogo"]
    return [shell_path]


class PtySession:
    """One persistent real terminal. Spawn, stream, type, resize, kill."""

    def __init__(
        self,
        *,
        session_id: str,
        cwd: str | Path,
        name: str = "",
        env: Optional[dict[str, str]] = None,
        shell_path: Optional[str] = None,
        cols: int = DEFAULT_COLS,
        rows: int = DEFAULT_ROWS,
        on_output: Optional[Any] = None,
        marks: bool = False,
    ) -> None:
        self.id = session_id
        self.name = name or session_id
        self.cwd = str(Path(cwd).expanduser().resolve())
        self.opened_at = time.time()
        self.cols = max(20, int(cols or DEFAULT_COLS))
        self.rows = max(5, int(rows or DEFAULT_ROWS))
        self._buffer = _Buffer()
        self._on_output = on_output
        self._closed = False
        if shell_path is None:
            shell_path = default_shell_path()  # the same finder run_shell uses
        self.shell_path = shell_path
        argv = pty_argv(shell_path)
        from .agent_env import without_own_python

        # Reeve's own venv activation stays out of the terminal (agent_env.py).
        env_full = {**without_own_python(os.environ), **(env or {})}
        # The person's panel asks the shell to mark its prompt and each command
        # (`shell_marks.py`); the agent's `type="pty"` shell never does.
        self.marks = False
        if marks:
            from .shell_marks import integration

            extra_argv, extra_env, self.marks = integration(shell_path)
            if extra_argv:
                argv = [*argv, *extra_argv]
            env_full.update(extra_env)

        if _IS_WINDOWS:
            if _PtyProcess is None:  # pragma: no cover - depends on the machine
                raise OSError(
                    "pywinpty is required for the terminal panel on Windows "
                    "(pip install pywinpty)"
                )
            try:
                # pywinpty dimensions are (rows, cols).
                self._pty = _PtyProcess.spawn(
                    argv, cwd=self.cwd, env=env_full, dimensions=(self.rows, self.cols)
                )
            except Exception as exc:  # noqa: BLE001 — surface why the shell did not start
                raise OSError(f"could not start a shell: {exc}") from exc
        else:
            import pty

            master, slave = pty.openpty()
            self._resize_posix(master)
            self._proc = spawn(
                argv,
                stdin=slave,
                stdout=slave,
                stderr=slave,
                cwd=self.cwd,
                env=env_full,
                start_new_session=True,
                close_fds=True,
            )
            os.close(slave)
            self._master_fd = master

        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    # -- reading -----------------------------------------------------------------------

    def _read_loop(self) -> None:
        # POSIX pty reads are bytes; keep one incremental decoder so a multi-byte character
        # split across reads is not mangled into replacement glyphs.
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        try:
            while True:
                if _IS_WINDOWS:
                    try:
                        raw: Any = self._pty.read(_READ_CHUNK)
                    except EOFError:
                        return
                    if not raw:
                        if not self.running:
                            return
                        time.sleep(0.01)
                        continue
                    text = raw
                else:
                    data = os.read(self._master_fd, _READ_CHUNK)
                    if not data:
                        return
                    text = decoder.decode(data)
                if not text:
                    continue
                start, end = self._buffer.append(text)
                if self._on_output is not None:
                    try:
                        self._on_output(text, start, end)
                    except Exception:  # noqa: BLE001 — a bad subscriber costs itself, not the shell
                        pass
        except (OSError, ValueError):
            return

    # -- state -------------------------------------------------------------------------

    @property
    def buffer(self) -> _Buffer:
        """The shared scrollback. `TerminalSession`'s `type="pty"` mode reads THIS buffer,
        so a wrapper must never start a second reader over the same stream."""
        return self._buffer

    @property
    def pid(self) -> Optional[int]:
        """The shell's process id (pywinpty's process on Windows, the Popen's on POSIX)."""
        if _IS_WINDOWS:
            try:
                return int(self._pty.pid)
            except Exception:  # noqa: BLE001 — a dead handle has no pid to report
                return None
        return self._proc.pid

    def foreground_pgid(self) -> Optional[int]:
        """The POSIX foreground process group, or None where it cannot be known.

        Windows/ConPTY has no equivalent query, and a wrapper must not guess: None is
        what keeps `stdin_read` off a platform that cannot prove it.
        """
        if _IS_WINDOWS:
            return None
        try:
            return os.tcgetpgrp(self._master_fd)
        except (OSError, ValueError):
            return None

    def shell_pgid(self) -> Optional[int]:
        """The shell's own process group (POSIX), for comparing against the foreground."""
        if _IS_WINDOWS:
            return None
        try:
            return os.getpgid(self._proc.pid)
        except (OSError, ValueError):
            return None

    def foreground_is_child(self) -> bool:
        """True when a CHILD process owns the terminal's foreground (POSIX only).

        This is the evidence `stdin_read` is named on: the shell has handed the terminal
        to a running program, and output has stopped. A quiet foreground child may still
        be merely slow — but this is the strongest claim a PTY can make without typing
        into it, and it is strictly more than a pipe can say.
        """
        fg = self.foreground_pgid()
        shell = self.shell_pgid()
        return bool(fg and shell and fg != shell)

    @property
    def running(self) -> bool:
        if _IS_WINDOWS:
            try:
                return bool(self._pty.isalive())
            except Exception:  # noqa: BLE001 — a dead handle reads as not running
                return False
        return self._proc.poll() is None

    def status(self) -> dict[str, Any]:
        running = self.running
        code: Optional[int] = None
        if not running:
            if _IS_WINDOWS:
                try:
                    code = self._pty.exitstatus
                except Exception:  # noqa: BLE001
                    code = None
            else:
                code = self._proc.poll()
        return {
            "id": self.id,
            "name": self.name,
            "cwd": self.cwd,
            "shell": self.shell_path,
            "running": running,
            "marks": self.marks,
            **({} if code is None else {"exit_code": code}),
            "opened_at": round(self.opened_at, 3),
        }

    def snapshot(self) -> dict[str, Any]:
        return {
            **self.status(),
            "output": self._buffer.snapshot(),
            "produced": self._buffer.produced,
            "cols": self.cols,
            "rows": self.rows,
        }

    # -- interaction -------------------------------------------------------------------

    def write(self, text: str) -> dict[str, Any]:
        """Type raw keystrokes into the terminal (xterm sends exactly what was pressed)."""
        if not self.running:
            return {"error": "terminal has already exited"}
        if not text:
            return {"ok": True, "running": True}
        try:
            if _IS_WINDOWS:
                self._pty.write(text)
            else:
                os.write(self._master_fd, text.encode("utf-8"))
        except (OSError, ValueError) as exc:
            return {"error": f"terminal is not accepting input: {exc}"}
        return {"ok": True, "running": True}

    def interrupt(self) -> dict[str, Any]:
        """Ctrl-C the way a terminal means it: the terminal itself delivers the interrupt."""
        if not self.running:
            return {"error": "terminal has already exited"}
        try:
            if _IS_WINDOWS:
                self._pty.sendintr()
            else:
                os.write(self._master_fd, b"\x03")
        except (OSError, ValueError) as exc:
            return {"error": f"could not interrupt: {exc}"}
        return {"ok": True, "running": True}

    def resize(self, cols: int, rows: int) -> dict[str, Any]:
        self.cols = max(20, int(cols or self.cols))
        self.rows = max(5, int(rows or self.rows))
        try:
            if _IS_WINDOWS:
                # pywinpty's order is (rows, cols), the opposite of the wire/emulator.
                self._pty.setwinsize(self.rows, self.cols)
            else:
                self._resize_posix(self._master_fd)
        except (OSError, ValueError) as exc:
            return {"error": f"could not resize: {exc}"}
        return {"ok": True, "cols": self.cols, "rows": self.rows}

    def _resize_posix(self, master: int) -> None:
        import fcntl
        import struct
        import termios

        fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", self.rows, self.cols, 0, 0))

    def close(self) -> dict[str, Any]:
        if self._closed:
            return {"id": self.id, "closed": True}
        self._closed = True
        if _IS_WINDOWS:
            try:
                self._pty.terminate(force=True)
            except Exception:  # noqa: BLE001 — shutdown closes what it can
                pass
            try:
                self._pty.close(force=True)
            except Exception:  # noqa: BLE001
                pass
        else:
            try:
                os.close(self._master_fd)
            except OSError:
                pass
            if self._proc.poll() is None:
                try:
                    self._proc.terminate()
                    self._proc.wait(timeout=3)
                except Exception:  # noqa: BLE001
                    try:
                        self._proc.kill()
                    except Exception:  # noqa: BLE001
                        pass
        return {"id": self.id, "closed": True}
