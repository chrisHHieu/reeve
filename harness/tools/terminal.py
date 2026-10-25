"""Persistent terminals — shells the agent can keep, and type into.

`run_shell` gives one long-lived shell and one shape of interaction: send a whole command,
wait for it to finish, read what it printed. Two things it cannot do, and reeve's own
code shows both were hit and worked around rather than solved:

**Answer a question.** `_NONINTERACTIVE_ENV` forces `GIT_TERMINAL_PROMPT=0`,
`PIP_NO_INPUT=1` and friends to stop programs asking; when one asks anyway, `ate_stdin`
DEDUCES it (measured on PowerShell 2026-08-26) and the model is told "nothing here can
answer it — re-run it non-interactively, or ask the user". That is an honest message about
a missing capability, not a solution.

**Keep two things running.** There is one shell. A REPL holding state and a build that must
run beside it cannot both exist.

The mechanism that makes answering possible is not a new tool — it is how output is read.
`LocalExecutor._read_loop` iterates `for line in stdout`, so a prompt is invisible to it by
construction: `Password: ` carries no newline and never completes an iteration. This reads
raw CHUNKS instead, which is why a session here can notice that a program has stopped and
is waiting.

Narrowed to what a pipe-backed
shell can honestly report. A wait reason can be `stdin_read` on a backend that
runs a real PTY and can see the read syscall. This one cannot, so it does not claim to:
its reasons are `idle`, `timeout` and `exited`, and "idle" means output stopped, which is
evidence of a prompt rather than proof of one.

A real PTY (ConPTY on Windows, `pty` on POSIX) ADDS `stdin_read` and the programs that
demand a tty — `ssh`, `sudo`, `git rebase -i`. It is now a backend of this same family:
`terminal_open(type="pty")` spawns `pty_session.PtySession` (the implementation the GUI
panel uses) and shares its buffer, so one reader still feeds both the panel and the tool —
while the default `type="shell"` stays pipe-backed, plain text, and cannnot claim more
than "output stopped" (`idle`). `stdin_read` is named only where a PTY can prove a child
process owns the foreground terminal (POSIX `tcgetpgrp`); on Windows that proof does not
exist, so the honest reason there is still `idle`.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from . import cards
from .agent_env import without_own_python
from .contract import ToolContract, ToolPresentation, attach_contract, clip
from .shell_kinds import default_shell_path
from harness.platform.spawn import run as spawn_run, spawn
from harness.platform.win_job import ProcessJob

_IS_WINDOWS = sys.platform == "win32"


def shell_argv(shell_path: str, *, is_windows: Optional[bool] = None) -> list[str]:
    """argv that makes `shell_path` one long-lived, stdin-driven shell.

    Keyed on the EXECUTABLE, not the OS: the user terminal can be asked for cmd.exe or
    bash on the same Windows machine (the rail's shell menu), and
    PowerShell's `-Command -` is a syntax error to cmd. cmd reads commands line by line
    from a piped stdin and `/K` keeps it alive after each; a POSIX shell gets `-i` so it
    prints a prompt. Unknown executables fall back to the interactive flag.
    """
    if is_windows is None:
        is_windows = _IS_WINDOWS
    name = Path(shell_path).name.lower()
    if name in {"powershell.exe", "powershell", "pwsh", "pwsh.exe"}:
        return [
            shell_path,
            "-NoProfile",
            "-NoLogo",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            "-",
        ]
    if name in {"cmd", "cmd.exe"}:
        return [shell_path, "/Q", "/K"]
    return [shell_path, "-i"]


def noecho_command(shell_path: str) -> str:
    """The startup line that silences the terminal's own echo, keyed by shell.

    A tty echoes input; a persistent shell opens with `stty -echo` for the same
    reason. PowerShell's PSReadLine redraws every keystroke (cursor moves, partial
    words), and removing it leaves the console's plain single echo, which is readable.
    cmd.exe has no line editor to remove and its console echo is already single, so it
    gets nothing. Returns "" when there is nothing to run.
    """
    name = Path(shell_path).name.lower()
    if name in {"powershell.exe", "powershell", "pwsh", "pwsh.exe"}:
        return "Remove-Module PSReadLine -ErrorAction SilentlyContinue"
    if name in {"cmd", "cmd.exe"}:
        return ""
    return "stty -echo 2>/dev/null || true"


#: Retained output per session. Enough to hold a build log's tail; small enough that ten
#: idle terminals cannot become a memory leak nobody attributes to them.
MAX_SCROLLBACK = 200_000

#: How long a send waits for output to stop before calling the session idle. Long enough
#: that a program pausing between lines is not mistaken for a prompt, short enough that
#: answering a real prompt does not feel like a hang.
IDLE_SECONDS = 0.6

#: Ceiling for one send. A command that runs longer is not failed — the send returns with
#: `timeout` and the session keeps running, which is the whole point of a session.
DEFAULT_SEND_TIMEOUT = 30.0
MAX_SEND_TIMEOUT = 600.0

#: How many terminals one session may hold open. A bound because each is a live process
#: tree, and an agent that opens one per thought would leave them all running.
MAX_SESSIONS = 8

#: Terminal backends `terminal_open` accepts. `shell` is the pipe-backed default;
#: `pty` is a real terminal for programs that require one.
TERMINAL_TYPES = ("shell", "pty")

#: Signal vocabulary for `terminal_signal`. SIGKILL is refused when it
#: would target the shell itself — ending the session is `terminal_close`'s job.
SIGNAL_NAMES = ("SIGINT", "SIGTERM", "SIGKILL", "SIGHUP", "SIGTSTP")

#: The six tool names this module owns. Used when a second owner takes over a registry
#: (`replace_terminal_tools`) — a subagent gets its OWN terminals (owner isolation).
TERMINAL_TOOL_NAMES = (
    "terminal_open",
    "terminal_send",
    "terminal_read",
    "terminal_signal",
    "terminal_close",
    "terminal_list",
)

_READ_CHUNK = 4096

#: One `terminal_read` page. Defaults `offset 0`, `count 500`, with the same
#: newest-relative offset: 0 is the tail, 500 is the page above it, and so on.
DEFAULT_READ_COUNT = 500
MAX_READ_COUNT = 5000


@dataclass
class _Buffer:
    """Bounded scrollback plus a cursor into what has already been handed out.

    Counts in TOTAL characters produced, not offsets into the retained text: the buffer
    drops its head as it fills, so an offset would silently start pointing at different
    output. The difference between the cursor and what is still retained is how the reader
    learns that something was dropped — which it must be told, because output silently cut
    is output it will reason about as though it were whole.
    """

    #: Retained output as appended chunks plus an aggregate count. A single string
    #: re-sliced on every append is O(retained) per PTY callback — measured 4159 ms → 8.75 ms
    #: steady on a 4 MiB stream after this change, so the hot path only appends and evicts whole chunks.
    _chunks: deque[str] = field(default_factory=deque)
    _count: int = 0
    produced: int = 0
    cursor: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def text(self) -> str:
        with self.lock:
            return self._joined()

    def _joined(self) -> str:
        """The retained text; the caller already holds the lock."""
        return "".join(self._chunks)

    def append(self, chunk: str) -> tuple[int, int]:
        """Add output; return its absolute [start, end) character positions.

        The positions are what lets a live subscriber dedupe against a snapshot taken
        concurrently (see `snapshot_with_cursor`): characters, not offsets into the
        retained text, so a dropped head cannot silently shift them.
        """
        with self.lock:
            start = self.produced
            if chunk:
                self._chunks.append(chunk)
                self._count += len(chunk)
                while self._count > MAX_SCROLLBACK and self._chunks:
                    head = self._chunks[0]
                    excess = self._count - MAX_SCROLLBACK
                    if len(head) <= excess:
                        self._chunks.popleft()
                        self._count -= len(head)
                    else:
                        self._chunks[0] = head[excess:]
                        self._count -= excess
            self.produced += len(chunk)
            return start, self.produced

    def drain(self) -> tuple[str, int]:
        """Everything not yet handed out, and how many characters were lost before it."""
        with self.lock:
            text = self._joined()
            oldest = self.produced - len(text)
            lost = max(0, oldest - self.cursor)
            start = max(0, self.cursor - oldest)
            out = text[start:]
            self.cursor = self.produced
            return out, lost

    def snapshot(self) -> str:
        with self.lock:
            return self._joined()

    def snapshot_with_cursor(self) -> tuple[str, int, int]:
        """Retained text and its absolute [start, end) span in the produced stream."""
        with self.lock:
            text = self._joined()
            return text, self.produced - len(text), self.produced


def page_lines(text: str, offset: int, count: int) -> tuple[str, int, int, int]:
    """One newest-relative page of retained output: (page, line_begin, line_end, total).

    `offset` counts back from the newest line (0 is the tail); `count` is how many lines
    the page holds. Shared with the remote world's terminal so one model-facing contract
    holds for both.
    """
    lines = text.splitlines()
    total = len(lines)
    off = max(0, int(offset or 0))
    size = max(1, min(int(count or DEFAULT_READ_COUNT), MAX_READ_COUNT))
    end = max(0, total - off)
    begin = max(0, end - size)
    body = "\n".join(lines[begin:end])
    if body:
        body += "\n"
    return body, begin, end, total


def _pid_alive(pid: int) -> bool:
    """Whether a Windows pid still exists (STILL_ACTIVE is 259; anything else is dead)."""
    import ctypes

    handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
    if not handle:
        return False
    code = ctypes.c_ulong()
    ok = ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
    ctypes.windll.kernel32.CloseHandle(handle)
    return bool(ok) and code.value == 259


_ANSI_RE = re.compile(
    r"\x1b\[[0-9;?]*[ -/]*[@-~]"  # CSI: colours, cursor moves, device queries
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC: window titles, hyperlinks
    r"|\x1b[@-Z\\-_]"  # two-character escapes (ESC c, ESC =, ESC >)
)


def strip_ansi(text: str) -> str:
    """What is actually on the screen: escape sequences removed. Used for readiness
    only — the model gets the raw stream, because a terminal's colours and progress
    redraws are part of what happened."""
    return _ANSI_RE.sub("", text)


def _group_alive(pgid: int) -> bool:
    """Whether a POSIX process group still has members (`killpg(0)` probe)."""
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return True
    return True


class TerminalSession:
    """One persistent shell, readable in chunks and writable at any time.

    Two backends, one interface: `type="shell"` is the pipe-backed default (clean text,
    `idle` is the strongest reason it can honestly name), `type="pty"` is a real terminal
    (`pty_session.PtySession`) for programs that need a tty — and the only backend that
    can report `stdin_read` on POSIX. Every method reads the same way for both.
    """

    def __init__(
        self,
        *,
        session_id: str,
        cwd: str | Path,
        name: str = "",
        env: Optional[dict[str, str]] = None,
        shell_path: Optional[str] = None,
        type: str = "shell",
        on_output: Optional[Any] = None,
    ) -> None:
        self.id = session_id
        self.name = name or session_id
        self.cwd = str(Path(cwd).expanduser().resolve())
        self.opened_at = time.time()
        self.type = "pty" if str(type or "").strip().lower() == "pty" else "shell"
        # `send`/`write` are serialized per terminal: two calls
        # that interleave their writes would type over each other. `interrupt`/`signal`
        # deliberately do NOT take it — a lock that stopped Ctrl-C behind a 30 s wait
        # would remove the one verb whose whole job is to cut that wait short.
        self._io_lock = threading.Lock()
        self._closed = False
        # A live subscriber (the GUI's terminal panel) gets every chunk as it lands, with
        # its absolute [start, end) positions. Called on the reader thread; a subscriber
        # that raises must never stop the shell from being read.
        self._on_output = on_output
        self._is_windows = _IS_WINDOWS
        self._pty: Optional[Any] = None
        self._proc: Optional[subprocess.Popen] = None
        #: Windows job-object containment for the spawned shell, when it attached.
        self._job: Optional[ProcessJob] = None
        if shell_path is None:
            shell_path = default_shell_path()  # the same finder run_shell uses
        self.shell_path = shell_path
        if self.type == "pty":
            # The panel's own PTY implementation, shared as a backend: it already owns a
            # reader thread, and its buffer is what this session reads. One reader over
            # one stream — the rule that keeps the tool and the panel from stealing each
            # other's output.
            from .pty_session import PtySession

            self._pty = PtySession(
                session_id=session_id,
                cwd=self.cwd,
                name=self.name,
                env=env,
                shell_path=shell_path,
                on_output=on_output,
            )
            self._buffer = self._pty.buffer
            return
        self._buffer = _Buffer()
        argv = shell_argv(shell_path, is_windows=self._is_windows)
        self._proc = spawn(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=self.cwd,
            # Reeve's own venv activation stays out of the terminal (agent_env.py).
            env={**without_own_python(os.environ), **(env or {})},
            bufsize=0,
        )
        # Structural containment on Windows: whatever this shell spawns dies with the
        # job handle even if the kill path never runs (Reeve crashed). Best-effort.
        self._job = ProcessJob.attach(self._proc.pid)
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    # -- reading -------------------------------------------------------------------

    def _read_loop(self) -> None:
        """Raw chunks, never lines.

        This is the whole reason a session can answer a prompt. `for line in stdout` blocks
        until a newline arrives, so `Password: ` — which has none — would sit unseen in the
        pipe while the caller concluded nothing was happening.
        """
        stream = self._proc.stdout
        if stream is None:
            return
        fd = stream.fileno()
        try:
            while True:
                data = os.read(fd, _READ_CHUNK)
                if not data:
                    return
                text = data.decode("utf-8", errors="replace")
                start, end = self._buffer.append(text)
                if self._on_output is not None:
                    try:
                        self._on_output(text, start, end)
                    except Exception:  # noqa: BLE001 — a bad subscriber costs itself, not the shell
                        pass
        except (OSError, ValueError):
            return

    # -- state ---------------------------------------------------------------------

    @property
    def running(self) -> bool:
        if self._pty is not None:
            return self._pty.running
        return self._proc is not None and self._proc.poll() is None

    def status(self) -> dict[str, Any]:
        if self._pty is not None:
            return {**self._pty.status(), "type": self.type}
        code = self._proc.poll() if self._proc is not None else None
        return {
            "id": self.id,
            "name": self.name,
            "cwd": self.cwd,
            "shell": self.shell_path,
            "type": self.type,
            "running": code is None,
            **({} if code is None else {"exit_code": code}),
            "opened_at": round(self.opened_at, 3),
        }

    # -- interaction ---------------------------------------------------------------

    def send(
        self,
        text: str,
        *,
        enter: bool = True,
        timeout: float = DEFAULT_SEND_TIMEOUT,
        idle: float = IDLE_SECONDS,
    ) -> dict[str, Any]:
        """Type into the session and wait for it to settle.

        Returns what appeared since the last read, and WHY it stopped waiting. The reason
        is the load-bearing field: `idle`/`stdin_read` after a send that produced a prompt
        is the session asking a question, and the caller answers it with another send.
        """
        timeout = max(0.1, min(float(timeout or DEFAULT_SEND_TIMEOUT), MAX_SEND_TIMEOUT))
        # A real terminal's Enter is CR; a pipe-backed shell's is LF.
        payload = text + (("\r" if self._pty is not None else "\n") if enter else "")
        with self._io_lock:
            if not self.running:
                out, lost = self._buffer.drain()
                return self._result(out, lost, reason="exited")
            try:
                if self._pty is not None:
                    self._pty.write(payload)
                elif self._proc is not None and self._proc.stdin is not None:
                    self._proc.stdin.write(payload.encode("utf-8"))
                    self._proc.stdin.flush()
                else:
                    return {"error": "terminal has no stdin"}
            except (OSError, ValueError) as exc:
                return {"error": f"terminal is not accepting input: {exc}"}
            return self._wait(timeout=timeout, idle=idle)

    def _wait(
        self, *, timeout: float, idle: float, require_output: bool = True
    ) -> dict[str, Any]:
        """Wait for the session to stop producing output, or to exit, or to run out of time.

        `require_output` is the difference between two questions that look alike. A SEND
        asks "did what I typed produce a reply, and has it stopped?", so silence from the
        start proves nothing and the wait must run its budget. STARTUP asks "has the shell
        finished saying whatever it says?", and silence is a complete answer — PowerShell
        in `-Command -` mode prints nothing at all, so requiring output there made every
        `open()` sit out its full budget for a shell that was ready immediately.
        """
        deadline = time.monotonic() + timeout
        last_change = time.monotonic()
        started_at = self._buffer.produced
        seen = started_at
        reason = "timeout"
        while True:
            if not self.running:
                reason = "exited"
                break
            now = time.monotonic()
            if self._buffer.produced != seen:
                seen = self._buffer.produced
                last_change = now
            elif (seen > started_at or not require_output) and now - last_change >= idle:
                # PRINTED SOMETHING AND THEN STOPPED. Both halves are needed, and the
                # first was missing at first: without it a command that simply works
                # quietly — `sleep 5`, a compile before its first line of output — is
                # indistinguishable from one waiting to be typed into, and every send of a
                # slow build would come back `idle` within a second claiming it had been
                # asked a question. Silence from the start is not evidence of a prompt; it
                # is evidence of nothing, and waiting out the budget is the honest answer.
                #
                # Even then this is evidence, not proof, so the reason names the strongest
                # claim the backend can support: a PTY can prove a CHILD owns the
                # foreground terminal (POSIX `tcgetpgrp`) — `stdin_read`, the name for
                # it. A pipe cannot see that at all, and Windows has no equivalent query,
                # so both stay `idle`. The caller reads the tail and decides.
                reason = (
                    "stdin_read"
                    if self._pty is not None and self._pty.foreground_is_child()
                    else "idle"
                )
                break
            if now >= deadline:
                reason = "timeout"
                break
            time.sleep(0.05)
        out, lost = self._buffer.drain()
        return self._result(out, lost, reason=reason)

    def read(self, *, offset: int = 0, count: int = DEFAULT_READ_COUNT) -> dict[str, Any]:
        """A PAGE of retained output, without sending anything — how to check on a long
        command. Newest-relative: `offset=0` is the tail, `offset=500`
        the page above it; the result names the line window and the retained total so a
        reader can walk backwards through a build log a page at a time."""
        text = self._buffer.snapshot()
        page, begin, end, total = page_lines(text, offset, count)
        lost = self._buffer.produced - len(text)
        payload = self._result(page, lost, reason="read")
        payload.update({"total_lines": total, "line_begin": begin, "line_end": end})
        return payload

    def write(self, text: str) -> dict[str, Any]:
        """Raw stdin, no waiting and no reply.

        `send` is the model's verb: type, then wait for the shell to settle. A terminal
        panel is the person's: keystrokes arrive as they are typed, and output arrives on
        its own through `on_output`, so blocking here would freeze the panel's input for
        every command that runs longer than the wait. Serialized with `send` so two
        writers cannot interleave mid-line.
        """
        with self._io_lock:
            if not self.running:
                return {"error": "terminal has exited", "running": False}
            try:
                if self._pty is not None:
                    self._pty.write(text)
                elif self._proc is not None and self._proc.stdin is not None:
                    self._proc.stdin.write(text.encode("utf-8"))
                    self._proc.stdin.flush()
                else:
                    return {"error": "terminal has no stdin"}
            except (OSError, ValueError) as exc:
                return {"error": f"terminal is not accepting input: {exc}"}
            return {"ok": True, "running": True}

    def snapshot(self) -> dict[str, Any]:
        """Status plus the retained scrollback and its absolute cursor.

        A subscriber attaches with this: `produced` is the end of the stream the text
        already covers, so chunks delivered afterwards can drop any prefix the snapshot
        already contains. A PTY session adds its window size (the emulator needs it).
        """
        if self._pty is not None:
            return {**self._pty.snapshot(), "type": self.type}
        text, _start, produced = self._buffer.snapshot_with_cursor()
        return {**self.status(), "output": text, "produced": produced}

    def interrupt(self) -> dict[str, Any]:
        """Ctrl-C, so a runaway command can be taken back without losing the shell and
        everything it holds. A PTY's Ctrl-C is the terminal's own (the line discipline
        delivers it to the foreground job); a pipe's is the process group's."""
        if not self.running:
            return {"error": "terminal has already exited"}
        try:
            if self._pty is not None:
                return self._after_signal(self._pty.interrupt(), "SIGINT")
            import signal as _signal

            if self._is_windows:
                self._proc.send_signal(_signal.CTRL_BREAK_EVENT)
            else:
                os.killpg(os.getpgid(self._proc.pid), _signal.SIGINT)
        except (OSError, ValueError, AttributeError) as exc:
            return {"error": f"could not interrupt: {exc}"}
        out, lost = self._buffer.drain()
        return self._result(out, lost, reason="interrupted")

    def signal(self, name: str) -> dict[str, Any]:
        """Deliver one allowed signal to the CURRENT foreground process group (the
        `terminal_signal` contract).

        The target is verified, not guessed: a PTY reports its foreground pgid
        (`tcgetpgrp`), so a Ctrl-C-shaped signal lands on the running command, not on the
        shell holding it. A pipe has no foreground concept — the shell's own group is the
        only honest target. Shell-targeted SIGKILL is refused by design: ending the
        session is `terminal_close`'s job, and a killed shell silently takes its scrollback
        with it.
        """
        requested = str(name or "").strip().upper()
        if not requested.startswith("SIG"):
            requested = f"SIG{requested}"
        if requested not in SIGNAL_NAMES:
            return {
                "error": (
                    f"unknown signal {name!r} — one of {', '.join(SIGNAL_NAMES)}"
                )
            }
        if not self.running:
            return {"error": "terminal has already exited"}
        target = "shell"
        if self._pty is not None and self._pty.foreground_is_child():
            target = "foreground"
        if requested == "SIGKILL" and target == "shell":
            return {
                "error": (
                    "refusing SIGKILL for the shell itself — use terminal_close to end "
                    "the session and kill its process tree"
                )
            }
        try:
            if self._is_windows:
                return self._signal_windows(requested, target)
            return self._signal_posix(requested, target)
        except (OSError, ValueError, AttributeError) as exc:
            return {"error": f"could not send {requested}: {exc}"}

    def _signal_windows(self, requested: str, target: str) -> dict[str, Any]:
        """Windows has no POSIX signals: Ctrl-C becomes CTRL_BREAK_EVENT / the console's
        own interrupt, an end-the-session signal becomes the process-tree kill, and the
        rest are refused by name rather than silently ignored."""
        if self._pty is not None:
            if requested == "SIGINT":
                self._pty.interrupt()
            elif requested in {"SIGTERM", "SIGKILL"}:
                self._pty.close()
            else:
                return {"error": f"{requested} is not supported on Windows"}
        else:
            if requested == "SIGINT":
                import signal as _signal

                self._proc.send_signal(_signal.CTRL_BREAK_EVENT)
            elif requested == "SIGTERM":
                self._kill_tree()
            else:
                return {"error": f"{requested} is not supported on Windows"}
        return self._after_signal({"ok": True}, requested, target)

    def _signal_posix(self, requested: str, target: str) -> dict[str, Any]:
        import signal as _signal

        if self._pty is not None:
            pgid = self._pty.foreground_pgid() or os.getpgid(self._pty.pid)
        else:
            pgid = os.getpgid(self._proc.pid)
        os.killpg(pgid, getattr(_signal, requested))
        return self._after_signal({"ok": True}, requested, target)

    def _after_signal(
        self, outcome: dict[str, Any], requested: str, target: str = "shell"
    ) -> dict[str, Any]:
        """Drain what the signal produced and label the wait, the way `interrupt` does."""
        if outcome.get("error"):
            return outcome
        time.sleep(0.2)
        out, lost = self._buffer.drain()
        payload = self._result(out, lost, reason="signal")
        payload.update({"signal": requested, "target": target})
        return payload

    def close(self) -> dict[str, Any]:
        """End the session and everything it owns — idempotently, and only after the
        process tree is actually gone.

        A shell's `terminate()` kills the shell, not its children: a dev server or a
        build the shell started keeps holding its port long after the session "closed".
        So this walks the tree (`taskkill /T /F` on Windows; the process group's
        SIGTERM→SIGKILL on POSIX) and then WAITS for it to empty, reporting honestly when
        something outlives the grace period instead of claiming a clean kill.
        """
        if self._closed:
            return {"id": self.id, "closed": True}
        self._closed = True
        gone = True
        if self._pty is not None:
            pid = self._pty.pid
            if self._is_windows and pid:
                self._kill_tree(pid)
            try:
                self._pty.close()
            except Exception:  # noqa: BLE001 — shutdown closes what it can
                pass
            if not self._is_windows and pid:
                gone = self._await_gone(pgid=pid)
        else:
            proc = self._proc
            if proc is not None:
                pgid: Optional[int] = None
                if not self._is_windows:
                    try:
                        pgid = os.getpgid(proc.pid)
                    except OSError:
                        pgid = None
                try:
                    if proc.stdin is not None:
                        proc.stdin.close()
                except (OSError, ValueError):
                    pass
                if self._is_windows:
                    if proc.poll() is None:
                        # taskkill kills the shell AND its descendants; terminate()
                        # alone would leave every child holding its port.
                        self._kill_tree(proc.pid)
                else:
                    import signal as _signal

                    try:
                        os.killpg(pgid, _signal.SIGTERM)
                    except (OSError, ValueError):
                        pass
                try:
                    proc.wait(timeout=3)
                except Exception:  # noqa: BLE001
                    try:
                        proc.kill()
                    except Exception:  # noqa: BLE001
                        pass
                if not self._is_windows:
                    gone = self._await_gone(pgid=pgid)
        result: dict[str, Any] = {"id": self.id, "closed": True}
        if not gone:
            result["warning"] = (
                "the process tree did not empty within 3s — some children may still be "
                "running"
            )
        return result

    def _kill_tree(self, pid: Optional[int] = None) -> None:
        """Kill a whole Windows process tree. The shell's `terminate()` only kills the
        shell; the job object (when it attached) reaches everything structurally, and
        `taskkill /T /F` stays as the fallback for the case it did not."""
        if pid is None and self._proc is not None:
            pid = self._proc.pid
        if not (self._is_windows and pid):
            return
        if pid == getattr(self._proc, "pid", None) and self._job is not None:
            self._job.terminate()
        try:
            spawn_run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True,
                timeout=10,
            )
        except Exception:  # noqa: BLE001 — best-effort teardown
            pass

    def _await_gone(self, *, pgid: Optional[int], timeout: float = 3.0) -> bool:
        """Wait until the POSIX process group has no members left (a real quiescence
        check, not a fixed sleep: `killpg(0)` answers whether anything remains)."""
        if pgid is None or self._is_windows:
            return True
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not _group_alive(pgid):
                return True
            time.sleep(0.05)
        return not _group_alive(pgid)

    def _result(self, out: str, lost: int, *, reason: str) -> dict[str, Any]:
        if self._pty is not None and out:
            # A real terminal speaks in cursor moves and colours (ConPTY/PSReadLine
            # redraws every keystroke). The model asked for a terminal to run a program,
            # not to parse escape codes: results carry what is ON SCREEN. The buffer
            # itself stays raw, so a panel subscriber still sees the true stream.
            out = strip_ansi(out)
        payload: dict[str, Any] = {
            "id": self.id,
            "type": self.type,
            "output": out,
            "reason": reason,
            "running": self.running,
        }
        if lost:
            # Named, never silent: output cut without saying so is output the model will
            # reason about as though it were whole (tools/retention.py's rule).
            payload["dropped_chars"] = lost
        if self._pty is not None:
            code = self._pty.status().get("exit_code")
        else:
            code = self._proc.poll() if self._proc is not None else None
        if code is not None:
            payload["exit_code"] = code
        return payload


class TerminalSessions:
    """Every terminal one session owns, and the only thing that hands them out."""

    def __init__(self, *, cwd: str | Path, env_provider: Optional[Any] = None) -> None:
        self.cwd = str(Path(cwd).expanduser().resolve())
        self._env_provider = env_provider
        self._sessions: dict[str, TerminalSession] = {}
        self._lock = threading.Lock()

    def open(
        self, *, name: str = "", cwd: Optional[str] = None, type: str = "shell"
    ) -> dict[str, Any]:
        kind = str(type or "shell").strip().lower()
        if kind == "pipe":
            kind = "shell"
        if kind not in TERMINAL_TYPES:
            return {
                "error": (
                    f"unknown terminal type {type!r} — one of {', '.join(TERMINAL_TYPES)}"
                )
            }
        with self._lock:
            live = {i: s for i, s in self._sessions.items() if s.running}
            self._sessions = live
            if len(live) >= MAX_SESSIONS:
                return {
                    "error": (
                        f"already holding {len(live)} terminals (the limit is "
                        f"{MAX_SESSIONS}) — close one before opening another"
                    ),
                    "terminals": [s.status() for s in live.values()],
                }
        session_id = f"term-{uuid.uuid4().hex[:8]}"
        env: dict[str, str] = {}
        if self._env_provider is not None:
            try:
                env = dict(self._env_provider())
            except Exception:  # noqa: BLE001 — context must not cost the terminal
                env = {}
        try:
            session = TerminalSession(
                session_id=session_id,
                cwd=cwd or self.cwd,
                name=name,
                env=env,
                type=kind,
            )
        except OSError as exc:
            return {"error": f"could not start a terminal: {exc}"}
        with self._lock:
            self._sessions[session_id] = session
        # A shell may print a banner before it is ready, and returning immediately would
        # let the first send race that text.
        #
        # A PIPE shell can be silent by design (PowerShell's `-Command -` prints
        # nothing), so silence is a complete answer there. A PTY greets differently: its
        # first bytes are ConPTY's DEVICE QUERIES (`\x1b[1t\x1b[c…`), not the shell — any
        # output counts as "settled", so waiting on output alone handed the first send a
        # shell that had not started reading (measured 2026-09-20). Wait until something
        # VISIBLE is on screen instead, bounded so a prompt-less shell still returns.
        if session.type == "pty":
            chunks: list[str] = []
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                settled = session._wait(
                    timeout=0.4, idle=IDLE_SECONDS, require_output=True
                )
                chunks.append(settled.get("output") or "")
                if strip_ansi("".join(chunks)).strip():
                    break
            # Echo suppression is SETUP, not a command the caller asked for: the
            # exchange is swallowed so it never reaches the model. (On POSIX `stty
            # -echo` was sent at the prompt; this runs once, before the first send.)
            setup = noecho_command(session.shell_path)
            if setup:
                try:
                    session.send(setup, timeout=5)
                except Exception:  # noqa: BLE001 — setup must not fail the open
                    pass
            return {**session.status(), "output": "".join(chunks)}
        settled = session._wait(timeout=2.0, idle=IDLE_SECONDS, require_output=False)
        return {**session.status(), "output": settled.get("output", "")}

    def get(self, session_id: str) -> Optional[TerminalSession]:
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


# -- the model-facing tools ------------------------------------------------------------
#
# `terminal_open`, `terminal_send` and `terminal_signal` are EXEC (harness/safety/risk.py's
# `EXEC_TOOLS`), which is what puts them behind the permission engine, the self-protection
# floor and the approval card. `terminal_send`'s text is read through
# `permissions.command_text`, so the floor inspects what is being typed rather than missing
# it because the argument is not spelled `command`. Reading, listing and closing touch
# nothing new and stay low.


# -- what a person sees (tools/contract.py) -------------------------------------
# `terminal_send` is the only one of these whose call IS a command, so it is the only one
# that gets a terminal card. `terminal_read` returns output from something already
# running, and a card headed by a command it did not run would attribute one to the other.


def _terminal_send_present_result(
    arguments: dict[str, Any], value: Any, card_meta: Any = None
) -> ToolPresentation:
    if not isinstance(value, dict):
        return ToolPresentation(summary="Sent input to the terminal", kind="execute")
    output = str(value.get("output") or "")
    typed = str(arguments.get("text") or "").strip()
    running = value.get("running")
    detail = f"{len(output.splitlines())} lines back"
    dropped = value.get("dropped_chars")
    if isinstance(dropped, int) and dropped:
        detail += f", {dropped:,} dropped"
    summary = "Terminal is waiting" if running else "Terminal command finished"
    if not typed:
        # No command to head a card with — the same rule the shell presenter applies.
        return ToolPresentation(summary=summary, detail=detail, kind="execute")
    return ToolPresentation(
        summary=summary,
        detail=detail,
        kind="execute",
        card=cards.terminal_result(
            title=typed,
            output=output,
            exit_code=value.get("exit_code"),
            truncated=bool(dropped),
        ),
    )


def _terminal_read_present_result(
    arguments: dict[str, Any], value: Any, card_meta: Any = None
) -> ToolPresentation:
    if not isinstance(value, dict):
        return ToolPresentation(summary="Read from a terminal", kind="read")
    output = str(value.get("output") or "")
    reason = str(value.get("reason") or "")
    detail = f"terminal {value.get('id', '')}"
    if isinstance(value.get("total_lines"), int):
        detail += (
            f" · lines {value.get('line_begin')}-{value.get('line_end')}"
            f" of {value['total_lines']}"
        )
    if reason:
        detail += f" · {reason}"
    if not output.strip():
        # An empty page is an answer (nothing retained yet, or past the head).
        return ToolPresentation(
            summary="No terminal output in this page", detail=detail, kind="read"
        )
    return ToolPresentation(
        summary=f"Read {len(output.splitlines())} lines from the terminal",
        detail=detail,
        kind="read",
        card=cards.generic_result(content=[output]),
    )


def _terminal_status(value: dict[str, Any]) -> str:
    """One bracket line: which terminal, how the wait ended, and what it cost.

    Keeps the facts the JSON form carried — `reason` (idle/stdin_read/timeout/exited/
    signal), whether the shell is still running, its exit code, the page window for a
    read, and how many characters were dropped — in one line instead of a per-key object.
    """
    parts: list[str] = []
    ident = value.get("id")
    if ident:
        parts.append(str(ident))
    if value.get("reason"):
        parts.append(str(value["reason"]))
    if "running" in value:
        state = "running" if value["running"] else "exited"
        if state != value.get("reason"):  # `reason` already names it at EOF
            parts.append(state)
    if isinstance(value.get("exit_code"), int):
        parts.append(f"exit {value['exit_code']}")
    if isinstance(value.get("total_lines"), int):
        parts.append(
            f"lines {value.get('line_begin')}-{value.get('line_end')}"
            f" of {value['total_lines']}"
        )
    if value.get("signal"):
        parts.append(f"sent {value['signal']}")
        if value.get("target") and value["target"] != "shell":
            parts.append(f"to {value['target']}")
    if isinstance(value.get("dropped_chars"), int):
        parts.append(f"{value['dropped_chars']} chars dropped")
    if value.get("warning"):
        parts.append(str(value["warning"]))
    for key in ("name", "cwd", "closed", "interrupted"):
        if value.get(key):
            parts.append(f"{key}={value[key]}")
    return "terminal " + " ".join(str(p) for p in parts) if parts else "terminal"


def _terminal_render(arguments: dict[str, Any], value: Any) -> str:
    """The terminal's new output, then a status line — instead of the JSON result."""
    if not isinstance(value, dict):
        return json.dumps(value, default=str)
    error = str(value.get("error") or "").strip()
    if error and "output" not in value:
        return f"Error: {error}"
    output = value.get("output")
    body = output if isinstance(output, str) and output else ""
    status = f"[{_terminal_status(value)}]"
    return f"{body}\n{status}" if body else status


