"""The live job-output channel: `job_output` frames on the session socket.

The jobs panel used to poll `GET …/jobs/output` on a one-second clock. The engine already
reads every job's output line by line, so the panel now anchors once with that tail route and
APPENDS what arrives after it, as a followed job streams. What must hold here:

  · a job that prints twice with a gap reports both, and the frame that carries the final text
    is the one the END of the stream sent — the exit flush is the only reason a job that lives
    for less than one coalescing window reports anything at all;
  · a chatty job's lines COALESCE: fewer frames than lines, never an empty chunk;
  · `total` is the job's absolute character count, monotonic across frames, and equal to the
    tail route's `total_chars` once the process is gone — a client can tell it missed a frame;
  · a raising listener cannot stop the reader (the buffer is what `shell_task_output` and the
    tail route read, and the only record of what the command printed);
  · frames ride the SESSION socket of the session that owns the job — never another's — and a
    session whose span was undone (`/undo`) gets none, exactly like the completion notification;
  · the stream is GATED on observation: a job nobody has said it is watching sends
    nothing at all — while staying readable through the anchor route and the completion
    notification — the frames start when a client observes and stop when it releases, the
    count is per session per job (two watchers, one release, still streaming), it cannot go
    below zero, and a settled job's count is dropped rather than leaked;
  · nothing model-visible changes: no session-log row, no new event type.

Every job here is short-lived or killed in teardown. The window is injected where WHICH flush
sent a frame matters (a widened `FLUSH_WINDOW_SECONDS` means no timer can fire before the job
is over), and the coalescer itself is unit-tested on a frozen clock and a scheduler that never
runs — so nothing waits seconds to watch a window close. `_follow` is a client that OBSERVES
(the panels in these tests are all looking at their jobs); the gating cases observe by hand so
they can prove what an unobserved job does and does not send.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys
import threading
import time

import pytest

from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient
from harness.interface.server import SessionManager
from harness.session.sessions import SessionRecord
from harness.tools import shell_output_stream
from harness.tools.shell_output_stream import FLUSH_WINDOW_SECONDS, JobOutputStream

_WIN = os.name == "nt"
#: Quoted on both platforms, with the `&` call operator on Windows — the same lesson the
#: shell tests already learned (a bare path with a space becomes two arguments; PowerShell
#: reads a quoted path in command position as data).
_LAUNCH = f'& "{sys.executable}"' if _WIN else f'"{sys.executable}"'

#: Prints, waits longer than one window, prints again. The gap is what makes the first frame
#: the timer's and the second the exit flush's.
PRINTS_TWICE = (
    _LAUNCH + " -c \"print('alpha'); import time; time.sleep(0.4); print('beta')\""
)
PRINTS_TWICE_TEXT = "alpha\nbeta\n"
#: One print and out: the whole job is shorter than a window.
PRINTS_ONCE = _LAUNCH + " -c \"print('only once')\""
#: 200 lines from ONE write, so they land together and coalesce into a frame or two.
MANY_LINES = _LAUNCH + " -c \"print('\\n'.join(['ping'] * 200))\""
MANY_LINES_TEXT = "ping\n" * 200
#: One line and out — the job the gate tests leave unobserved.
SAYS_READY = _LAUNCH + " -c \"print('ready')\""
#: Prints, then stays alive: a RUNNING job whose observers can come and go. Killed by the test
#: that starts it.
SAYS_READY_THEN_WAITS = (
    _LAUNCH + " -c \"print('ready', flush=True); import time; time.sleep(30)\""
)
#: Head, a pause, tail — the shape a foreground command adopted into the registry has.
HEAD_THEN_TAIL = (
    _LAUNCH + " -c \"print('head'); import time; time.sleep(0.5); print('tail')\""
)


class Provider(ProviderClient):
    def complete(self, *, model, messages, tools=None, **settings):
        return AssistantTurn(text="ok", finish_reason="stop")

    def capabilities(self, model):
        return ModelCapabilities()


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    ws = tmp_path / "ws"
    ws.mkdir()
    mgr = SessionManager(workspace=ws, provider=Provider())
    yield mgr
    # `close()` interrupts what is in flight and deliberately leaves background tasks alone.
    for engine in list(mgr._engines.values()):
        executor = getattr(engine, "executor", None)
        for task_id in list(getattr(executor, "_bg_tasks", {}) or {}):
            try:
                executor.background_kill(task_id)
            except Exception:  # noqa: BLE001 — teardown cleanup, never a test failure
                pass
        closer = getattr(executor, "close", None)
        if callable(closer):
            closer()


@pytest.fixture
def wide_window(monkeypatch):
    """Widen the coalescing window.

    Then NO timer can fire before a short job is over, so whatever arrives was sent by the
    stream's END — which is how "the flush-on-exit is what makes a short-lived job's output
    arrive" is pinned end to end instead of raced against a 100 ms timer.
    """
    monkeypatch.setattr(shell_output_stream, "FLUSH_WINDOW_SECONDS", 30.0)


@contextlib.contextmanager
def _live_loop(manager):
    """The manager's loop, running on a thread: the only seam that can prove a frame reached
    a socket (the listener fires on a plain reader thread and hands its work over)."""
    loop = asyncio.new_event_loop()
    manager._loop = loop
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    try:
        yield
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=5)
        loop.close()
        manager._loop = None


def _session(mgr: SessionManager, session_id: str, **kwargs) -> SessionRecord:
    record = SessionRecord(
        session_id=session_id,
        model="gpt-5.5",
        mode="interactive",
        **{"workspace": str(mgr.default_workspace), **kwargs},
    )
    mgr.session_store.save(record)
    return record


def _follow(mgr: SessionManager, session_id: str) -> list[dict]:
    """One client on one session's socket, as the WS driver registers one.

    A socket alone carries no `job_output` frames: the client also has to say which
    job it is looking at, and it can only name a job that exists — see `_start_watched`.
    """
    frames: list[dict] = []

    async def cb(message):
        frames.append(message)

    mgr.register_session_client(session_id, cb)
    return frames


def _start_watched(mgr: SessionManager, session_id: str, command: str) -> dict:
    """Start a job and observe it at once — what a panel does when it opens the row of a job
    it just saw start. The gate is read when a FLUSH happens (100 ms after the first chunk, or
    at the end of the pipe), and the observation lands within the same Python statement pair as
    the spawn, long before a child process has started, printed and flushed."""
    started = _executor(mgr, session_id).run_background(command)
    held = mgr.observe_background_job(session_id, started["task_id"], True)
    assert held == {"ok": True, "id": started["task_id"], "observing": 1}, held
    return started


def _hand_fed(mgr: SessionManager, session_id: str, task_id: str) -> None:
    """Open the gate for a frame a test feeds to `_shell_output` by hand. The public door
    refuses a job the session does not hold, and these frames belong to no real job."""
    mgr._shell_observed.setdefault(session_id, {})[task_id] = 1


def _executor(mgr: SessionManager, session_id: str):
    engine = mgr.get_engine(session_id)
    assert engine is not None
    return engine.executor


def _tail(mgr: SessionManager, session_id: str, job_id: str) -> dict:
    """The anchor read a panel makes when it opens a job — never gated, never consuming."""
    return mgr.background_job_output(session_id, job_id, None)


def _wait_until(predicate, *, seconds: float = 20.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


def _drained(task, *, seconds: float = 20.0) -> bool:
    """The process is gone AND its reader has stopped appending — the narrower fact a
    comparison against the buffer needs, since `poll()` answers while bytes are in the pipe."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        before = task.chars_read
        time.sleep(0.05)
        if task.done and task.chars_read == before:
            return True
    return False


