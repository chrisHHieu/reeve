"""SessionJobsMixin — the GUI's background-jobs panel, read from the live executor.

The engine already owns this state: the executor's `_bg_tasks` holds one `BackgroundTask`
per detached command, each with its command, cwd, dialect, start stamp and exit code
(`harness/tools/shell_background.py`). Nothing exposed it to a client, so the panel had no
honest source and would have had to guess from the transcript.

Three CALLS, all plain dicts and none able to raise: a list for the panel, the tail of ONE
job's output for a panel that opens it, and a Stop for the running job. Reads of in-memory
state — nothing here reaches a provider, so nothing here writes a session-log row.

One PUSH rides beside them: `_shell_output` puts each job's new text on its session socket as
a `job_output` frame, so a panel that has anchored with the tail route appends instead of
re-reading the tail on a one-second clock (a followed job's stream). It is
display-only for the same reason the reads are — no session-log row, and nothing the model
sees changes.

That push is GATED, because a frame nobody renders is work nobody asked for: a client states
which job it is looking at with `observe_background_job` (the `…/jobs/observe` route) and the
frames only exist while that count is above zero. The gate is one dict lookup — see
`_shell_output` for what it costs and why. `observe_background_job` is REQUEST-SHAPED, not
connection-shaped, so a count says "two panels", not "panel 1 and panel 2": a client that
disappears without releasing keeps the stream for the other watchers, and the count is dropped
anyway the moment the job settles.

The LIST is pushed too, by `jobs_roster.JobsRosterMixin`: a `jobs` frame when a job
joins the roster, ends, or prints. `_shell_output` is where a job's printing is noticed, so it
tells the roster from here; the frame itself is built there.

It lives in its own mixin rather than beside the artifacts/files reads because
`manager/artifacts.py` is baselined at 622 lines with a 10% growth allowance: three methods
there would leave the file five lines from failing every later change.
"""
from __future__ import annotations

import asyncio
from typing import Any

from harness.tools.shell_jobs import clamp_tail


