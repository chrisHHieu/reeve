"""A running shell job's output, from the pipe to the socket.

The jobs panel used to poll `GET …/jobs/output` on a one-second clock, so the client kept
re-asking for the end of text the engine already held. A followed job streams it instead —
an anchor frame, then coalesced output frames — and this module is the engine's half of that:
the panel anchors once with the tail route and appends what arrives on the session socket after
it. Nothing here is model-visible: the socket frame is a display, and `shell_task_output` reads
the same buffer through the same cursor it always did.

Two pieces, one path in and one path out:

  · `TaskOutput` is the READING half — the thread that drains the process's pipe, the lines it
    collected, and the one incremental cursor `shell_task_output` and the foreground wait share.
    Its `on_chunk` hook is where a line, the moment it is read, becomes a stream event.
  · `JobOutputStream` is the SENDING half — it coalesces those chunks into one `job_output`
    frame per window and carries the running character total a client compares against.

The end of both is `shell_jobs.watch_background`: it attaches the stream a task adopted from the
foreground never had, then reports the completion that follows the final flush.

Split out of `shell_background.py` when the stream work pushed that module past the 400-line
ceiling the architecture policy enforces. The seam is not new: reading is about the
PROCESS, streaming is about the CLIENT.
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from typing import TYPE_CHECKING, Any, Callable, Iterable, Optional

if TYPE_CHECKING:  # a runtime import would be a cycle: shell_background imports this module
    from .shell_background import BackgroundTask

#: The coalescing window, 100 ms for the same job. A chatty build must
#: not put one frame per line on the socket, and a quiet one must not make the panel wait a
#: second for a line the engine already has. The window is measured from the FIRST chunk it
#: holds (not from the last frame), which is what lets a print-and-exit job be reported by its
#: exit flush rather than by a timer that was already due.
FLUSH_WINDOW_SECONDS = 0.1

#: One frame's ceiling. A window can still be enormous — a command
#: that dumps a file prints megabytes inside one 100 ms — and a frame that size is a socket
#: write nothing asked for. A longer flush becomes SEVERAL frames, each at most this long,
#: each carrying the total it ends at: text is never dropped, and a client's arithmetic
#: (`total` minus what it has) still says exactly whether it missed anything.
MAX_FRAME_CHARS = 64 * 1024


def discard_files(paths: Iterable[str]) -> None:
    """Remove temp files nothing will read any more (a cmd batch script, once its output has
    ended — which is when the process is done with them). Best-effort: a file already gone is
    not a failure."""
    for path in paths:
        try:
            os.unlink(path)
        except OSError:
            pass


class TaskOutput:
    """One task's output buffer and the thread that fills it.

    The buffer is the list of lines read so far, never joined: `tail()` is measured against a
    running count (`chars_read`) rather than a re-join of everything, and an incremental read
    joins only the span it has not seen. One cursor serves both incremental readers — the
    model's `shell_task_output` and the foreground wait — and they are never interleaved.

    It is also the one place that knows when the output is OVER: `ended` turns true when the
    pipe closes, after the last line is in the buffer, and `wait()` blocks until a line
    arrives or that happens. A waiter that asks the PROCESS instead (`proc.poll()`) is
    asking the wrong thing: the process leaves first and its last lines arrive after.

    `on_chunk(text, ended)` is called from the reader thread for every line as it is read, and
    once with `ended=True` when the pipe closes. Best-effort by construction: this is the
    panel's channel, and a display that raises (or a socket that is gone) must not stop the
    job from being read — the buffer is what the model and the tail route read.
    """

    def __init__(
        self,
        stdout: Any,
        *,
        on_chunk: Optional[Callable[[str, bool], None]] = None,
        cleanup: Optional[list[str]] = None,
    ) -> None:
        self._stdout = stdout
        self._on_chunk = on_chunk
        self._cleanup = list(cleanup or [])
        self._lock = threading.Lock()
        #: Notified for every line read and once at the end of the pipe (`wait`).
        self._changed = threading.Condition(self._lock)
        self._lines: list[str] = []
        self._cursor = 0
        #: Kept as each line is read, so the jobs roster reads two fields instead of
        #: joining the buffer: every character so far, and the last line that said something.
        self._chars = 0
        self.last_line = ""
        #: True once the pipe has closed. Set BEFORE the closing chunk goes out, so whoever
        #: acts on that chunk (the manager dropping the job's observers) can rely on a later
        #: reader of this flag seeing it. The pipe closes before `proc.poll()` reports an
        #: exit, so "the process is done" is not the same edge.
        self.ended = False
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def _read_loop(self) -> None:
        try:
            if self._stdout is not None:
                for line in self._stdout:
                    with self._changed:
                        self._lines.append(line)
                        self._chars += len(line)
                        if line.strip():
                            self.last_line = line
                        self._changed.notify_all()
                    self._chunk(line, ended=False)
        finally:
            # The end of the pipe is the only moment that knows no more text can arrive, so
            # the stream's final flush belongs here — a short-lived job reports itself here
            # and nowhere else.
            with self._changed:
                self.ended = True
                self._changed.notify_all()
            self._chunk("", ended=True)
            self._close()

    def _chunk(self, text: str, *, ended: bool) -> None:
        listener = self._on_chunk
        if listener is None:
            return
        try:
            listener(text, ended)
        except Exception:  # noqa: BLE001 — a display sink never breaks the reading
            pass

    def _close(self) -> None:
        try:
            discard_files(self._cleanup)
        except Exception:  # noqa: BLE001 — closing reports nothing, least of all a failure
            pass

    def wait(self, timeout: float) -> None:
        """Block until there is a line nobody has drained, the pipe has closed, `wake()` is
        called, or `timeout` seconds pass. Returns at once when one already holds."""
        with self._changed:
            if self._cursor >= len(self._lines) and not self.ended:
                self._changed.wait(max(0.0, timeout))

    def wake(self) -> None:
        """Release a `wait()` early: a Stop has to be noticed now, not at the next line."""
        with self._changed:
            self._changed.notify_all()

    def read_new(self) -> str:
        """New text since the last read, consuming the cursor."""
        with self._lock:
            new = "".join(self._lines[self._cursor :])
            self._cursor = len(self._lines)
        return new

    def drain_new(self) -> list[str]:
        """New complete lines since the last drain, as a list.

        The foreground wait needs lines (it scans each one for the trailer); the model's
        `shell_task_output` needs text. One cursor, two readers — never both at once.
        """
        with self._lock:
            new = self._lines[self._cursor :]
            self._cursor = len(self._lines)
        return list(new)

    def lines(self) -> list[str]:
        """Every line read so far, the cursor untouched (`shell_task_output`'s search)."""
        with self._lock:
            return list(self._lines)

    @property
    def chars_read(self) -> int:
        """Every character read so far — what `tail()` is measured against, without joining."""
        return self._chars

    def tail(self, chars: int) -> str:
        """The last `chars` characters of everything read, WITHOUT consuming the cursor.

        The completion notification wants a preview and the jobs panel polls for the end of
        the text; `shell_task_output` still owns the incremental read, and a reader that ate
        the buffer would make the model's next look at the task show nothing.
        """
        with self._lock:
            text = "".join(self._lines)
        return text[-chars:] if chars > 0 else ""


class JobOutputStream:
    """One job's output as a client receives it: coalesced `job_output` frames.

    A window OPENS with the first chunk held and closes `window` seconds later, when the
    timer fires; a whole flush that would be longer than one frame is split (see
    `MAX_FRAME_CHARS`). That is the followed-job shape — nothing is sent until the first
    line of a burst is 100 ms old — and it is what makes a short-lived job report itself at
    all: a command that prints and exits sends its one frame from the END flush, carrying
    `ended=True`, instead of a frame per line.

    `ended=True` therefore rides the frame that carried the job's last text. A job that fell
    silent more than a window before it exited has no such frame: nothing is left to send, and
    an empty one would be noise (`feed` says so at the call site). The row's finish is the
    jobs list's `status` either way — the authoritative read — which is why the terminal flag
    is a convenience and not the signal a panel has to wait for.

    Locked, and the listener is called UNDER the lock so frames cannot be reordered between
    the reader thread and a timer. That is safe because the production listener only hands the
    frame to the server loop (`run_coroutine_threadsafe`) and returns: a slow socket queues on
    the loop, it never stalls the reader.

    The window, the clock and the timer are injectable, so coalescing is testable without
    waiting: a unit test drives `feed` on a frozen clock with a scheduler that never runs,
    which is exactly "everything printed inside one window". The defaults are the real ones.
    """

    def __init__(
        self,
        task_id: str,
        emit: Callable[[dict[str, Any]], None],
        *,
        generation: int = 0,
        start_total: int = 0,
        window: Optional[float] = None,
        clock: Callable[[], float] = time.monotonic,
        schedule: Optional[Callable[[float, Callable[[], None]], Any]] = None,
        max_frame: int = MAX_FRAME_CHARS,
        on_close: Optional[Callable[[int], None]] = None,
    ) -> None:
        self._task_id = task_id
        self._emit = emit
        #: Called ONCE with the final total, after the last frame, when the pipe has closed.
        #: It is how a listener learns that nothing more can come — for a job that
        #: exited, was killed, or fell silent long before either.
        self._on_close = on_close
        self._generation = int(generation)
        # Read from the module at construction, not bound as a default, so a test can widen
        # the one knob and prove WHICH flush sent a frame (see the exit-flush test).
        self._window = max(
            0.0, float(FLUSH_WINDOW_SECONDS if window is None else window)
        )
        self._clock = clock
        self._schedule = schedule or _start_timer
        self._max_frame = max(1, int(max_frame))
        self._lock = threading.Lock()
        self._pending: list[str] = []
        #: When the current window opened — the clock reading of the first chunk held since
        #: the last frame. None = nothing is held, so no window is running.
        self._pending_since: Optional[float] = None
        #: `start_total` is what a stream attached to an ALREADY READ task counts from — a
        #: foreground command adopted into the registry read its first lines with nobody
        #: streaming them, and its `total` must stay the job's absolute count.
        self._total = max(0, int(start_total))
        self._ended = False
        self._timer: Any = None

    def feed(self, text: str = "", ended: bool = False) -> None:
        """Newly read text, or `ended=True` when no more can arrive. Never raises."""
        with self._lock:
            if self._ended:
                return  # a closed stream takes nothing more, however late a read lands
            if text:
                if not self._pending:
                    self._pending_since = self._clock()
                self._pending.append(text)
                self._total += len(text)
            if ended:
                self._ended = True
            if self._pending:
                held = 0.0
                if self._pending_since is not None:
                    held = self._clock() - self._pending_since
                if ended or self._window <= 0.0 or held >= self._window:
                    self._flush_locked(ended=ended)
                elif self._timer is None:
                    self._arm_timer_locked(held)
            # Nothing pending sends nothing: an empty frame is noise on somebody's socket, and
            # the row's finish is the jobs list's `status`, the authoritative read.
            if ended:
                self._close_locked()

    def _close_locked(self) -> None:
        """Tell the owner the stream is over — after the last frame, under the same lock, so
        "closed" can never overtake the text it closes."""
        closer, self._on_close = self._on_close, None
        if closer is None:
            return
        try:
            closer(self._total)
        except Exception:  # noqa: BLE001 — the panel's channel never breaks the job
            pass

    def _flush_locked(self, *, ended: bool) -> None:
        text = "".join(self._pending)
        self._pending = []
        self._pending_since = None
        self._cancel_timer_locked()
        if not text:
            return
        # `total` is the job's absolute character count: the text in hand ENDS at
        # `self._total`, so the chunks before the last one end earlier and a client can place
        # every frame it receives.
        upto = self._total - len(text)
        limit = self._max_frame
        for start in range(0, len(text), limit):
            chunk = text[start : start + limit]
            upto += len(chunk)
            self._deliver(
                {
                    "task_id": self._task_id,
                    "chunk": chunk,
                    "total": upto,
                    "ended": bool(ended and upto >= self._total),
                    "generation": self._generation,
                }
            )

    def _deliver(self, info: dict[str, Any]) -> None:
        try:
            self._emit(info)
        except Exception:  # noqa: BLE001 — the panel's channel never breaks the job
            pass

    def _arm_timer_locked(self, held: float) -> None:
        self._timer = self._schedule(max(0.0, self._window - held), self._on_timer)

    def _on_timer(self) -> None:
        """The trailing edge: everything held since the window opened, at its close."""
        with self._lock:
            self._timer = None
            if self._ended or not self._pending:
                return
            self._flush_locked(ended=False)

    def _cancel_timer_locked(self) -> None:
        timer, self._timer = self._timer, None
        if timer is None:
            return
        try:
            timer.cancel()
        except Exception:  # noqa: BLE001 — a timer already gone is not a failure
            pass


def _start_timer(delay: float, callback: Callable[[], None]) -> threading.Timer:
    """The default scheduler: a DAEMON timer, so a job's trailing flush cannot hold the
    process open after everything else has stopped."""
    timer = threading.Timer(max(0.0, delay), callback)
    timer.daemon = True
    timer.start()
    return timer


def job_output_stream(
    listener: Optional[Callable[[dict[str, Any]], None]],
    task_id: str,
    *,
    generation: int = 0,
    start_total: int = 0,
) -> Optional[Callable[[str, bool], None]]:
    """A callable taking `(text, ended)` for one job, or None when nobody is listening.

    The narrowest thing the reader needs — the stream object stays private to this function,
    and the bound `feed` keeps it alive. Built at SPAWN rather than attached afterwards: the
    reader thread starts inside `BackgroundTask.__init__`, and a sink attached a line later
    could miss the first chunk, which is the whole of a short-lived job's output.
    """
    if listener is None:
        return None

    def closed(total: int) -> None:
        # Not a frame (its chunk is empty, and the manager sends none for it): the news that
        # this job's stream is over, on the same channel and thread as its last text.
        listener(
            {
                "task_id": task_id,
                "chunk": "",
                "total": total,
                "ended": True,
                "closed": True,
                "generation": generation,
            }
        )

    return JobOutputStream(
        task_id, listener, generation=generation, start_total=start_total, on_close=closed
    ).feed


__all__ = [
    "FLUSH_WINDOW_SECONDS", "MAX_FRAME_CHARS", "JobOutputStream", "TaskOutput",
    "discard_files", "job_output_stream", "_start_timer",
]