def _job_frames(frames: list[dict]) -> list[dict]:
    """Only the channel under test.

    The same socket also carries the events of the turn a finished job wakes (`_shell_finished`
    opens a real one), which is exactly why these tests filter rather than count.
    """
    return [frame for frame in frames if frame.get("type") == "job_output"]


def _chunks(frames: list[dict]) -> list[str]:
    """The chunks of the CLIENT frames on the session socket."""
    return [frame["data"]["chunk"] for frame in _job_frames(frames)]


def _totals(frames: list[dict]) -> list[int]:
    return [frame["data"]["total"] for frame in _job_frames(frames)]


def _sent(frames: list[dict]) -> list[str]:
    """The chunks the coalescer handed its listener (the payload, before the frame is built)."""
    return [frame["chunk"] for frame in frames]


def _local_executor(tmp_path):
    from harness.tools.shell import LocalExecutor

    return LocalExecutor(cwd=tmp_path)


# -- the coalescer, on an injected clock (no process, no waiting) ----------------------


class _Clock:
    """A monotonic clock a test moves by hand."""

    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _FakeTimer:
    def __init__(self, delay: float, callback) -> None:
        self.delay = delay
        self.callback = callback
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


def _scheduler():
    """A scheduler that only RECORDS: the test decides when a window closes."""
    armed: list[tuple[float, _FakeTimer]] = []

    def schedule(delay, callback):
        timer = _FakeTimer(delay, callback)
        armed.append((delay, timer))
        return timer

    return armed, schedule