def _terminal_list_render(arguments: dict[str, Any], value: Any) -> str:
    """One line per open terminal, replacing the JSON list of objects."""
    if not isinstance(value, dict):
        return json.dumps(value, default=str)
    error = str(value.get("error") or "").strip()
    if error:
        return f"Error: {error}"
    items = value.get("terminals")
    if not isinstance(items, list) or not items:
        return "No terminals"
    rows = [
        ", ".join(f"{key}={item[key]}" for key in sorted(item))
        for item in items
        if isinstance(item, dict)
    ]
    return "\n".join(rows)


def terminal_tools(sessions: "TerminalSessions") -> list:
    """The six verbs, over one session's terminals."""
    import aisuite as ai

    def terminal_open(name: str = "", cwd: str = "", type: str = "shell") -> dict:
        """Open a persistent terminal and keep it. `type="shell"` (the default) is a
        pipe-backed shell with plain text output; `type="pty"` is a REAL terminal — use it
        for programs that require a tty (ssh, sudo, git rebase -i, full-screen REPLs). Use
        a terminal when work needs state across calls (a REPL, an activated environment, a
        long build) or when a command may ask a question — unlike run_shell, this one can
        be answered. Returns the terminal id."""
        return sessions.open(name=name, cwd=cwd or None, type=type)

    def terminal_send(
        id: str, text: str, enter: bool = True, timeout: float = DEFAULT_SEND_TIMEOUT
    ) -> dict:
        """Type into a terminal and wait for it to settle. `text` is a command, or the
        ANSWER to something the terminal is waiting for. Returns the output since your last
        read and why the wait ended: `idle` (it printed something and stopped — read the
        tail, it is often a prompt), `timeout` (still working; send again or read later),
        `exited` (the shell is gone)."""
        session = sessions.get(id)
        if session is None:
            return {"error": f"no terminal {id!r}", "terminals": sessions.list()}
        return session.send(text, enter=bool(enter), timeout=float(timeout))

    def terminal_read(id: str, offset: int = 0, count: int = DEFAULT_READ_COUNT) -> dict:
        """Read a PAGE of a terminal's retained output without typing anything — how to
        check on a command that was still running when the last send returned, or to walk
        back through a long log. Newest-relative: `offset=0` is the tail, `offset=500` the
        page above it; the result names the line window and the retained total."""
        session = sessions.get(id)
        if session is None:
            return {"error": f"no terminal {id!r}", "terminals": sessions.list()}
        return session.read(offset=int(offset or 0), count=int(count or DEFAULT_READ_COUNT))

    def terminal_signal(id: str, signal: str) -> dict:
        """Send a signal to the terminal's current foreground process group. `SIGINT`
        stops what is running without ending the session (the same effect as Ctrl-C);
        `SIGTERM`/`SIGHUP` end the shell; `SIGKILL` is refused for the shell itself — use
        terminal_close for that. The target is the foreground job when a PTY can prove
        one, the shell's own group otherwise."""
        session = sessions.get(id)
        if session is None:
            return {"error": f"no terminal {id!r}", "terminals": sessions.list()}
        return session.signal(signal)

    def terminal_close(id: str) -> dict:
        """Close a terminal and end what it is running. Close terminals you are done with:
        there is a limit, and each one is a live process."""
        return sessions.close(id)

    def terminal_list() -> dict:
        """Every terminal you have open, with what it is and whether it is still alive."""
        return {"terminals": sessions.list()}

    attach_contract(
        terminal_open,
        ToolContract(
            render=_terminal_render,
            present_call=lambda args: ToolPresentation(
                summary="Open a terminal"
                + (f" ({args.get('name')})" if args.get("name") else "")
                + (" [pty]" if str(args.get("type") or "").lower() == "pty" else ""),
                detail=str(args.get("cwd") or ""),
                kind="execute",
            ),
            timeout_ms=60_000,
        ),
    )
    attach_contract(
        terminal_send,
        ToolContract(
            render=_terminal_render,
            # The card must show WHAT IS BEING TYPED. A card reading "send to term-1a2b"
            # asks a person to consent to a command they were never shown.
            present_call=lambda args: ToolPresentation(
                summary=f"Run in the terminal: {clip(str(args.get('text', '')), 120)}",
                detail=f"terminal {args.get('id', '')}",
                kind="execute",
                card=cards.terminal_call(
                    str(args.get("text") or "").strip() or "(send input)",
                    description=f"Terminal {args.get('id', '')}".strip(),
                ),
            ),
            present_result=_terminal_send_present_result,
            # `terminal_send` waits for the command, so its own budget is the caller's
            # `timeout` clamped to `MAX_SEND_TIMEOUT` (600 s). This sits above that: the
            # tool's own wait wakes and reports a timeout properly (`reason: "timeout"`
            # with the session still alive), and this only catches the case where that
            # loop itself never comes back.
            timeout_ms=int(MAX_SEND_TIMEOUT * 1000) + 60_000,
        ),
    )
    attach_contract(
        terminal_read,
        ToolContract(
            render=_terminal_render,
            present_result=_terminal_read_present_result,
            timeout_ms=60_000,
        ),
    )
    attach_contract(
        terminal_close,
        ToolContract(
            render=_terminal_render,
            present_call=lambda args: ToolPresentation(
                summary=f"Close terminal {args.get('id', '')}"
            ),
            timeout_ms=60_000,
        ),
    )
    attach_contract(
        terminal_signal,
        ToolContract(
            render=_terminal_render,
            present_call=lambda args: ToolPresentation(
                summary=(
                    f"Send {str(args.get('signal') or 'SIGINT').upper()} to terminal "
                    f"{args.get('id', '')}"
                ),
                kind="execute",
            ),
            timeout_ms=60_000,
        ),
    )
    attach_contract(terminal_list, ToolContract(render=_terminal_list_render))

    def _wrap(func, *, risk: str, capabilities=("terminal",)):
        return ai.tool(
            func,
            metadata=ai.ToolMetadata(
                category="terminal", risk_level=risk, capabilities=list(capabilities)
            ),
        )

    return [
        _wrap(terminal_open, risk="high"),
        _wrap(terminal_send, risk="high"),
        _wrap(terminal_read, risk="low"),
        _wrap(terminal_signal, risk="high"),
        _wrap(terminal_close, risk="low"),
        _wrap(terminal_list, risk="low"),
    ]


def replace_terminal_tools(registry: Any, sessions: "TerminalSessions") -> bool:
    """Give a registry a SECOND owner's terminals (owner isolation).

    A subagent's registry is a copy of its session's, so its `terminal_*`
    closures still point at the parent's shells — a child could list, read, type into or
    close the terminals the parent is holding. The owner's own world is passed here
    instead: the same six tools, backed by the child's `TerminalSessions`, so a child's
    terminal ids are its own and a parent's ids answer "no terminal" there (the honest
    equivalent of a foreign-session error). Returns whether the swap happened.
    """
    if registry.get("terminal_open") is None:
        return False
    for name in TERMINAL_TOOL_NAMES:
        registry.remove(name)
    registry.register_all(terminal_tools(sessions))
    return True
