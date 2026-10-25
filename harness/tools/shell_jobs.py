"""The jobs panel's reads of shell tasks: one plain row per background command, and the
output tail the panel polls for one of them.

Also the WATCHER that ends a job (`watch_background`): it attaches the output stream and
reports the completion, and it moved here when `shell_output_stream.py` reached the same
ceiling.

Split out of `shell_background.py`, which sits at the 400-line ceiling the architecture
policy enforces. The split is the natural one: the FACTS (when it started, when it ended,
its exit code, which dialect it was written in, how much it has read) stay with
`BackgroundTask`, and the SHAPE a client renders is a view, here.

Read-only by construction. The LIST carries no output on purpose: `tail()` joins a task's
whole buffer, the panel polls that list on a timer, and a chatty build would be re-joined on
every poll for a field nothing draws. A panel that wants the text asks for ONE job's tail
(`GET …/jobs/output`) and gets a bounded slice of the end. Both reads go through the
non-consuming `tail()` — the model's `shell_task_output` owns that buffer's incremental
cursor, and a panel read that ate it would make the model's next look at the task show
nothing.
"""

from __future__ import annotations

import time

import subprocess
import threading
from typing import TYPE_CHECKING, Any, Callable, Iterable, Optional

from .shell_output_stream import job_output_stream

if TYPE_CHECKING:  # a runtime import would be a cycle: shell_background imports this module
    from .shell_background import BackgroundTask

#: What a `…/jobs/output` read returns when no tail is named, and the most it will return
#: however large a number it is asked for. The ceiling is what keeps a chatty build from
#: being copied into a response body on every poll; the floor is 0, a caller asking for the
#: metadata alone.
DEFAULT_TAIL_CHARS = 4_000
MAX_TAIL_CHARS = 20_000
#: The most of one output line a roster row carries. A row is one line of a panel; a minified
#: bundle printed on a single line must not ride every roster frame whole.
LAST_LINE_CHARS = 200


def job_row(task: "BackgroundTask") -> dict[str, Any]:
    """One job as a plain dict: the running one gives the panel its elapsed time and the id
    its Stop control posts back; the finished ones give their exit code and how long.

    `killed` is carried because the exit code cannot say it: a job stopped by `kill()` is 1
    on Windows (taskkill) and a signal on POSIX, while a job that had already finished
    reports its own status — most often a 0 that would read as "it worked".

    `last_line` and `output_chars` are what a row shows WITHOUT opening the job:
    the last thing it printed and how much it has printed. Both are fields the reader keeps
    as it reads, so a roster costs no join of anybody's buffer.
    """
    finished = task.done
    if finished:
        task.note_finished()
    duration_ms: int | None = None
    if finished and task.ended_at is not None:
        # max(): both stamps are wall-clock, and a clock that steps backwards between them
        # must not turn a finished job into a negative duration.
        duration_ms = int(max(0.0, task.ended_at - task.started_at) * 1000)
    return {
        "id": task.id,
        "command": task.command,
        "cwd": task.cwd,
        "shell": task.shell_name,
        "status": "finished" if finished else "running",
        "started_at": task.started_at,
        "ended_at": task.ended_at,
        "exit_code": task.exit_code,
        "duration_ms": duration_ms,
        "killed": task.killed,
        "output_chars": task.chars_read,
        "last_line": last_line(task.last_line),
    }


def last_line(raw: str) -> str:
    """One line of a job's output, as a row can show it: what a terminal would be showing on
    that line (the text after the last carriage return — a progress bar rewrites itself),
    without its line ending, cut at `LAST_LINE_CHARS`. Colour codes are left to the client,
    which already has the parser."""
    shown = raw.rstrip("\r\n").rsplit("\r", 1)[-1].strip()
    return shown if len(shown) <= LAST_LINE_CHARS else shown[: LAST_LINE_CHARS - 1] + "…"


def job_rows(tasks: Iterable["BackgroundTask"]) -> list[dict[str, Any]]:
    """Every job, newest first.

    Sorted by the task's own start stamp; two commands launched in the same clock tick fall
    back to the registry's order — the order they were accepted in, `bg-N` minted under the
    lock — so the list never reshuffles between two polls.
    """
    ordered = sorted(
        enumerate(tasks),
        key=lambda pair: (pair[1].started_at, pair[0]),
        reverse=True,
    )
    return [job_row(task) for _index, task in ordered]


def clamp_tail(value: Any) -> int:
    """`tail` as an int in `0..MAX_TAIL_CHARS`, from whatever a caller carried.

    Three outcomes, and a client can tell which one it got: a number in range is itself, a
    number outside it takes the nearer bound, and text that is not a number at all takes the
    default — the alternatives (0, or the ceiling) would silently draw a panel nothing or
    everything. Never raises: a poll must not be able to 500 over a query string.
    """
    try:
        chars = int(str(value).strip() or DEFAULT_TAIL_CHARS)
    except (TypeError, ValueError):
        return DEFAULT_TAIL_CHARS
    return max(0, min(chars, MAX_TAIL_CHARS))