def test_the_window_opens_with_the_first_chunk_and_closes_on_the_timer():
    clock = _Clock()
    armed, schedule = _scheduler()
    frames: list[dict] = []
    stream = JobOutputStream(
        "bg-1", frames.append, window=0.1, clock=clock, schedule=schedule
    )

    stream.feed("one\n")
    assert frames == []  # held: the window is open, and nothing has closed it yet
    assert len(armed) == 1 and armed[0][0] == pytest.approx(0.1)

    clock.advance(0.05)
    stream.feed("two\n")
    assert frames == []  # the same window holds it — one window, one flush
    assert len(armed) == 1  # and no second timer was armed behind the first

    clock.advance(0.05)
    armed[0][1].callback()  # the window closes
    assert _sent(frames) == ["one\ntwo\n"]
    assert frames[0]["total"] == 8 and frames[0]["ended"] is False

    # A later burst opens a NEW window — it is not sent on arrival just because time passed.
    clock.advance(5.0)
    stream.feed("three\n")
    assert len(frames) == 1
    assert armed[-1][0] == pytest.approx(0.1)
    stream.feed("", ended=True)
    assert _sent(frames) == ["one\ntwo\n", "three\n"]
    assert frames[-1]["total"] == 14 and frames[-1]["ended"] is True


def test_a_short_job_reports_from_the_exit_flush():
    """The pin for "the flush-on-exit is what makes a short-lived job's output arrive": with a
    scheduler that never runs, the end of the stream is the ONLY sender, and its frame carries
    the whole text and `ended`."""
    clock = _Clock()
    frames: list[dict] = []
    stream = JobOutputStream(
        "bg-2", frames.append, window=0.1, clock=clock, schedule=lambda _d, _f: None
    )

    stream.feed("only once\n")
    assert frames == []  # nothing has closed the window, and nothing ever will
    stream.feed("", ended=True)

    assert _sent(frames) == ["only once\n"]
    assert frames[0]["ended"] is True
    assert frames[0]["total"] == len("only once\n")


def test_an_end_with_nothing_held_sends_no_empty_frame():
    """A job that fell silent more than a window before it exited has nothing left to send;
    the row's finish is the jobs list's `status`, the authoritative read — an empty frame is
    noise on somebody's socket, so the stream does not build one."""
    clock = _Clock()
    armed, schedule = _scheduler()
    frames: list[dict] = []
    stream = JobOutputStream(
        "bg-3", frames.append, window=0.1, clock=clock, schedule=schedule
    )

    stream.feed("quiet\n")
    clock.advance(0.5)
    armed[0][1].callback()
    assert _sent(frames) == ["quiet\n"]

    stream.feed("", ended=True)
    assert len(frames) == 1  # the end added nothing


