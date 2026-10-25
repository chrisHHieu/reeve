"""JobsRosterMixin — the session's jobs roster, PUSHED on its socket.

`SessionJobsMixin` answers a client that asks (the list, one job's tail, a Stop). This is the
half nobody asks for: a `jobs` frame carrying the whole roster, sent when a job joins the list,
when a process ends, when a pipe closes, and — paced — while a job prints. A panel that polled
the list on a clock it only started once it already knew of a running job showed a job the
agent launched mid-turn when the turn was over.

Display-only, like the reads beside it: nothing here reaches a provider, so nothing here writes
a session-log row. Its own module because `session_jobs.py` sits at the 400-line ceiling the
architecture policy enforces, and because the seam is real: the reads are request-shaped, this
is the push.
"""
from __future__ import annotations

import asyncio
import time


#: The most often a session's roster is re-sent because a job PRINTED. A row shows
#: one line of what its job is saying; once a second reads as live, and a build that prints a
#: thousand lines a second still costs one small frame. Lifecycle changes are never paced.
JOBS_FRAME_SECONDS = 1.0


class JobsRosterMixin:
    def _jobs_changed(self, session_id: str, *, printing: bool = False) -> None:
        """The session's jobs changed: put the roster on its socket as a `jobs` frame.

        Called from the executor's threads (the spawn, the watcher that saw a process leave,
        the reader), so it only hands the work to the manager loop — the same discipline as
        `_shell_output`. A LIFECYCLE change (a job joined the list, a process ended, a pipe
        closed) is sent at once. `printing=True` is a job that said something: the rows carry
        its last line, and that moves at most once per `JOBS_FRAME_SECONDS` however fast the
        job prints — the full text is the observed stream's job, this is the look
        a row gives without being opened.

        Two cheap refusals on the calling thread, because the reader calls this for every
        coalesced flush: nobody is connected to the session (a client reads the list when it
        connects, so there is nothing to keep current), or a frame is already due — it reads
        the roster when it is SENT, so the line that caused this call is in it.
        """
        if not self._session_clients.get(session_id):
            return
        if printing and session_id in self._jobs_due:
            return
        loop = self._loop
        if loop is None or loop.is_closed():
            return  # no loop (a bare test manager): there is no socket to put it on
        try:
            loop.call_soon_threadsafe(self._schedule_jobs_frame, session_id, printing)
        except RuntimeError:
            pass  # the loop shut down between the check and the call

    def _schedule_jobs_frame(self, session_id: str, printing: bool) -> None:
        """On the loop: send now, or arm the one timer a printing job is allowed."""
        timer = self._jobs_timers.get(session_id)
        if not printing:
            if timer is not None:
                timer.cancel()
            self._send_jobs_frame(session_id)
            return
        if timer is not None:
            return  # already due
        wait = JOBS_FRAME_SECONDS - (time.monotonic() - self._jobs_sent.get(session_id, 0.0))
        if wait <= 0:
            self._send_jobs_frame(session_id)
            return
        self._jobs_due.add(session_id)
        self._jobs_timers[session_id] = asyncio.get_running_loop().call_later(
            wait, self._send_jobs_frame, session_id
        )

    def _send_jobs_frame(self, session_id: str) -> None:
        """On the loop: a frame is owed NOW. One send per session is in flight at a time; a
        frame owed while one is in flight is sent after it, from a fresh read."""
        self._jobs_timers.pop(session_id, None)
        # Cleared BEFORE the roster is read: a reader that still finds the mark set printed its
        # line before that read and is covered by it; one that finds it cleared arms the next.
        self._jobs_due.discard(session_id)
        self._jobs_sent[session_id] = time.monotonic()
        if session_id in self._jobs_sending:
            self._jobs_again.add(session_id)
            return
        self._jobs_sending.add(session_id)
        task = asyncio.ensure_future(self._deliver_jobs(session_id))
        # Retrieve the outcome so a failed send is not reported as an unhandled task error: the
        # broadcaster already drops a dead socket, and a display frame has nobody to tell.
        task.add_done_callback(lambda done: done.cancelled() or done.exception())

    async def _deliver_jobs(self, session_id: str) -> None:
        """Read the roster and broadcast it, again while another frame was owed meanwhile.

        Whole-set, so a client that missed a frame is corrected by the next one and a
        reconnect needs no replay — which is only true if the LAST frame a client receives is
        the newest roster. Two sends racing on a slow socket could land the older one last;
        sending one at a time, each read at the moment it is sent, cannot.
        """
        try:
            while True:
                self._jobs_again.discard(session_id)
                roster = self.background_jobs(session_id)
                if not any(row.get("status") == "running" for row in roster["jobs"]):
                    self._jobs_sent.pop(session_id, None)  # nothing left that could print
                await self.broadcast_session(session_id, {"type": "jobs", "data": roster})
                if session_id not in self._jobs_again:
                    return
        finally:
            self._jobs_sending.discard(session_id)
            self._jobs_again.discard(session_id)