def job_tail(
    task: "BackgroundTask | None", task_id: str, chars: int
) -> dict[str, Any]:
    """One job's output tail: the last `chars` characters it has read, and whether that cut
    anything.

    Every key is always present, so a panel binds one shape whether the job runs or is over.
    The text comes from `BackgroundTask.tail`, which does NOT move the reading cursor.

    `truncated` and `total_chars` are measured against the buffer's own character count, so
    the answer is exact rather than guessed from the returned text (a tail that fills `chars`
    exactly is indistinguishable from a cut one). The count is the reader thread's own lines
    summed, not a second join of them.

    `chars` is clamped to `0..MAX_TAIL_CHARS` here as well as where a query string is parsed:
    this shape is also built for an executor called directly, and the bound is a promise the
    shape makes.

    A task that is not here is an ERROR, matching the Stop route's vocabulary for the same
    condition: the panel named an id off its own list, and a sentence tells the user that
    list is stale where an empty read would look like silence.
    """
    if task is None:
        return {"error": f"unknown task: {task_id}"}
    chars = clamp_tail(chars)
    exit_code = task.exit_code
    output = task.tail(chars)
    total = task.chars_read
    return {
        "id": task.id,
        "status": "running" if exit_code is None else "finished",
        "exit_code": exit_code,
        "output": output,
        "truncated": len(output) < total,
        "total_chars": total,
    }


def watch_background(
    task: "BackgroundTask",
    *,
    on_finish: Optional[Callable[[dict[str, Any]], None]],
    on_output: Optional[Callable[[dict[str, Any]], None]],
    on_change: Optional[Callable[[], None]] = None,
) -> None:
    """Watch one background task: attach its stream, then report its completion.

    This is the END of the path the rest of this module walks — the moment the reader's final
    flush is followed by the news that the job is over — which is why the two live here
    together: a task ADOPTED from the foreground read its first lines with nobody
    streaming them, so its stream starts HERE, counting from `chars_read`; the client still
    sees the job's absolute total and can tell it missed the head.

    The wait is a thread rather than the reader loop: the reader ends when stdout closes,
    which also happens for a task whose pipes were never drained, and the completion must be
    reported for every finished task exactly once. A listener that raises is swallowed — the
    command already ran, and its notification is the last thing that should be able to fail.

    `on_change()` says the ROSTER changed: once here, where a task joins the
    registry by either door (started in the background, or adopted from the foreground), and
    once when its process is gone — however it went. A killed task reports no completion, but
    it did leave the list of what is running, and a panel is told that the same way.
    """
    if task.on_output is None:
        task.on_output = job_output_stream(
            on_output, task.id, generation=task.generation, start_total=task.chars_read
        )

    def _changed() -> None:
        if on_change is None:
            return
        try:
            on_change()
        except Exception:  # noqa: BLE001 — a display channel never breaks the job
            pass

    _changed()
    watch = getattr(task, "watch", None)
    if watch is not None and not task.killed:
        # `notify_when`: the first matching line goes down the completion's channel.
        watch.attach(on_finish)

    def _watch() -> None:
        try:
            task.proc.wait()
        except (OSError, subprocess.SubprocessError):
            return
        # Stamped before the listener test below: a task the model killed, or one on an
        # executor nobody listens to, still finished, and the panel reads that stamp.
        task.note_finished()
        _changed()
        if task.killed or on_finish is None:
            return
        try:
            on_finish(
                {
                    "task_id": task.id,
                    "command": task.command,
                    "exit_code": task.proc.returncode,
                    "output": task.tail(2_000),
                    "generation": task.generation,
                    # Whether the line `notify_when` waited for ever came: an end without it
                    # is the news (the server died before it was ready).
                    **(
                        {"pattern": watch.pattern, "matched": watch.fired}
                        if watch is not None
                        else {}
                    ),
                }
            )
        except Exception:  # noqa: BLE001 — never let a notification break anything
            pass

    threading.Thread(target=_watch, name=f"bg-watch-{task.id}", daemon=True).start()


#: The longest `shell_task_output` waits for a task to end before answering.
MAX_OUTPUT_WAIT_SECONDS = 120


def wait_for_end(task: Any, wait: Any, abort: Any = None, released: Any = None) -> bool:
    """Wait up to `wait` seconds (capped) for `task` to end; return early on a Stop, or
    `RELEASE_GRACE_SECONDS` after the person typed into the turn (`released`, the engine's
    `person_waiting`). True when it returned early for the person, who waits on this step.

    Measured 2026-10-06: models that wanted a task's result asked `shell_task_output` again
    and again, a step each (runs of 13, 9 and 5; 7.8 s apart at the median, each re-reading
    the whole conversation). Waiting here makes that one step. A missing or unreadable value
    waits for nothing."""
    from .shell_wait import RELEASE_GRACE_SECONDS

    try:
        seconds = max(0.0, min(float(wait or 0), MAX_OUTPUT_WAIT_SECONDS))
    except (TypeError, ValueError):
        return False
    deadline = time.monotonic() + seconds
    cut = False
    while seconds and not task.done and time.monotonic() < deadline:
        if abort is not None and abort.is_set():
            return False
        if not cut and released is not None and released.is_set():
            deadline, cut = min(deadline, time.monotonic() + RELEASE_GRACE_SECONDS), True
        time.sleep(0.2)
    return cut and not task.done


def kill_job(task: "BackgroundTask", task_id: str) -> dict[str, Any]:
    """Stop one background job, or say it had already finished (`background_kill`)."""
    # Asked BEFORE the kill, because the kill destroys the evidence. Nothing is terminated for a
    # job that is already over (there is no process left, and the exit code on record is the
    # command's own); it is still reported, as a no-op.
    already_finished = task.done
    if already_finished:
        task.note_finished()
    else:
        task.kill()
        try:
            task.proc.wait(timeout=5)
        except (subprocess.TimeoutExpired, OSError):
            pass
        if task.done:
            task.note_finished()
    stopped = task.proc.poll()
    return {
        "task_id": task_id,
        "status": "running" if stopped is None else "finished" if already_finished else "killed",
        "exit_code": stopped,
        "already_finished": already_finished,
    }