def test_everything_printed_inside_one_window_is_one_frame():
    """Coalescing, deterministically: 40 lines on a frozen clock with no timer running are one
    frame — which is the property the e2e chatty-job test can only bound."""
    frames: list[dict] = []
    stream = JobOutputStream(
        "bg-4",
        frames.append,
        window=FLUSH_WINDOW_SECONDS,
        clock=_Clock(),
        schedule=lambda _d, _f: None,
    )
    for index in range(40):
        stream.feed(f"line {index}\n")
    assert frames == []

    stream.feed("", ended=True)
    assert len(frames) == 1
    assert frames[0]["chunk"] == "".join(f"line {index}\n" for index in range(40))
    assert frames[0]["total"] == len(frames[0]["chunk"])
    assert frames[0]["ended"] is True


def test_a_flush_longer_than_a_frame_is_split_with_absolute_totals():
    """A window can hold megabytes (a command that dumps a file). Rather than one frame that
    size — or dropping the text — it becomes several bounded frames, each carrying the total it
    ends at, so the client's arithmetic still says what it has and what it missed."""
    frames: list[dict] = []
    stream = JobOutputStream(
        "bg-5", frames.append, window=0.0, max_frame=4, schedule=lambda _d, _f: None
    )

    stream.feed("abcdefghij", ended=True)

    assert _sent(frames) == ["abcd", "efgh", "ij"]
    assert [frame["total"] for frame in frames] == [4, 8, 10]
    assert [frame["ended"] for frame in frames] == [False, False, True]


def test_the_generation_travels_with_the_frame_but_not_to_the_client():
    frames: list[dict] = []
    stream = JobOutputStream("bg-6", frames.append, window=0.0, generation=7)
    stream.feed("x", ended=True)
    assert frames[0]["generation"] == 7  # the manager's fence reads it …
    assert set(frames[0]) == {"task_id", "chunk", "total", "ended", "generation"}


def test_a_raising_listener_leaves_the_stream_counting():
    seen: list[dict] = []

    def half_broken(info):
        seen.append(info)
        raise RuntimeError("listener bug")

    stream = JobOutputStream("bg-7", half_broken, window=0.0)
    stream.feed("a\n")
    stream.feed("b\n", ended=True)
    # The frames are built and handed over in order, with absolute totals, despite the raise.
    assert _sent(seen) == ["a\n", "b\n"]
    assert [frame["total"] for frame in seen] == [2, 4]
    assert seen[-1]["ended"] is True

    # And a closed stream takes nothing more, however late a read lands.
    stream.feed("c\n")
    assert len(seen) == 2


# -- through a real job, on the session socket -----------------------------------------


def test_a_job_that_prints_twice_reports_both_and_the_last_frame_ends_it(
    manager, wide_window
):
    """The whole path: a detached command, the reader thread, the coalescer, the manager's
    loop and a client's socket. The window is widened so the frame that carries the final text
    is provably the one the END of the stream sent."""
    _session(manager, "s-stream")
    with _live_loop(manager):
        frames = _follow(manager, "s-stream")
        started = _start_watched(manager, "s-stream", PRINTS_TWICE)

        assert _wait_until(
            lambda: bool(_job_frames(frames)) and _job_frames(frames)[-1]["data"]["ended"]
        ), f"no terminal frame arrived: {frames}"
        job = _job_frames(frames)
        assert "".join(_chunks(frames)) == PRINTS_TWICE_TEXT
        assert job[-1]["data"]["chunk"].endswith("beta\n")  # the final text, at the end
        assert job[-1]["data"]["task_id"] == started["task_id"]
        # The wire shape a panel binds to, key for key: `generation` is the fence's, not the
        # client's, and nothing else rides along.
        assert set(job[-1]["data"]) == {"task_id", "chunk", "total", "ended"}


