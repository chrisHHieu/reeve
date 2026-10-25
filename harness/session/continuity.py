"""What was running when the process ended, on disk, so the next process can pick it up.

The engine already survives a restart at two points: a turn parked on a person's answer is
resumed when the answer comes (`inbox_handlers._durable_resume`), and a call that had started
is never repeated without a person, or in Full access without the model being told
(`tools/intents.py`). What it did not survive was the ordinary case: a turn in the middle of
its work when the app quit, crashed or was restarted by its watchdog. The transcript was saved
at every checkpoint, so nothing was lost, but nothing went on either: the conversation sat
where it stopped until someone typed again, and a background command that died with the
process was never reported to the session that started it (its completion was delivered in
memory only, `turn_state._deliver_shell_result`).

This store is the durable half of that. Two records, both written by the manager:

- **A turn claim** per session that is running a turn: written when the session is marked
  running, released when the turn ends. A claim left on disk means the turn did not end in this
  process, however that process ended (a kill, a crash and a graceful quit leave the same mark).
  `attempts` counts how many times the next process has picked it up, so a turn that kills the
  process every time is given up on instead of crash-looping.
- **The background jobs** a session had running: kept in step with the jobs roster (a job is
  added when it joins, removed when it ends), with the tail of its output written at a graceful
  shutdown. Whatever is left at startup was stopped by the end of the process.

Every record names the process that wrote it (`platform/process_owner.py`), and a starting
process takes over only the records of a process that has ended. The state folder is shared: the
desktop app and a dev server both use it, and an agent working on Reeve can start a second server
beside the one it runs in. Read as "whatever is on disk was left by the last process", one process
picked up a turn another was still running, and two engines wrote one session's log (2026-10-15,
242 `invariant/violation` rows in one session). Each write also starts from the file as it is, so
one process's claims are never written over by another's stale copy.

The failure policy is the session log's (AGENTS.md: log writes are best-effort): a record that
cannot be written must never cost a turn, because this store is how a turn is continued, not a
condition for running one.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

from harness.platform import process_owner

logger = logging.getLogger(__name__)

#: How many times one turn is picked up after the process that ran it ended. A turn that takes
#: the process down with it would otherwise restart it forever, spending a request each time.
MAX_RESUMES = 3

#: How long a write waits for another process's write to the same file before going ahead.
_LOCK_WAIT_SECONDS = 2.0


class ContinuityStore:
    def __init__(self, path: Optional[Path], *, owner: Optional[dict[str, Any]] = None) -> None:
        self.path = Path(path) if path else None
        self._lock = threading.Lock()
        self._memory: dict[str, Any] = {}  # the store when it has no file (tests, a pathless manager)
        #: The process this store writes as. Tests pass a dead one to stand for "the last process".
        self.owner = owner if owner is not None else process_owner.current()

    # -- turns ------------------------------------------------------------------------

    def claim_turn(self, session_id: str, *, parent: Optional[str] = None) -> None:
        """Note that `session_id` is running a turn in this process. A claim already held keeps its
        attempts: a turn that is being picked up claims again, and its count must survive that."""

        def change(data: dict[str, Any]) -> None:
            turns = data.setdefault("turns", {})
            held = turns.get(session_id) or {}
            turns[session_id] = {
                "since": held.get("since") or time.time(),
                "attempts": int(held.get("attempts") or 0),
                "owner": self.owner,
                **({"parent": parent} if parent else {}),
            }

        self._update(change)

    def release_turn(self, session_id: str) -> None:
        self._update(lambda data: data.get("turns", {}).pop(session_id, None) is not None)

    def held_turns(self) -> dict[str, dict[str, Any]]:
        """Every claim on disk, whoever holds it."""
        with self._lock:
            return {sid: dict(claim) for sid, claim in self._read().get("turns", {}).items()}

    def orphaned_turns(self) -> dict[str, dict[str, Any]]:
        """The claims whose process is no longer running: the turns a starting process may pick
        up. A claim held by a live process (the desktop app beside a dev server, a second server an
        agent started) is that process's turn, and running it here too put two engines on one
        session (2026-10-15)."""
        return {sid: claim for sid, claim in self.held_turns().items() if not process_owner.running(claim.get("owner"))}

    def count_resume(self, session_id: str) -> int:
        """One more pick-up of this turn, written BEFORE the pick-up runs, so a pick-up that
        takes the process down is counted by the next one. Returns the new count."""

        def change(data: dict[str, Any]) -> int:
            claim = data.get("turns", {}).get(session_id)
            if claim is None:
                return 0
            claim["attempts"] = int(claim.get("attempts") or 0) + 1
            return claim["attempts"]

        return self._update(change) or 0

    # -- background jobs -----------------------------------------------------------------

    def set_jobs(self, session_id: str, jobs: list[dict[str, Any]]) -> None:
        """The session's running jobs, as the roster has them now. A job's kept tail (written
        at shutdown) survives a later sync of the same job."""

        def change(data: dict[str, Any]) -> bool:
            table = data.setdefault("jobs", {})
            before = table.get(session_id) or {}
            rows = {
                str(job["id"]): {
                    "command": str(job.get("command") or ""),
                    "started": job.get("started_at"),
                    "owner": self.owner,
                    **({"tail": before[str(job["id"])]["tail"]} if before.get(str(job["id"]), {}).get("tail") else {}),
                }
                for job in jobs
                if job.get("id")
            }
            if rows == before:
                return False
            if rows:
                table[session_id] = rows
            else:
                table.pop(session_id, None)
            return True

        self._update(change)

    def keep_tails(self, tails: dict[str, dict[str, str]]) -> None:
        """At a graceful shutdown: what each running job had printed, `{session: {job: text}}`."""

        def change(data: dict[str, Any]) -> bool:
            table = data.setdefault("jobs", {})
            changed = False
            for session_id, by_job in tails.items():
                for job_id, text in by_job.items():
                    row = table.get(session_id, {}).get(job_id)
                    if row is not None and text:
                        row["tail"] = text
                        changed = True
            return changed

        self._update(change)

    def take_jobs(self) -> dict[str, dict[str, dict[str, Any]]]:
        """Every job whose process has ended, removed from the store: at startup these were stopped
        by the end of the process that ran them. A live process's jobs stay its own."""

        def change(data: dict[str, Any]) -> dict[str, dict[str, dict[str, Any]]]:
            table = data.get("jobs") or {}
            taken: dict[str, dict[str, dict[str, Any]]] = {}
            for session_id in list(table):
                rows = table[session_id] or {}
                gone = {job_id: row for job_id, row in rows.items() if not process_owner.running(row.get("owner"))}
                if not gone:
                    continue
                taken[session_id] = {job_id: {k: v for k, v in row.items() if k != "owner"} for job_id, row in gone.items()}
                left = {job_id: row for job_id, row in rows.items() if job_id not in gone}
                if left:
                    table[session_id] = left
                else:
                    table.pop(session_id)
            if not table:
                data.pop("jobs", None)
            return taken

        return self._update(change) or {}

    # -- storage ------------------------------------------------------------------------

    def _update(self, change: Callable[[dict[str, Any]], Any]) -> Any:
        """Read the file as it is now, apply one change, write it back. Every write starts from
        the disk and not from a copy this process read earlier, so a neighbour's claims survive our
        writes: a whole-file copy held in memory used to put back what another process had just
        released, or erase what it had just claimed."""
        with self._lock, self._file_lock():
            data = self._read()
            result = change(data)
            if result is not False:
                self._write(data)
            return result

    @contextlib.contextmanager
    def _file_lock(self) -> Iterator[None]:
        """Another process's write to the same store waits for ours. Best-effort: when the lock
        cannot be had in time the write goes ahead, as it did before there was one."""
        if self.path is None:
            yield
            return
        handle = None
        locked = False
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handle = open(self.path.with_suffix(".lock"), "a+b")
            deadline = time.monotonic() + _LOCK_WAIT_SECONDS
            while True:
                try:
                    if os.name == "nt":
                        import msvcrt

                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    locked = True
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        break
                    time.sleep(0.02)
        except Exception:  # noqa: BLE001 - a lock that cannot be made never costs the write
            logger.debug("continuity store lock unavailable", exc_info=True)
        try:
            yield
        finally:
            if handle is not None:
                try:
                    if locked:
                        if os.name == "nt":
                            import msvcrt

                            handle.seek(0)
                            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                        else:
                            import fcntl

                            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                finally:
                    handle.close()

    def _read(self) -> dict[str, Any]:
        if self.path is None:
            return self._memory
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except Exception:  # noqa: BLE001 - a broken file means "nothing to pick up"
            logger.warning("continuity store %s is unreadable; starting empty", self.path, exc_info=True)
            return {}
        return data if isinstance(data, dict) else {}

    def _write(self, data: dict[str, Any]) -> None:
        """Flushed, fsynced and swapped in, like the intent notes: a half-written file would read
        as "nothing was running" on exactly the restart it is for. Never raises."""
        if self.path is None:
            self._memory = data
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(f".{os.getpid()}.tmp")
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(data, handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self.path)
        except Exception:  # noqa: BLE001 - best-effort: a record must never cost a turn
            logger.warning("continuity store %s could not be written", self.path, exc_info=True)
