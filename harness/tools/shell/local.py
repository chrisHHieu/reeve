"""`LocalExecutor`: every `run_shell` call in a process of its own on this machine.

Two things a long-lived shell used to hold are reproduced the honest way. The working directory
persists, because each process reports its directory in a trailer and the next command starts
there. The managed `REEVE_*` variables are resolved fresh for each command. What a command sets in
its own environment does not survive it, and neither does an activated venv.

The default shell is the OS's own: `/bin/bash` on POSIX and `powershell.exe` on Windows. A command
may name the other dialect the machine has, `shell="bash"` for Git Bash on Windows or `pwsh` on
POSIX (`shell_kinds.py`). Each dialect has its own script and exit-code protocol, but the
contract and the parsed `{marker} {exit_code} {cwd}` trailer are the same.

Safety here is permission gating (a high-risk tool needs approval) plus a wait budget that never
costs work. A command that outruns `timeout_seconds` is not killed. It becomes a background task and
the session is woken with its result. Stdin is closed for every command, so a prompt
reads EOF and does not wedge the turn.

Background tasks (`run_in_background`) are the same detached process without the wait, and a
foreground command that times out joins them in one registry. `close()`, which the interrupt path
calls, leaves them alone. They end when they exit or through `shell_task_kill`.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

from .. import output
from ..agent_env import without_own_python
from ..shell_background import BackgroundTask, ShellBackgroundMixin
from ..shell_cmd import compose as cmd_compose
from ..shell_env import ShellEnv, export_preamble
from ..shell_kinds import KINDS, Shell, alternative, local_shells, normalise, pick_default
from ..shell_sequence import sequence_script
from ..shell_status import _parse_cwd, _parse_details, _parse_exit_code, _reported_exit
from ..shell_trailer import trailer
from ..shell_wait import wait_for_command
from ..spill import SpillStore
from .executor import Executor
from .limits import DEFAULT_TIMEOUT, NONINTERACTIVE_ENV
from .cwd_drift import drift_note
from .outcome import OutcomeMixin

_IS_WINDOWS = sys.platform == "win32"
_REAP_SECONDS = 5


class LocalExecutor(OutcomeMixin, ShellBackgroundMixin, Executor):
    def __init__(
        self,
        *,
        cwd: str | Path,
        env: Optional[dict[str, str]] = None,
        shells: Optional[dict[str, Shell]] = None,
        default_timeout: float = DEFAULT_TIMEOUT,
        max_output_chars: int = 20_000,
        spill: Optional[SpillStore] = None,
        shell_env: Optional[ShellEnv] = None,
        path_append: Optional[list[str]] = None,
    ) -> None:
        self.cwd = str(Path(cwd).expanduser().resolve())
        # The session folder, and the last command that moved the shell away from it: what a failed
        # command is told when it ran somewhere else (`cwd_drift.py`).
        self._root = self.cwd
        self._moved_by: Optional[str] = None
        self.default_timeout = default_timeout
        self.max_output_chars = max_output_chars
        # Bound once and not per command. The retainer asks for it on every run, and only the
        # truncating ones call it. None when the session has nowhere to write.
        self._spill = spill.sink("shell") if spill is not None else None
        # The managed `REEVE_*` set (shell_env.py), asked per command and not merged into `_env`.
        # The facts it publishes change while a session runs: a folder granted mid-session has to
        # show in the very next command, and a value fixed into the environment at spawn would not.
        self._shell_env = shell_env
        self._marker = f"__REEVE_DONE_{uuid.uuid4().hex}__"
        self._is_windows = _IS_WINDOWS
        self._abort = threading.Event()  # set by interrupt_now (the person's Stop)
        self._waiting: set[Any] = set()  # foreground commands in a wait, so a Stop can wake them
        self._person_waiting: Optional[threading.Event] = None  # the engine's (`set_person_waiting`)
        self._init_background()
        # The shells this machine has. A shell is not long-lived any more, because it is one process
        # per command: it only decides which dialect a command's script is written in and which
        # executable runs it.
        self._shells = dict(shells) if shells is not None else dict(local_shells())
        # The default is read once, with the world: `run_shell`'s schema names it, and a Settings
        # change mid-session must not run a command in a dialect the schema did not promise.
        self._default = pick_default(self._shells, windows=self._is_windows)
        self._env ={**without_own_python(os.environ), **NONINTERACTIVE_ENV, **(env or {})}
        self._extend_path(path_append or [])

    def _init_background(self) -> None:
        self._bg_tasks: dict[str, BackgroundTask] = {}
        self._bg_counter = 0
        # Guards id minting and registration. Commands run concurrently, one process each, so two
        # calls on one executor could mint the same id and the second would overwrite the first.
        self._bg_lock = threading.Lock()
        # Where a finished task reports itself. The manager installs one so the session is woken
        # and does not have to poll. None means nobody is listening.
        self._bg_listener: Optional[Callable[[dict[str, Any]], None]] = None
        # Where a running task's output goes as it is read, so a jobs panel appends frames and does
        # not poll the tail. None means nobody follows, and then a task streams nothing.
        self._bg_output_listener: Optional[Callable[[dict[str, Any]], None]] = None
        # Told, with no arguments, when a task joins the registry or its process ends, so a jobs
        # panel is pushed the roster and does not ask for it on a clock.
        self._bg_roster_listener: Optional[Callable[[], None]] = None
        # The owner's revert generation, asked at each spawn so a completion can be dropped when the
        # work it describes has since been undone.
        self._bg_generation: Optional[Callable[[], int]] = None

    def _extend_path(self, extra: list[str]) -> None:
        """Append the managed bin folder and any plugin bin folders to PATH, once.

        The managed folder goes on here for every command this executor will ever run, so a tool
        the user approves mid-session works by name in the very next command. A freshly installed
        trivy once needed a retry with its full path. Enabled plugins' `bin/` folders follow (ADR
        0065). All are appended and never prepended, so the user's own copies win and a plugin's
        `git` cannot shadow the machine's."""
        from harness.tools import toolchain

        for folder in [str(toolchain.bin_dir()), *extra]:
            path = self._env.get("PATH", "")
            if folder not in path.split(os.pathsep):
                self._env["PATH"] = f"{path}{os.pathsep}{folder}" if path else folder

    def shells(self) -> list[Shell]:
        """Every dialect this machine can run, in `shell_kinds.KINDS` order."""
        return [self._shells[kind] for kind in KINDS if kind in self._shells]

    def default_shell(self) -> str:
        """The kind a command that names none runs in, fixed when this world was built."""
        return self._default

    def _shell_for(self, kind: Optional[str]) -> Shell:
        """The shell a command asked for, or this OS's own when it did not ask.

        Raises `ValueError` in words the model can act on: what it asked for and what exists."""
        wanted = normalise(kind) or self._default
        found = self._shells.get(wanted)
        if found is None:
            have = ", ".join(shell.kind for shell in self.shells()) or "none"
            raise ValueError(f"shell {wanted!r} is not available on this machine (available: {have})")
        return found

    def _trailer(self, shell: Optional[Shell] = None) -> str:
        """The line appended after each command to report how it ended (shell_trailer.py)."""
        return trailer(self._marker, shell or self._shell_for(None), windows=self._is_windows)

    def _task_for(
        self, command: str, script: str, shell: Shell, cleanup: Optional[list[str]] = None
    ) -> BackgroundTask:
        """The process for one command. Its id is minted here and not by the background path: a
        foreground command that has to be backgrounded keeps the id it was born with, so the result
        the model already saw names a task it can still read."""
        with self._bg_lock:
            self._bg_counter += 1
            task_id = f"bg-{self._bg_counter}"
        return BackgroundTask(
            task_id,
            command,
            self.cwd,
            self._managed_env(),
            generation=self._generation_now(),
            script=script,
            shell=shell,
            cleanup=cleanup,
        )

    def _adopt(self, task: BackgroundTask) -> None:
        """Hand a foreground command that is still running to the background registry.

        It is registered and watched, which is the point of not killing it: the session is woken
        when it finishes, `shell_task_output` can read it meanwhile, and
        `shell_task_kill` stops it. The only difference from a `run_in_background` task is that this
        one has already spent its wait budget."""
        with self._bg_lock:
            self._bg_tasks[task.id] = task
        self._watch_background(task)

    def _script_for(self, command: str, chosen: Shell) -> tuple[str, list[str]]:
        """The script text handed to the shell, and any temporary files it needs removed after.

        The command goes in as text, since argv on Windows is Unicode and a non-ASCII command needs
        no encoding. The trailer follows on its own line so that it reads the command's status."""
        if chosen.is_cmd:
            # One line carries the trailer. Several lines become a batch file (shell_cmd.py).
            return cmd_compose(command, self._marker)
        script = (
            self._managed_preamble(chosen)
            + sequence_script(command, powershell=chosen.is_powershell)
            + "\n"
            + self._trailer(chosen)
        )
        return script, []

    def run(
        self, command: str, timeout: Optional[float] = None, shell: Optional[str] = None
    ) -> dict[str, Any]:
        """Run one command in a process of its own and wait for its trailer.

        One process per command keeps the timeout policy simple, because the deadline only decides
        how long to wait. Nothing is killed: the process is registered as a background task and the
        session is woken when it finishes, so a slow command loses no work and costs no re-run.

        What this gives up: `cd` still persists, but environment variables a command sets do not.
        Each command has its own environment, with the managed `REEVE_*` set resolved fresh."""
        timeout = timeout or self.default_timeout
        # A Stop that arrived with nothing in flight (an approval card, a finished command) must not
        # poison the next command. The flag means "stop what is running now", so clear it first.
        self._abort.clear()
        try:
            chosen = self._shell_for(shell)
        except ValueError as exc:
            return self._result(command, None, "", timed_out=False, error=str(exc))
        other = alternative(chosen, self._shells)
        script, temps = self._script_for(command, chosen)
        try:
            task = self._task_for(command, script, chosen, temps)
        except (OSError, ValueError) as exc:
            # ValueError covers what argv cannot carry (an embedded NUL), and OSError a command
            # line too long for CreateProcess.
            return self._result(command, None, "", timed_out=False, error=f"failed to start: {exc}")

        started = time.monotonic()
        self._waiting.add(task)
        try:
            waited = wait_for_command(
                task,
                marker=self._marker,
                deadline=started + timeout,
                stopped=self._abort,
                # The reader sees each line as it arrives and not when the command ends
                # (tools/output.py). The result below is unchanged: the whole truth the model reads.
                emit=output.emit,
                released=self._person_waiting,
            )
        finally:
            self._waiting.discard(task)
        if waited.outcome in ("deadline", "released"):
            # Not a kill: the command keeps running and the session is woken later.
            return {**self._background_result(task, waited.lines, started, waited.outcome), "shell": chosen.kind}
        if waited.outcome == "stopped":
            task.kill()

        before = self.cwd
        exit_code, exit_note = self._read_trailer(waited, command)
        moved_now = self.cwd != before
        if moved_now:
            self._moved_by = None if os.path.normcase(self.cwd) == os.path.normcase(self._root) else command
        # Reap. The pipe closes a moment before the exit status exists, and a command with no trailer
        # is reported by the process's own code. This also leaves no zombie.
        try:
            task.proc.wait(timeout=_REAP_SECONDS)
        except (subprocess.TimeoutExpired, OSError):
            pass
        aborted = self._abort.is_set()  # the Stop killed it, or the process died by itself
        elapsed = time.monotonic() - started
        kept, total = self._keep(waited.lines)
        if waited.trailer is None:
            result = self._unreported_result(
                command, task, waited, kept, total, chosen, other, exit_code, aborted, elapsed
            )
        else:
            result = self._reported_result(
                command, waited, kept, total, chosen, other, exit_code, exit_note, aborted, elapsed
            )
        # Which shell ran it. The default is a Setting the GUI cannot see, so a call
        # that named none was drawn as PowerShell while it ran in bash (owner, 2026-10-15). Data
        # for the card: the model's text is rendered from the output and status only.
        result["shell"] = chosen.kind
        # A command that moved the shell itself chose where it ran; only an earlier move is news.
        note = None if moved_now else drift_note(
            result, cwd=self.cwd, root=self._root, moved_by=self._moved_by, command=command
        )
        if note:
            result["error"] = f"{result['error']}; {note}" if result.get("error") else note
            result["cwd_drift"] = True  # counted by the harness review
        return result

    def _read_trailer(self, waited, command: str) -> tuple[Optional[int], Optional[str]]:
        """The exit status the trailer carries, and a note when the status needs qualifying. It also
        adopts the working directory the command ended in."""
        if waited.trailer is None:
            return None, None
        exit_code, exit_note = _reported_exit(
            _parse_exit_code(waited.trailer, self._marker),
            _parse_details(waited.trailer, self._marker),
            command,
        )
        cwd = _parse_cwd(waited.trailer, self._marker)
        if cwd:
            # Git Bash reports `D:/x`. The executor and the cards use one spelling.
            self.cwd = os.path.normpath(cwd) if self._is_windows else cwd
        return exit_code, exit_note

    def interrupt_now(self) -> None:
        """The person's Stop: kill the process tree of the foreground command in flight.

        The flag is set and every foreground wait is woken, so each kills its own process and reports
        the interruption now and not at its next line. Background tasks are left alone, since they
        are fire-and-forget and `shell_task_kill` is their stop."""
        self._abort.set()
        for task in list(self._waiting):
            task.wake()

    def interrupt(self) -> None:
        """What the engine's Stop path calls (see `Executor.interrupt`)."""
        self.interrupt_now()

    def set_person_waiting(self, event: Any) -> None:
        self._person_waiting = event

    def wake_waiters(self) -> None:
        """The person steered: every foreground wait reads `person_waiting` now."""
        for task in list(self._waiting):
            task.wake()

    def _managed_preamble(self, shell: Optional[Shell] = None) -> str:
        """The `REEVE_*` exports for the command about to run.

        It never raises. This is context, and a command that would have worked must not be lost
        because a fact about the session could not be computed. A failure here costs the variables
        and nothing else."""
        if self._shell_env is None:
            return ""
        try:
            return export_preamble(
                self._shell_env.resolve(),
                powershell=(shell or self._shell_for(None)).is_powershell,
            )
        except Exception:  # noqa: BLE001 - see the docstring
            return ""

    def _managed_env(self) -> dict[str, str]:
        """The process environment for a command, managed values included.

        Each command has its own process, so the values ride in its environment and are not exported
        into a shell. They are resolved at launch, so the command sees the session as it stands when
        it starts."""
        if self._shell_env is None:
            return self._env
        try:
            return {**self._env, **self._shell_env.resolve()}
        except Exception:  # noqa: BLE001
            return self._env

    def close(self) -> None:
        """Dispose the executor by interrupting whatever is in flight.

        There is no shell process to kill any more, since each command has its own, so this is the
        same call `interrupt_now` makes. Background tasks are not touched. The session manager kills
        process trees at shutdown, and a task the model started may still be writing a log somebody
        wants."""
        self.interrupt_now()