def test_a_chatty_job_coalesces_and_never_sends_an_empty_chunk(manager):
    """A build printing 200 lines must not put 200 frames on the socket."""
    _session(manager, "s-chatty")
    with _live_loop(manager):
        frames = _follow(manager, "s-chatty")
        executor = _executor(manager, "s-chatty")
        started = _start_watched(manager, "s-chatty", MANY_LINES)
        task = executor._bg_tasks[started["task_id"]]
        assert _drained(task), "the job's output never settled"
        assert _wait_until(lambda: bool(_job_frames(frames))), "no frame arrived at all"
        time.sleep(0.3)  # let a trailing window land

        job = _job_frames(frames)
        assert 0 < len(job) < 200, f"{len(job)} frames for 200 lines"
        assert all(frame["data"]["chunk"] for frame in job), "an empty chunk went out"


def test_the_total_is_monotonic_and_matches_the_tail_route(manager):
    """`total` is the job's absolute count, so a client that missed a frame can tell — and the
    anchor it compares against is the tail route's `total_chars`."""
    _session(manager, "s-total")
    with _live_loop(manager):
        frames = _follow(manager, "s-total")
        executor = _executor(manager, "s-total")
        started = _start_watched(manager, "s-total", MANY_LINES)
        task = executor._bg_tasks[started["task_id"]]
        assert _drained(task), "the job's output never settled"
        assert _wait_until(lambda: bool(_job_frames(frames))), "no frame arrived at all"
        time.sleep(0.3)

        totals = _totals(frames)
        assert totals == sorted(totals) and len(set(totals)) == len(totals)
        assert "".join(_chunks(frames)) == MANY_LINES_TEXT
        tail = executor.background_tail(started["task_id"], 20_000)
        assert tail["total_chars"] == len(MANY_LINES_TEXT)
        assert totals[-1] == tail["total_chars"]  # nothing was left behind in a window
        assert totals[-1] == task.chars_read


def test_frames_go_only_to_the_socket_following_that_session(manager):
    _session(manager, "s-owner")
    _session(manager, "s-other")
    with _live_loop(manager):
        mine = _follow(manager, "s-owner")
        theirs = _follow(manager, "s-other")
        started = _start_watched(manager, "s-owner", PRINTS_ONCE)

        assert _wait_until(lambda: bool(_job_frames(mine))), "no frame reached the owner"
        time.sleep(0.2)
        assert _job_frames(theirs) == []  # another session's socket saw nothing
        assert _job_frames(mine)[0]["data"]["task_id"] == started["task_id"]
        assert "".join(_chunks(mine)) == "only once\n"


def test_an_executor_nobody_listens_to_streams_nothing(tmp_path):
    """The channel is opt-in: without a listener the stream is never built, and the reader
    behaves exactly as it did before it existed."""
    ex = _local_executor(tmp_path)
    try:
        started = ex.run_background(PRINTS_ONCE)
        task = ex._bg_tasks[started["task_id"]]
        assert task.on_output is None
        assert _drained(task)
        assert ex.background_tail(started["task_id"], 4_000)["output"] == "only once\n"
    finally:
        ex.close()


def test_a_raising_output_listener_cannot_stop_the_reader(tmp_path):
    """The buffer is what `shell_task_output` and the tail route read, and the only record of
    what the command printed: a display sink that raises must not cost the job its output."""
    ex = _local_executor(tmp_path)

    def boom(_info):
        raise RuntimeError("listener bug")

    ex.set_background_output_listener(boom)
    try:
        started = ex.run_background(PRINTS_TWICE)
        task = ex._bg_tasks[started["task_id"]]
        assert _wait_until(lambda: task.done and task.chars_read == len(PRINTS_TWICE_TEXT))
        assert task.exit_code == 0

        # The MODEL's read is whole, and so is the panel's tail (which moves no cursor).
        assert ex.background_output(started["task_id"])["output"] == PRINTS_TWICE_TEXT
        assert (
            ex.background_tail(started["task_id"], 4_000)["output"] == PRINTS_TWICE_TEXT
        )
    finally:
        ex.close()