class SessionJobsMixin:
    # -- background shell jobs (the jobs panel) ----------------------------------

    def _shell_output(self, session_id: str, info: dict[str, Any]) -> None:
        """One job's new output: put a `job_output` frame on this session's socket.

        Called from the EXECUTOR's reader thread (or its coalescer's timer), so it only hands
        the frame to the manager loop — `_shell_finished`'s discipline, and
        `_subagent_metadata_emitter`'s before it: broadcasting belongs to the loop, and a
        frame that cannot be delivered must never fail the job. Nothing here is awaited by
        the reader, so a slow client queues on the loop instead of stalling a build.

        GATED FIRST: a job nobody is observing produces nothing. The check is one
        dict lookup on the manager's in-memory observation map (`_shell_observed[session]`
        then the task id), deliberately a plain lookup rather than a store read or a scan of
        the buffer — this runs per coalesced flush, per job, on the reader thread, and a
        display nobody asked for must cost a reader an `in` test and not a disk touch. A
        missing session entry short-circuits on the first `.get`, so the common case of an
        unobserved job is one dict lookup that misses.

        FENCED like the completion: an `/undo` hides the span that started the
        job, so output from before it is not news a client should be shown. Deliberately NOT
        fenced on the session still existing: that is a store read on every frame — for a
        socket nobody is holding, on a session whose clients were closed with it.
        """
        if info.get("closed"):
            # The stream is over (the pipe closed): nothing more can be sent for this job, so
            # its observers are forgotten here — AFTER its last frame, which the same reader
            # thread handed over just before this. Before the loop check: a manager with no
            # loop still must not keep the count.
            self._drop_shell_observation(session_id, str(info.get("task_id") or ""))
            # Its last line and its final count are in the buffer now, which the process's
            # own exit could not promise (it leaves first): the roster is told once more.
            self._jobs_changed(session_id)
            return
        chunk = str(info.get("chunk") or "")
        if not chunk:
            return  # an empty frame is noise, and printed nothing a row could show
        # The roster's one-line look moves with the output, at its own slower pace
        # and whether or not anybody opened the job: BEFORE the observation gate below. Not
        # fenced by an `/undo` either: the roster is the whole registry, as the route serves it.
        self._jobs_changed(session_id, printing=True)
        loop = self._loop
        if loop is None or loop.is_closed():
            return  # no loop (a bare test manager): there is no socket to put it on
        observed = self._shell_observed.get(session_id)
        if not observed or not observed.get(str(info.get("task_id") or "")):
            return  # nobody is looking at this job: the buffer is still readable
        if self._revert_generation.get(session_id, 0) > int(info.get("generation") or 0):
            return
        frame = {
            "type": "job_output",
            "data": {
                "task_id": str(info.get("task_id") or ""),
                "chunk": chunk,
                "total": int(info.get("total") or 0),
                "ended": bool(info.get("ended")),
            },
        }
        try:
            asyncio.run_coroutine_threadsafe(
                self.broadcast_session(session_id, frame), loop
            )
        except RuntimeError:
            pass  # the loop shut down between the check and the call

    def background_jobs(self, session_id: str) -> dict[str, Any]:
        """The shell jobs this session's executor still holds, newest first.

        An unknown session, or one with no engine here (the app restarted, the session was
        never opened), holds no jobs — an empty list and no error, because "this process is
        running nothing for you" is true rather than a failure. A remote-bound session's
        shell runs on the other host and has no local executor; it reads the same way.
        """
        executor = getattr(self._engines.get(session_id), "executor", None)
        snapshot = getattr(executor, "background_jobs", None)
        if not callable(snapshot):
            return {"jobs": []}
        try:
            return {"jobs": snapshot()}
        except Exception:  # noqa: BLE001 — a panel read may fail; the panel must not
            return {"jobs": []}

    def background_job_output(
        self, session_id: str, job_id: str, tail_chars: Any = None
    ) -> dict[str, Any]:
        """The tail of ONE shell job's output — the ANCHOR a jobs panel reads when it opens that job.

        A panel that follows the job live reads this ONCE and then appends the `job_output`
        frames `_shell_output` pushes (the coalesced stream) — after it has said it is watching
        with `observe_background_job`, which is what turns those frames on. This route is the
        BACKLOG and stays exactly as it is: the most recent text the job has printed, whatever
        the observation count is, so a panel shows what it missed the moment it starts
        observing. Reading it never observes anything on the panel's behalf, and `total_chars`
        here is what the frame's `total` is measured against, so the two line up by
        construction.

        WHY it is a separate read from `background_jobs`: the list is a cheap snapshot with no
        text, and the panel showing a job wants the end of what it printed. That text must
        come from the NON-consuming read (`BackgroundTask.tail`), because the model's
        `shell_task_output` owns the incremental cursor into the same buffer — a panel read
        that consumed it would leave the model's next look at the job showing nothing. A panel
        reads this once when it opens a job (and again only to re-anchor), and it is a read of
        in-memory state either way: no session-log row and nothing model-visible changes.

        As defensive as the other two: a remote-bound session (its shell runs on the other
        host), a session with no engine here, and a job id that is not this session's are all
        honest `ok: False` sentences rather than exceptions. An unknown job id is an ERROR and
        not an empty read — the panel named a specific job off its list, and a sentence tells
        the user that list is stale where a blank pane would look like silence (the Stop route
        answers the same way for the same condition).

        `tail_chars` arrives as query text and is clamped by `clamp_tail` to `0..20000`
        (default 4000): an unparseable or out-of-range value answers with the nearest honest
        tail instead of a 500, and the response shape clamps again for a direct caller.
        """
        record = self.session_store.load(session_id)
        if record is not None and getattr(record, "environment_id", None):
            return {
                "ok": False,
                "error": (
                    "This session runs on a remote host; reading its job output from here "
                    "is not available yet."
                ),
            }
        executor = getattr(self._engines.get(session_id), "executor", None)
        tailer = getattr(executor, "background_tail", None)
        if not callable(tailer):
            return {
                "ok": False,
                "error": "This session is not running here, so it has no job output to read.",
            }
        wanted = str(job_id or "").strip()
        if not wanted:
            return {"ok": False, "error": "No job was named."}
        try:
            result = tailer(wanted, clamp_tail(tail_chars))
        except Exception:  # noqa: BLE001 — a panel's poll answers, it does not raise
            return {"ok": False, "error": f"Job {wanted}'s output could not be read."}
        if "error" in result:
            return {"ok": False, "error": f"No job {wanted} in this session."}
        return {"ok": True, **result}

    def shell_observed_count(self, session_id: str, job_id: str) -> int:
        """How many clients are observing one job — 0 when nobody is, or it never existed.

        The read behind the observe route's `observing`, and the honest way for a test (or a
        future panel) to ask whether a job still has a stream, without reaching into the map.
        """
        observed = self._shell_observed.get(str(session_id or ""))
        if not observed:
            return 0
        return int(observed.get(str(job_id or "").strip(), 0))

    def observe_background_job(
        self, session_id: str, job_id: str, on: Any = True
    ) -> dict[str, Any]:
        """Start or stop STREAMING one job's output to this session — the panel's `observe`.

        The gate `_shell_output` reads: the `job_output` frames for a job exist only while its
        count here is above zero, so a client that wants them says so and a client
        that has looked away stops paying for them. Observing a job,
        which is reference-counted for the same reason: two windows (or two panels in one
        window) can watch the same job, and the one that goes away must not stop the other's
        stream. `on: false` decrements and the count CLAMPS at 0 — a release from a panel that
        never acquired, or released twice, is not an error and cannot make the count negative,
        which would silence a job that IS being watched.

        This is NOT the read. A panel shows the backlog by reading the ANCHOR route first
        (`background_job_output`, the non-consuming tail of everything the job has printed)
        and then appends the frames that follow it; observing only says "keep sending me
        those". An unobserved job therefore still keeps its whole buffer: the model's
        `shell_task_output` and the tail route read it exactly as before, and only the socket
        goes quiet.

        As defensive as its siblings, in the same vocabulary: an unknown session, one bound to
        a remote host, an unnamed id and an id that is not this session's are honest
        `ok: False` sentences rather than exceptions. A job that is already over is one more,
        with its own sentence: its count was dropped when it settled and there is nothing left
        to stream, so neither `on` nor `off` can leave a count behind for it.
        """
        record = self.session_store.load(session_id)
        if record is not None and getattr(record, "environment_id", None):
            return {
                "ok": False,
                "error": (
                    "This session runs on a remote host; observing its jobs from here is "
                    "not available yet."
                ),
            }
        executor = getattr(self._engines.get(session_id), "executor", None)
        tasks = getattr(executor, "_bg_tasks", None)
        if not isinstance(tasks, dict):
            return {
                "ok": False,
                "error": "This session is not running here, so it has no jobs to observe.",
            }
        wanted = str(job_id or "").strip()
        if not wanted:
            return {"ok": False, "error": "No job was named."}
        if wanted not in tasks:
            return {"ok": False, "error": f"No job {wanted} in this session."}
        task = tasks[wanted]
        over = {
            "ok": False,
            "error": f"Job {wanted} has finished; there is nothing left to stream.",
        }
        if getattr(task, "done", False):
            # Over: the settle edge already dropped its count and nothing will ever drop a
            # new one, so taking it would leave a dead id in the map for the server's life.
            # Said as what it is — the job is still on the list, so "no such job" would be
            # a false sentence about a row the person can see.
            self._drop_shell_observation(session_id, wanted)
            return over
        with self._shell_observe_lock:
            if getattr(task, "output_ended", False):
                # The pipe has closed but the exit is not reaped yet (`done` is still false
                # for a moment). The close already dropped this job's observers, or is about
                # to and is waiting for this lock; either way a count taken now would never
                # be dropped. Read UNDER the lock the drop takes, so the two cannot interleave.
                # Found by the full suite: one run in which the release after a settle
                # answered `ok: true` here.
                held = self._shell_observed.get(session_id)
                if held:
                    held.pop(wanted, None)
                    if not held:
                        self._shell_observed.pop(session_id, None)
                return over
            held = self._shell_observed.setdefault(session_id, {})
            if bool(on):
                observing = held.get(wanted, 0) + 1
                held[wanted] = observing
            else:
                # A decrement that bottoms out at zero DELETES the entry: "nobody is observing"
                # is the ABSENCE of a count, which is what `_shell_output`'s one cheap `.get`
                # reads, and a zero left behind would grow the map for every job any panel ever
                # opened.
                observing = max(0, held.get(wanted, 0) - 1)
                if observing:
                    held[wanted] = observing
                else:
                    held.pop(wanted, None)
                    if not held:
                        self._shell_observed.pop(session_id, None)
        return {"ok": True, "id": wanted, "observing": observing}

    def _drop_shell_observation(self, session_id: str, job_id: str) -> None:
        """Forget every observer of one job — called when the job's output STREAM closes.

        A count exists to gate frames from a RUNNING job, and a job whose pipe has closed has
        none left to send: a client that observed and never released must not keep a dead id in
        the map (nor the session key holding it open) for the life of the server. The stream's
        own close is the edge, not the completion notice, for two reasons. A job the model or
        the person KILLED sends no completion notice (`watch_background` returns early for
        it), so a count hung on that notice leaked for every killed job. And the notice comes
        from the watcher thread while the last text comes from the reader thread: dropping on
        the notice could close the gate in front of the job's final frame. The close arrives
        on the reader thread, after that frame. Silent by design; no observer is told, because
        the jobs list's `status` is the authoritative read that says a job is over.

        So a panel still holding a settled job sits at zero — which is exactly the state "this
        stream can no longer deliver anything" — and its next `off` is a harmless no-op
        answered by the finished-job sentence above.
        """
        with self._shell_observe_lock:
            held = self._shell_observed.get(session_id)
            if not held:
                return
            held.pop(str(job_id or ""), None)
            if not held:
                self._shell_observed.pop(session_id, None)

    def kill_background_job(self, session_id: str, job_id: str) -> dict[str, Any]:
        """Stop one shell job (the panel's Stop control).

        Reuses the executor's own kill, which owns the cross-platform process rules —
        `taskkill /F /T` plus the job object on Windows, the process group on POSIX.
        A second stop path here would drift from them.

        A job that had already finished is a no-op reported as one: the panel asked to stop
        something that is over, which is neither an error nor a kill, and the exit code it
        comes back with is the command's own.
        """
        record = self.session_store.load(session_id)
        if record is not None and getattr(record, "environment_id", None):
            return {
                "ok": False,
                "error": (
                    "This session runs on a remote host; stopping its jobs from here is "
                    "not available yet."
                ),
            }
        executor = getattr(self._engines.get(session_id), "executor", None)
        killer = getattr(executor, "background_kill", None)
        if not callable(killer):
            return {
                "ok": False,
                "error": "This session is not running here, so it has no jobs to stop.",
            }
        wanted = str(job_id or "").strip()
        if not wanted:
            return {"ok": False, "error": "No job was named."}
        try:
            result = killer(wanted)
        except Exception:  # noqa: BLE001 — a Stop button answers, it does not raise
            return {"ok": False, "error": f"Job {wanted} could not be stopped."}
        if "error" in result:
            return {"ok": False, "error": f"No job {wanted} in this session."}
        return {"ok": True, **result}
