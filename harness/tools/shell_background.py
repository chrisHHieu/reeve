"""Background shell tasks: the detached command and the registry a session holds.

Split out of `shell.py` when the wake work pushed that module past its baselined growth
allowance. The unit is coherent on its own: a command that runs
detached, its process lifecycle (`kill`, the finish stamp), and the mixin that hands its
output to `shell_task_output` and its completion to a waking session.

What a task REPORTS (the reader that drains its output, the stream a jobs panel follows) is
in `shell_output_stream.py` and the watcher that ends both in `shell_jobs.py`, split out when
the stream work pushed THIS module at the same 400-line ceiling.

`ShellBackgroundMixin` reads the executor's attributes (`cwd`, `max_output_chars`, `_spill`,
`_managed_env`, the registries and hooks) rather than owning them — the executor is still
the one object a session holds.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from typing import Any, Callable, Optional

from .retention import retain
from .shell_cmd import command_line as cmd_command_line, compose as cmd_compose
from .shell_jobs import job_rows, job_tail, kill_job, wait_for_end, watch_background
from .shell_kinds import Shell, local_shells, pick_default
from .shell_output_read import whole_output
from .shell_watch import LineWatch, started
from .shell_output_stream import TaskOutput, discard_files, job_output_stream
from harness.platform.spawn import run as spawn_run, spawn
from harness.platform.win_job import ProcessJob
#: Local, not imported from shell.py: the dependency must stay one-way (shell -> this).
_IS_WINDOWS = os.name == "nt"

#: PowerShell 5.1 speaks the OEM code page (437 on a US box) on redirected handles — both
#: ways. A Vietnamese command written to the shell's stdin died in our own `write`
#: (cp1252 UnicodeEncodeError, 4 sessions) and, once the write was allowed, the shell
#: compared mojibake: `-match 'ăn uống'` against a UTF-8 file returned false with no error.
#: Setting `[Console]::OutputEncoding` fixes BOTH directions (PS 5.1 ties the redirected
#: stdin decode to it). `chcp 65001` is not needed; `[Console]::InputEncoding` alone is not
#: enough — measured, both. Every spawned script carries it, foreground or background.
_UTF8_CONSOLE = "[Console]::OutputEncoding=[Text.Encoding]::UTF8"


class BackgroundTask:
    """One command in its own process: a reader thread draining output into a buffer, an
    incremental-read cursor, and (for a background one) a watcher that reports completion.

    The SAME class serves the foreground path since the one-process-per-command change:
    a foreground `run()` creates a task, waits for the marker in its buffer,
    and — if the budget expires first — simply stops waiting and hands the still-running
    process to the background registry. There is no second runner and no shell to keep in
    sync, which is what makes "auto-background instead of kill" a two-line decision rather
    than a lifecycle.

    The buffer and the reader belong to `TaskOutput` (`shell_output_stream`) and the watcher
    to `watch_background` (`shell_jobs`): this class is the process half, and
    routes what its reader hands it (`on_output`) to whoever is watching.
    """

    def __init__(
        self,
        task_id: str,
        command: str,
        cwd: str,
        env: dict[str, str],
        generation: int = 0,
        utf8_preamble: str = _UTF8_CONSOLE,
        script: Optional[str] = None,
        shell: Optional[Shell] = None,
        cleanup: Optional[list[str]] = None,
        on_output: Optional[Callable[[str, bool], None]] = None,
        watch: Optional[LineWatch] = None,
    ):
        self.id = task_id
        #: `notify_when` (shell_watch): fed every line by the reader started below, so it is set first.
        self.watch = watch
        #: Where the command was started. Kept, although the process is already gone,
        #: because the executor's `cwd` moves with `cd` and can move again before anyone
        #: reads the jobs panel — this is the folder the command actually ran in.
        self.cwd = cwd
        #: Files this process needs and nobody else will delete (a cmd batch script): removed
        #: when its output ends, which is when the process is done with them. Built here, not
        #: in `TaskOutput`, because the script composed just below can still add to it.
        self._cleanup = list(cleanup or [])
        #: When the command was accepted, on the stamp clock the rest of the server uses
        #: (`time.time()`): the jobs panel renders an epoch second and computes elapsed
        #: time from it, and a monotonic reading is not a thing a client can hold.
        self.started_at = time.time()
        #: Set by the FIRST observer to see the process leave — the watcher thread, a
        #: `kill()`, or a panel read. `None` while it runs; `note_finished` never moves it,
        #: so a duration cannot grow after the process is gone.
        self.ended_at: Optional[float] = None
        #: What the model called: shown in the result, the notification and the card.
        self.command = command
        #: What actually runs. A foreground command appends its exit/cwd trailer, so the
        #: script differs from the display text; a background one runs the command as-is.
        self.script = script if script is not None else command
        #: The owner's revert generation when this task started (see
        #: `Executor.set_generation_provider`): a completion stamped before an `/undo` is
        #: dropped by the manager instead of reopening a session over undone work.
        self.generation = generation
        #: Set by `kill()`: a task the model stopped on purpose must not also report a
        #: completion — the notification exists for work nobody is watching, and this one
        #: was just watched to death.
        self.killed = False
        #: The dialect this command was written in; the OS's own shell by default.
        shells = local_shells()
        self.shell = shell or shells[pick_default(shells)]
        if self.shell.is_cmd:
            if script is None:  # a background task: the command alone, no trailer to read
                script, made = cmd_compose(command, None)
                self._cleanup += made
                self.script = script
            argv = cmd_command_line(self.shell.path, self.script)
        elif self.shell.is_powershell:
            if self.shell.is_modern_powershell:
                # 7 colours its error text with ANSI escapes even into a pipe (measured, 7.6):
                # the model would read `ESC[31;1m` around every message.
                utf8_preamble += "; if ($PSStyle) { $PSStyle.OutputRendering = 'PlainText' }"
            argv = [
                self.shell.path,
                "-NoProfile",
                "-Command",
                f"{utf8_preamble}; {self.script}",
            ]
        else:
            argv = [self.shell.path, "-c", self.script]
        try:
            self.proc = spawn(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=cwd,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=env,
            )
        except BaseException:
            discard_files(self._cleanup)  # no process will ever read them
            raise
        # Structural containment on Windows: a build the script spawns dies with the
        # job handle even if `kill()` never runs (Reeve crashed). Best-effort.
        self._job = ProcessJob.attach(self.proc.pid)
        #: Where output goes AS IT IS READ — the jobs panel's live channel, set by the owner
        #: at spawn; None = nobody is streaming. Public because a task adopted from the
        #: foreground attaches its stream later, once a panel exists for it.
        self.on_output = on_output
        # The reader thread starts HERE, sink in place: output read before a listener attached
        # would never be streamed. `chars_read`/`tail` are this buffer's, as they always were.
        self._output = TaskOutput(
            self.proc.stdout, on_chunk=self._emit, cleanup=self._cleanup
        )

    def _emit(self, text: str, ended: bool) -> None:
        """Hand newly read text to the live stream, best-effort (called on the reader thread).

        A sink that raises — a bug in the panel's channel, a server that went away — must
        never stop the reading: this buffer is what the model's `shell_task_output` and the
        jobs panel's tail route read, and the only record of what the command printed.
        """
        if self.watch is not None:
            self.watch.feed(text, self)
        listener = self.on_output
        if listener is None:
            return
        try:
            listener(text, ended)
        except Exception:  # noqa: BLE001 — a display sink never breaks the job
            pass

    def read_new(self) -> str:
        """New text since the last read — `TaskOutput.read_new`, consuming its cursor."""
        return self._output.read_new()

    def all_lines(self) -> list[str]:
        """Every line so far, the cursor untouched — `TaskOutput.lines`."""
        return self._output.lines()

    def drain_new(self) -> list[str]:
        """New complete lines since the last drain — `TaskOutput.drain_new` (see it for why
        the foreground wait and `shell_task_output` share one cursor)."""
        return self._output.drain_new()

    @property
    def done(self) -> bool:
        return self.proc.poll() is not None

    @property
    def output_ended(self) -> bool:
        """The pipe has closed and every line is in the buffer (see `shell_wait.py`)."""
        return self._output.ended

    def wait_output(self, timeout: float) -> None:
        """Block until new output, the end of the pipe, a `wake()`, or `timeout` seconds."""
        self._output.wait(timeout)

    def wake(self) -> None:
        self._output.wake()

    @property
    def exit_code(self) -> Optional[int]:
        """The command's exit status, or None while it runs.

        `poll()`, not the raw `returncode`: a process that left on its own keeps
        `returncode is None` until somebody reaps it, which would read as "still running".
        """
        return self.proc.poll()

    def note_finished(self) -> None:
        """Latch the finish stamp, once, from whoever saw the process leave first.

        The watcher thread, a kill and a panel read all arrive here for the same exit; the
        first stamp wins, so a duration cannot keep growing after the process is gone.
        """
        if self.ended_at is None:
            self.ended_at = time.time()

    @property
    def shell_name(self) -> str:
        """The dialect it was written in: `Shell.kind`, the name a model asks for it by."""
        return self.shell.kind

    @property
    def chars_read(self) -> int:
        """Every character read so far — what `tail()` is measured against, without joining."""
        return self._output.chars_read

    @property
    def last_line(self) -> str:
        """The last line that said something, as read (the roster's one-line look)."""
        return self._output.last_line

    def tail(self, chars: int) -> str:
        """The last `chars` characters of everything read, WITHOUT consuming the cursor.

        The completion notification wants a preview and the panel polls for the end of the
        text; `shell_task_output` still owns the incremental read, and a reader that ate the
        buffer would make the model's next look at the task show nothing.
        """
        return self._output.tail(chars)

    def kill(self) -> None:
        self.killed = True
        if self.proc.poll() is not None:
            self.note_finished()  # already over: there is nothing to terminate
            return
        if _IS_WINDOWS:
            if self._job is not None:
                self._job.terminate()
            try:
                spawn_run(
                    ["taskkill", "/F", "/T", "/PID", str(self.proc.pid)],
                    capture_output=True,
                )
            except (OSError, subprocess.SubprocessError):
                pass
            return
        try:
            os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            pass


class ShellBackgroundMixin:
    """`run_background` / `background_output` / `background_kill` + the completion watcher.

    Mixed into `LocalExecutor`; every attribute it touches is set there. A world that
    cannot run background tasks (the `Executor` defaults) simply does not mix this in.
    """

    # -- background tasks ---------------------------------------------------------
    def run_background(
        self, command: str, shell: Optional[str] = None, notify_when: Optional[str] = None
    ) -> dict[str, Any]:
        try:
            chosen = self._shell_for(shell)
        except ValueError as exc:
            return {"error": str(exc)}
        if notify_when and (refused := LineWatch.compile_error(notify_when)):
            return {"error": refused}
        with self._bg_lock:
            self._bg_counter += 1
            task_id = f"bg-{self._bg_counter}"
        generation = self._generation_now()
        try:
            task = BackgroundTask(
                task_id,
                command,
                self.cwd,
                self._managed_env(),
                generation=generation,
                shell=chosen,
                on_output=job_output_stream(
                    self._bg_output_listener, task_id, generation=generation
                ),
                watch=LineWatch(notify_when) if notify_when else None,
            )
        except (OSError, ValueError) as exc:
            return {"error": f"failed to start background task: {exc}"}
        self._bg_tasks[task_id] = task
        self._watch_background(task)
        return {**started(task_id, command, notify_when), "shell": chosen.kind}  # for the card

    def set_background_listener(self, listener: Any) -> None:
        """Where a finished background task reports itself (see `_watch_background`)."""
        self._bg_listener = listener

    def set_background_output_listener(self, listener: Any) -> None:
        """Where a RUNNING task's output goes as it is read — the jobs panel's live channel.
        Fired on a reader thread and best-effort: a display that is slow or gone never costs
        the job. The completion channel says "it finished"; this one says what it said."""
        self._bg_output_listener = listener

    def set_background_roster_listener(self, listener: Any) -> None:
        """Called with no arguments when a job joins the registry or its process ends."""
        self._bg_roster_listener = listener

    def set_generation_provider(self, provider: Any) -> None:
        """See `Executor.set_generation_provider` — asked once per task, at spawn."""
        self._bg_generation = provider

    def _generation_now(self) -> int:
        provider = self._bg_generation
        if provider is None:
            return 0
        try:
            return int(provider() or 0)
        except Exception:  # noqa: BLE001 — an unanswerable owner stamps 0 (unfenced)
            return 0

    def _watch_background(self, task: "BackgroundTask") -> None:
        """Watch one task to its end — `shell_jobs.watch_background`, where the executor's
        listeners meet: the completion that wakes the session, the live stream a task adopted
        from the foreground still has to be given, and the roster it has just joined."""
        watch_background(
            task, on_finish=self._bg_listener, on_output=self._bg_output_listener,
            on_change=self._bg_roster_listener,
        )

    def background_output(
        self,
        task_id: str,
        wait: float = 0,
        pattern: Optional[str] = None,
        from_line: Optional[int] = None,
        ignore_case: bool = False,
    ) -> dict[str, Any]:
        task = self._bg_tasks.get(task_id)
        if task is None:
            return {"error": f"unknown task: {task_id}"}
        # Up to `wait` s, less once the person waits on the step.
        early = wait_for_end(task, wait, getattr(self, "_abort", None), getattr(self, "_person_waiting", None))
        if pattern or from_line:
            # The whole output, the incremental cursor left where it was (`shell_output_read`).
            body = whole_output(task.all_lines(), pattern, from_line, ignore_case, self.max_output_chars)
        else:
            kept = retain(task.read_new(), budget=self.max_output_chars, spill=self._spill)
            body = {
                "output": kept.text,
                "truncated": kept.truncated,
                **({"omitted_lines": kept.omitted_lines} if kept.truncated else {}),
                **({"full_output": kept.spilled} if kept.spilled else {}),
            }
        exit_code = task.proc.poll()
        return {
            "task_id": task_id,
            "status": "running" if exit_code is None else "exited",
            "exit_code": exit_code,
            **body,
            **({"released": True} if early else {}),
        }

    def background_tail(self, task_id: str, chars: int) -> dict[str, Any]:
        """One job's output tail for a polling panel — the read that must not move the cursor."""
        return job_tail(self._bg_tasks.get(task_id), task_id, chars)

    def background_kill(self, task_id: str) -> dict[str, Any]:
        task = self._bg_tasks.get(task_id)
        if task is None:
            return {"error": f"unknown task: {task_id}"}
        return kill_job(task, task_id)

    def background_jobs(self) -> list[dict[str, Any]]:
        """Every shell job this executor still holds, newest first — the jobs panel's read.
        In memory only: a closed executor reports nothing rather than a stale list. Running
        and finished alike: the running one for its elapsed time and Stop, the rest for exits."""
        return job_rows(self._bg_tasks.values())

    def interrupt(self) -> None:
        self._interrupt()