def test_a_task_adopted_from_the_foreground_streams_from_its_attach_point(tmp_path):
    """A foreground command whose budget expired is handed to the background registry having
    already printed. Its stream starts where it was adopted, counting from `chars_read`: the
    client gets the job's ABSOLUTE total, so it can see the head it missed and re-anchor with
    the tail route (`_watch_background` is called again here, which is what adoption does)."""
    ex = _local_executor(tmp_path)
    try:
        started = ex.run_background(HEAD_THEN_TAIL)
        task = ex._bg_tasks[started["task_id"]]
        assert _wait_until(lambda: task.chars_read > 0), "the head was never read"
        assert task.on_output is None  # it printed with nobody streaming

        frames: list[dict] = []
        ex.set_background_output_listener(frames.append)
        ex._watch_background(task)

        assert _wait_until(lambda: bool(frames)), "the adopted task streamed nothing"
        assert "".join(frame["chunk"] for frame in frames) == "tail\n"
        assert frames[0]["total"] > len(frames[0]["chunk"])  # …the head is a visible gap
        assert frames[-1]["total"] == task.chars_read == len("head\ntail\n")
        # The buffer itself is whole, which is what the panel anchors with.
        assert ex.background_tail(started["task_id"], 4_000)["output"] == "head\ntail\n"
    finally:
        ex.close()


# -- observation gating ----------------------------------------------------------------


def test_a_job_nobody_observes_sends_no_frames_but_stays_readable(manager):
    """The whole point of the gate: a client that is not looking at a job costs the
    socket nothing. The job still ran, still holds its buffer (the anchor route reads it) and
    still reports its completion — only the `job_output` frames are absent."""
    _session(manager, "s-unwatched")
    with _live_loop(manager):
        frames = _follow(manager, "s-unwatched")  # one client, observing NOTHING
        executor = _executor(manager, "s-unwatched")
        started = executor.run_background(SAYS_READY)
        job_id = started["task_id"]

        assert _wait_until(
            lambda: any(frame.get("type") == "turn_done" for frame in frames)
        ), f"the completion notification never woke the session: {frames}"
        time.sleep(0.3)  # a window's worth of rope for any frame that wanted to arrive

        assert _job_frames(frames) == [], "an unobserved job streamed output"
        # The buffer is untouched by the gate: the anchor route still shows everything, and
        # so does the model's own read.
        assert _tail(manager, "s-unwatched", job_id)["output"].strip() == "ready"
        assert manager.shell_observed_count("s-unwatched", job_id) == 0


def test_observing_turns_the_frames_on_and_releasing_turns_them_off(manager):
    """`observe` on → the frames arrive; `observe` off → they stop. Pinned against a job that
    KEEPS PRINTING after the release: output read after it produces no frame, while the buffer
    the anchor route reads carries on growing (nothing is being dropped — only unsent)."""
    _session(manager, "s-observed")
    with _live_loop(manager):
        frames = _follow(manager, "s-observed")
        # Prints and KEEPS RUNNING, so the release below is a live job's and not a settled one's.
        started = _start_watched(manager, "s-observed", SAYS_READY_THEN_WAITS)
        job_id = started["task_id"]
        assert manager.shell_observed_count("s-observed", job_id) == 1

        assert _wait_until(lambda: bool(_job_frames(frames))), "no frame for an observed job"

        released = manager.observe_background_job("s-observed", job_id, False)
        assert released == {"ok": True, "id": job_id, "observing": 0}
        sent = len(_job_frames(frames))

        # The buffer is untouched by the release: the anchor route still reads it …
        assert "ready" in _tail(manager, "s-observed", job_id)["output"]
        # … and a frame that would have been sent before the release is not sent after it:
        # feeding the very payload the stream was sending is the deterministic half.
        manager._shell_output(
            "s-observed",
            {
                "task_id": job_id,
                "chunk": "late\n",
                "total": 99,
                "ended": False,
                "generation": 0,
            },
        )
        time.sleep(0.3)
        assert len(_job_frames(frames)) == sent, "a released job still streamed"
        _executor(manager, "s-observed").background_kill(job_id)


def test_two_observers_then_one_release_keeps_streaming(manager):
    """The count is per session per job: two panels can watch one job, and the one that goes
    away must not stop the other's stream. A second release bottoms out at zero and is
    harmless — it cannot go negative and silently silence a job that IS being watched."""
    _session(manager, "s-two")
    with _live_loop(manager):
        frames = _follow(manager, "s-two")
        executor = _executor(manager, "s-two")
        # Prints and KEEPS RUNNING: the whole test happens while the job is alive, so the
        # counts below are the live job's and not a settled one's.
        started = _start_watched(manager, "s-two", SAYS_READY_THEN_WAITS)
        job_id = started["task_id"]
        try:
            second = manager.observe_background_job("s-two", job_id, True)
            assert second == {"ok": True, "id": job_id, "observing": 2}
            assert _wait_until(
                lambda: "ready" in _tail(manager, "s-two", job_id)["output"]
            ), "the job never printed"

            assert manager.observe_background_job("s-two", job_id, False)["observing"] == 1
            # The surviving observer still gets frames: the payload is fed through the same
            # handler the reader thread uses, with the count at exactly one.
            manager._shell_output(
                "s-two",
                {
                    "task_id": job_id,
                    "chunk": "still watched\n",
                    "total": 14,
                    "ended": False,
                    "generation": 0,
                },
            )
            assert _wait_until(lambda: bool(_job_frames(frames))), "one release stopped it"

            # The second release, and a third for good measure: clamps at zero, no exception,
            # and no negative count behind it.
            assert manager.observe_background_job("s-two", job_id, False)["observing"] == 0
            assert manager.observe_background_job("s-two", job_id, False)["observing"] == 0
            assert manager.shell_observed_count("s-two", job_id) == 0
            assert _job_frames(frames)  # what already arrived is still there
        finally:
            executor.background_kill(job_id)


def test_a_settled_job_drops_its_observation(manager):
    """No leak: a client that observed and never released must not keep a dead id (and its
    session key) in the manager's map for the life of the server. The drop happens when the
    job's output stream CLOSES — after its last frame — so a later release is harmless."""
    _session(manager, "s-settled")
    with _live_loop(manager):
        _follow(manager, "s-settled")
        executor = _executor(manager, "s-settled")
        started = executor.run_background(PRINTS_TWICE)  # alive while the count is set below
        job_id = started["task_id"]
        held = manager.observe_background_job("s-settled", job_id, True)
        assert held.get("observing") == 1 or held.get("ok") is False  # may have just settled

        assert _wait_until(
            lambda: manager.shell_observed_count("s-settled", job_id) == 0
        ), "a settled job kept its observation count"
        assert "s-settled" not in manager._shell_observed, "an empty session key was left"

        # The buffer is still readable (the row is still in the jobs list) and the release a
        # panel sends on its way out is an honest no-op rather than an exception.
        assert "alpha" in _tail(manager, "s-settled", job_id)["output"]
        late = manager.observe_background_job("s-settled", job_id, False)
        assert late == {
            "ok": False,
            "error": f"Job {job_id} has finished; there is nothing left to stream.",
        }
        # …and neither can a late `on` resurrect a count nothing would ever drop.
        assert manager.observe_background_job("s-settled", job_id, True)["ok"] is False
        assert manager.shell_observed_count("s-settled", job_id) == 0


def test_a_job_whose_pipe_closed_but_is_not_reaped_yet_cannot_be_observed(manager):
    """The pipe closes a moment before `proc.poll()` reports the exit. In that window the
    stream's close has already dropped the observers, so a count taken then would never be
    dropped. Held still here with a task in exactly that state; the real race is how the
    full suite found it (the release above answered `ok: true` once in 5,164 tests)."""

    class _ClosedNotReaped:
        done = False
        output_ended = True

    _session(manager, "s-window")
    executor = _executor(manager, "s-window")
    executor._bg_tasks["bg-window"] = _ClosedNotReaped()
    try:
        finished = {
            "ok": False,
            "error": "Job bg-window has finished; there is nothing left to stream.",
        }
        assert manager.observe_background_job("s-window", "bg-window", True) == finished
        assert manager.shell_observed_count("s-window", "bg-window") == 0
        assert "s-window" not in manager._shell_observed
        assert manager.observe_background_job("s-window", "bg-window", False) == finished
    finally:
        executor._bg_tasks.pop("bg-window", None)


# -- the fence -------------------------------------------------------------------------


def test_a_revert_fenced_session_drops_the_frames(manager):
    """Same rule as the completion notification: an `/undo` hides the span that
    started the job, so output from before it is not news a client should be shown."""
    _session(manager, "s-fenced")
    with _live_loop(manager):
        frames = _follow(manager, "s-fenced")
        manager._revert_generation["s-fenced"] = 2
        # Observed, so the FENCE is the only thing that can drop these frames — otherwise the
        # gate would pass for the wrong reason and this test would stop proving anything.
        _hand_fed(manager, "s-fenced", "bg-9")
        stale = {
            "task_id": "bg-9",
            "chunk": "before the undo\n",
            "total": 16,
            "ended": False,
            "generation": 1,
        }

        manager._shell_output("s-fenced", stale)
        time.sleep(0.2)
        # The TEXT is fenced. The jobs roster may still be re-sent: it is the whole
        # registry, as the list route serves it, not news from the undone span.
        assert _job_frames(frames) == [], "a frame from the undone span was delivered"

        # …and a task started AFTER the undo still arrives.
        manager._shell_output("s-fenced", {**stale, "generation": 2})
        assert _wait_until(lambda: bool(_job_frames(frames)))
        assert _job_frames(frames)[0]["data"]["chunk"] == "before the undo\n"
        assert _job_frames(frames)[0]["data"]["total"] == 16


def test_the_handler_answers_quietly_when_it_cannot_deliver(manager):
    """A frame that cannot be delivered must never fail the job: no loop (a bare manager) and
    an empty chunk are both no-ops, not exceptions."""
    _session(manager, "s-quiet")
    info = {"task_id": "bg-1", "chunk": "text\n", "total": 5, "ended": False, "generation": 0}
    manager._shell_output("s-quiet", info)  # manager._loop is None here

    with _live_loop(manager):
        frames = _follow(manager, "s-quiet")
        # Observed, so the loop/empty-chunk checks below are the only ones under test.
        _hand_fed(manager, "s-quiet", "bg-1")
        manager._shell_output("s-quiet", {**info, "chunk": ""})
        manager._shell_output("s-quiet", {**info, "chunk": None})
        time.sleep(0.2)
        # Nothing at all: an empty chunk printed nothing, so not even the roster moves.
        assert frames == []

        manager._shell_output("s-quiet", info)
        assert _wait_until(lambda: bool(_job_frames(frames)))
        assert _job_frames(frames)[0]["data"]["chunk"] == "text\n"


def test_a_killed_job_drops_its_observation_too(manager):
    """A killed job sends no completion notice (the model stopped it on purpose), so a count
    hung on that notice leaked for every job a person or the model stopped. The stream's own
    close is the edge: the pipe closes when the process tree dies."""
    _session(manager, "s-killed")
    started = _start_watched(manager, "s-killed", SAYS_READY_THEN_WAITS)
    job_id = started["task_id"]
    assert manager.shell_observed_count("s-killed", job_id) == 1

    _executor(manager, "s-killed").background_kill(job_id)

    assert _wait_until(
        lambda: manager.shell_observed_count("s-killed", job_id) == 0
    ), "a killed job kept its observation count"
    assert "s-killed" not in manager._shell_observed


def test_the_close_arrives_after_the_last_frame_and_is_not_a_frame():
    """One channel, one thread, in order: the text, then the news that no more can come. The
    close carries no chunk, so nothing downstream can mistake it for output."""
    seen: list[dict] = []
    feed = shell_output_stream.job_output_stream(seen.append, "bg-1", generation=3)

    feed("last words\n", True)
    feed("too late\n", False)

    assert [info.get("chunk") for info in seen] == ["last words\n", ""]
    assert seen[0]["ended"] is True and "closed" not in seen[0]
    assert seen[1] == {
        "task_id": "bg-1", "chunk": "", "total": 11, "ended": True, "closed": True, "generation": 3,
    }
