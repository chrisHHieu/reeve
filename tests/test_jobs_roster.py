"""The jobs roster is PUSHED on the session's socket.

A panel used to ask `GET …/jobs`, and only on a clock it started once it already knew a job was
running — so a job the agent launched in the middle of a turn was not on screen until the turn
ended. What must hold here: a job that joins the registry (started in the background, or
adopted from the foreground) puts the roster on the socket at once; so does one whose process
ends, however it ended, a killed one included (it sends no completion notice); a job that is
PRINTING moves its row's last line at the roster's own pace and no faster; and none of it
costs anything when nobody is connected.

Every job started here is short-lived or killed in teardown.
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
from harness.interface.server.manager import jobs_roster
from harness.session.sessions import SessionRecord
from harness.tools.shell_jobs import LAST_LINE_CHARS, last_line

_WIN = os.name == "nt"
_LAUNCH = f'& "{sys.executable}"' if _WIN else f'"{sys.executable}"'
EXIT_0 = f'{_LAUNCH} -c "import sys; sys.exit(0)"'
SLEEPER = f'{_LAUNCH} -c "import time; time.sleep(30)"'
SAYS_READY_THEN_WAITS = (
    _LAUNCH + " -c \"print('ready', flush=True); import time; time.sleep(30)\""
)
#: The last thing it says before it leaves — the line the process's own exit cannot promise is
#: in the buffer yet, and the pipe's close can.
PRINTS_AND_LEAVES = _LAUNCH + " -c \"print('one'); print('last words'); print()\""


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


@contextlib.contextmanager
def _live_loop(manager):
    """The manager's loop, running on a thread: the seam that proves a frame reached a socket."""
    loop = asyncio.new_event_loop()
    manager._loop = loop
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    try:
        yield loop
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=5)
        loop.close()
        manager._loop = None


def _session(mgr: SessionManager, session_id: str) -> SessionRecord:
    record = SessionRecord(
        session_id=session_id,
        model="gpt-5.5",
        mode="interactive",
        workspace=str(mgr.default_workspace),
    )
    mgr.session_store.save(record)
    return record


def _executor(mgr: SessionManager, session_id: str):
    engine = mgr.get_engine(session_id)
    assert engine is not None
    return engine.executor


def _follow(mgr: SessionManager, session_id: str) -> list[dict]:
    """One client on the session's socket, as the WS driver registers one. It asks for nothing."""
    frames: list[dict] = []

    async def cb(message):
        frames.append(message)

    mgr.register_session_client(session_id, cb)
    return frames


def _rosters(frames: list[dict]) -> list[list[dict]]:
    return [frame["data"]["jobs"] for frame in list(frames) if frame.get("type") == "jobs"]


def _row(roster: list[dict], job_id: str) -> dict:
    return next((row for row in roster if row["id"] == job_id), {})


def _wait_until(predicate, *, seconds: float = 20.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def _seen(frames: list[dict], job_id: str, **facts) -> bool:
    """Some pushed roster shows the job with these facts."""
    return any(
        all(_row(roster, job_id).get(key) == value for key, value in facts.items())
        and bool(_row(roster, job_id))
        for roster in _rosters(frames)
    )


# -- the whole path: the model starts a job in the middle of a turn -----------------------


class _StartsAJob(ProviderClient):
    """Step one calls `run_shell` in the background; step two answers."""

    def __init__(self, command: str) -> None:
        self.command = command
        self.steps = 0

    def complete(self, *, model, messages, tools=None, **settings):
        from harness.llm.providers import ToolCall

        self.steps += 1
        if self.steps == 1:
            return AssistantTurn(
                tool_calls=[
                    ToolCall(
                        id="call_bg",
                        name="run_shell",
                        arguments={"command": self.command, "run_in_background": True},
                    )
                ],
                finish_reason="tool_calls",
            )
        return AssistantTurn(text="it is running in the background", finish_reason="stop")

    def capabilities(self, model):
        return ModelCapabilities()


def test_a_job_the_model_starts_mid_turn_reaches_the_socket_before_the_turn_ends(
    tmp_path, monkeypatch
):
    """The regression this ADR exists for, through the real engine, executor, manager and
    websocket: the panel used to learn of this job when the turn was OVER (it re-read the list
    only when the session's running flag flipped). The client here asks for nothing."""
    from fastapi.testclient import TestClient

    from harness.interface.server import create_app

    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    ws_dir = tmp_path / "ws"
    ws_dir.mkdir()
    mgr = SessionManager(workspace=ws_dir, provider=_StartsAJob(SLEEPER))
    # The app's loop is bound at startup (`start_gateway`), which a bare TestClient does not
    # run: bind it where the socket registers, which is on that loop.
    register = mgr.register_session_client

    def registering(session_id, send_cb):
        mgr._loop = asyncio.get_running_loop()
        return register(session_id, send_cb)

    monkeypatch.setattr(mgr, "register_session_client", registering)
    client = TestClient(create_app(mgr))
    seen: list[dict] = []
    try:
        with client.websocket_connect("/ws/session/s-live") as ws:
            assert ws.receive_json()["type"] == "ready"
            ws.send_json({"type": "user_message", "text": "start the server"})
            while True:
                event = ws.receive_json()
                seen.append(event)
                if event["type"] == "permission_required":
                    ws.send_json({"type": "approval", "decision": "once"})
                if event["type"] == "turn_done":
                    break
    finally:
        for engine in list(mgr._engines.values()):
            for task_id in list(getattr(engine.executor, "_bg_tasks", {}) or {}):
                engine.executor.background_kill(task_id)

    kinds = [event["type"] for event in seen]
    assert "jobs" in kinds, f"no roster reached the socket: {kinds}"
    first = kinds.index("jobs")
    roster = seen[first]["data"]["jobs"]
    assert [row["status"] for row in roster] == ["running"]
    assert roster[0]["command"] == SLEEPER
    # Before the turn ended — and before the model's second step was even shown.
    assert first < kinds.index("turn_end"), kinds
    assert first < max(i for i, kind in enumerate(kinds) if kind == "assistant_message"), kinds


# -- lifecycle: pushed at once -----------------------------------------------------------


def test_a_job_that_starts_is_pushed_without_being_asked_for(manager):
    _session(manager, "s-start")
    executor = _executor(manager, "s-start")
    with _live_loop(manager):
        frames = _follow(manager, "s-start")
        started = executor.run_background(SLEEPER)
        job_id = started["task_id"]
        assert _wait_until(lambda: _seen(frames, job_id, status="running"), seconds=5), (
            f"no roster was pushed for the job that started: {frames}"
        )
        row = _row(_rosters(frames)[-1], job_id)
        assert row["command"] == SLEEPER and row["exit_code"] is None
        # The whole set, in the shape the route serves: a client needs no second read.
        assert _rosters(frames)[-1] == manager.background_jobs("s-start")["jobs"]


def test_a_job_that_ends_is_pushed_with_its_exit_code(manager):
    _session(manager, "s-end")
    executor = _executor(manager, "s-end")
    with _live_loop(manager):
        frames = _follow(manager, "s-end")
        job_id = executor.run_background(EXIT_0)["task_id"]
        assert _wait_until(
            lambda: _seen(frames, job_id, status="finished", exit_code=0, killed=False)
        ), f"the finish was never pushed: {_rosters(frames)}"


def test_a_killed_job_is_pushed_although_it_reports_no_completion(manager):
    _session(manager, "s-kill")
    executor = _executor(manager, "s-kill")
    with _live_loop(manager):
        frames = _follow(manager, "s-kill")
        job_id = executor.run_background(SLEEPER)["task_id"]
        assert _wait_until(lambda: _seen(frames, job_id, status="running"), seconds=5)
        assert manager.kill_background_job("s-kill", job_id)["ok"] is True
        assert _wait_until(
            lambda: _seen(frames, job_id, status="finished", killed=True)
        ), f"the stop was never pushed: {_rosters(frames)}"


def test_a_command_adopted_from_the_foreground_joins_the_pushed_roster(manager):
    """A foreground command whose budget ran out is handed to the registry (`_adopt`), which
    is the second door into the list — and the one a panel that only heard about
    `run_in_background` would miss."""
    _session(manager, "s-adopt")
    executor = _executor(manager, "s-adopt")
    with _live_loop(manager):
        frames = _follow(manager, "s-adopt")
        task = executor._task_for(SLEEPER, SLEEPER, executor._shell_for(None))
        assert _rosters(frames) == []  # a foreground command is not a job
        executor._adopt(task)
        assert _wait_until(lambda: _seen(frames, task.id, status="running"), seconds=5)


def test_the_last_words_of_a_job_that_left_are_in_its_final_row(manager):
    """The process leaves before its last lines are read. The roster pushed when the PIPE
    closes is the one that carries them."""
    _session(manager, "s-last")
    executor = _executor(manager, "s-last")
    with _live_loop(manager):
        frames = _follow(manager, "s-last")
        job_id = executor.run_background(PRINTS_AND_LEAVES)["task_id"]
        assert _wait_until(
            lambda: _seen(frames, job_id, status="finished", last_line="last words")
        ), f"the final row never carried the last line: {_rosters(frames)}"
        final = _row(_rosters(frames)[-1], job_id)
        assert final["output_chars"] == len("one\nlast words\n\n")


def test_a_closed_pipe_is_a_roster_change_of_its_own(manager):
    """Fed by hand, so the frame can only come from the close: with a real job the process's
    exit usually lands in the same instant and would hide a close that pushed nothing."""
    _session(manager, "s-close")
    executor = _executor(manager, "s-close")
    with _live_loop(manager):
        job_id = executor.run_background(SLEEPER)["task_id"]
        frames = _follow(manager, "s-close")
        closed = {"task_id": job_id, "chunk": "", "total": 0, "ended": True, "closed": True}
        manager._shell_output("s-close", closed)
        assert _wait_until(lambda: len(_rosters(frames)) == 1, seconds=5), frames


class _Process:
    """A process a test ends by hand."""

    def __init__(self) -> None:
        self.gone = threading.Event()
        self.returncode = 0

    def wait(self) -> int:
        self.gone.wait(20)
        return self.returncode


class _Task:
    id = "bg-9"
    command = "x"
    generation = 0
    killed = True  # a killed task reports no completion — and still left the roster
    on_output = staticmethod(lambda text, ended: None)
    chars_read = 0

    def __init__(self) -> None:
        self.proc = _Process()
        self.finished = False

    def note_finished(self) -> None:
        self.finished = True

    def tail(self, chars: int) -> str:
        return ""


def test_the_watcher_reports_the_roster_when_a_task_joins_and_when_its_process_ends():
    from harness.tools.shell_jobs import watch_background

    task = _Task()
    seen: list[bool] = []
    completions: list[dict] = []
    watch_background(
        task,
        on_finish=completions.append,
        on_output=None,
        on_change=lambda: seen.append(task.finished),
    )
    assert seen == [False]  # joined: told at once, on the caller's thread
    task.proc.gone.set()
    assert _wait_until(lambda: len(seen) == 2, seconds=5)
    assert seen == [False, True]  # ended: told after the finish stamp, so the row reads finished
    assert completions == []


def test_a_raising_roster_listener_cannot_stop_the_watcher():
    from harness.tools.shell_jobs import watch_background

    task = _Task()
    task.killed = False
    completions: list[dict] = []

    def boom() -> None:
        raise RuntimeError("a panel fell over")

    watch_background(task, on_finish=completions.append, on_output=None, on_change=boom)
    task.proc.gone.set()
    assert _wait_until(lambda: len(completions) == 1, seconds=5)


# -- printing: paced ---------------------------------------------------------------------


def test_a_printing_job_moves_its_row_without_anybody_observing_it(manager):
    _session(manager, "s-print")
    executor = _executor(manager, "s-print")
    with _live_loop(manager):
        frames = _follow(manager, "s-print")
        job_id = executor.run_background(SAYS_READY_THEN_WAITS)["task_id"]
        assert manager.shell_observed_count("s-print", job_id) == 0
        assert _wait_until(
            lambda: _seen(frames, job_id, status="running", last_line="ready")
        ), f"the row never showed what the job printed: {_rosters(frames)}"
        # ...and the text itself still went nowhere: that stream is for a job somebody opened.
        assert [f for f in frames if f.get("type") == "job_output"] == []


def test_a_job_that_prints_fast_costs_one_frame_per_interval(manager, monkeypatch):
    """Two hundred flushes inside one interval: one frame at the leading edge, one when the
    interval closes — never one per flush."""
    monkeypatch.setattr(jobs_roster, "JOBS_FRAME_SECONDS", 0.4)
    _session(manager, "s-fast")
    executor = _executor(manager, "s-fast")
    with _live_loop(manager):
        job_id = executor.run_background(SLEEPER)["task_id"]
        frames = _follow(manager, "s-fast")  # after the start: only printing frames below
        began = time.monotonic()
        for _ in range(200):
            manager._jobs_changed("s-fast", printing=True)
        assert _wait_until(lambda: len(_rosters(frames)) == 1, seconds=2)
        assert _wait_until(lambda: len(_rosters(frames)) == 2, seconds=3), _rosters(frames)
        assert time.monotonic() - began >= 0.35  # the second one waited for the interval
        time.sleep(0.6)
        assert len(_rosters(frames)) == 2  # nothing printed since: nothing more is sent
        assert _row(_rosters(frames)[-1], job_id)["status"] == "running"


def test_a_lifecycle_change_does_not_wait_for_the_interval(manager, monkeypatch):
    monkeypatch.setattr(jobs_roster, "JOBS_FRAME_SECONDS", 30.0)
    _session(manager, "s-now")
    executor = _executor(manager, "s-now")
    with _live_loop(manager):
        job_id = executor.run_background(SLEEPER)["task_id"]
        frames = _follow(manager, "s-now")
        manager._jobs_changed("s-now", printing=True)  # leading edge: sent
        manager._jobs_changed("s-now", printing=True)  # inside the interval: a timer, 30 s out
        assert _wait_until(lambda: "s-now" in manager._jobs_due, seconds=2)
        # The leading frame's send is a task of its own: wait for it, then hold the count.
        assert _wait_until(lambda: len(_rosters(frames)) == 1, seconds=2)
        time.sleep(0.2)
        assert len(_rosters(frames)) == 1
        assert manager.kill_background_job("s-now", job_id)["ok"] is True
        assert _wait_until(lambda: _seen(frames, job_id, status="finished", killed=True))
        # The timer the printing armed is gone with it: no frame is owed any more.
        assert _wait_until(lambda: "s-now" not in manager._jobs_due, seconds=2)
        assert manager._jobs_timers == {}
        # Nothing is running, so there is no pace left to keep.
        assert _wait_until(lambda: "s-now" not in manager._jobs_sent, seconds=2)


def test_frames_go_out_one_at_a_time_and_the_last_one_is_the_newest_roster(manager):
    """A whole-set frame is only self-correcting if the last one a client gets is the newest.
    Here the socket is slow: three changes arrive while the first frame is still being sent.
    They become ONE more frame, read after the last of them — never three racing sends."""
    _session(manager, "s-slow")
    executor = _executor(manager, "s-slow")
    with _live_loop(manager) as loop:
        first = executor.run_background(SLEEPER)["task_id"]
        frames: list[dict] = []
        entered = threading.Event()
        release: list[asyncio.Event] = []

        async def slow_socket(message):
            if message.get("type") != "jobs":
                return
            if not release:
                release.append(asyncio.Event())
                entered.set()
                await release[0].wait()  # the first send is stuck on the wire
            frames.append(message)

        manager.register_session_client("s-slow", slow_socket)
        manager._jobs_changed("s-slow")
        assert entered.wait(5), "the first frame was never sent"
        # While it is stuck: a job joins, and two more changes are reported.
        second = executor.run_background(SLEEPER)["task_id"]
        manager._jobs_changed("s-slow")
        manager._jobs_changed("s-slow")
        assert _wait_until(lambda: "s-slow" in manager._jobs_again, seconds=5)
        assert frames == []
        loop.call_soon_threadsafe(release[0].set)
        assert _wait_until(lambda: len(frames) == 2, seconds=5), frames
        time.sleep(0.3)
        assert len(frames) == 2  # one stuck frame, one for everything since
        # The stuck frame was read before the second job existed; the last one has both.
        assert [row["id"] for row in frames[0]["data"]["jobs"]] == [first]
        assert {row["id"] for row in frames[-1]["data"]["jobs"]} == {first, second}
        assert manager._jobs_sending == set() and manager._jobs_again == set()


# -- what it costs when nobody is there --------------------------------------------------


class _NoLoop:
    """A loop that must not be touched (for a caller on the test's own thread, which sees it
    raise — the executor's threads swallow whatever a display channel raises)."""

    def is_closed(self) -> bool:
        raise AssertionError("the loop was consulted for work that needed none")

    def call_soon_threadsafe(self, *args, **kwargs):
        raise AssertionError("work was scheduled that needed none")


def test_a_session_nobody_is_connected_to_schedules_nothing(manager, monkeypatch):
    """A whole job's life — start, a line, a stop — with no client on the socket: not one hop
    to the loop on the roster's account."""
    _session(manager, "s-alone")
    executor = _executor(manager, "s-alone")
    hops: list[tuple] = []
    monkeypatch.setattr(
        manager, "_schedule_jobs_frame", lambda *args: hops.append(args), raising=True
    )
    with _live_loop(manager) as loop:
        job_id = executor.run_background(SAYS_READY_THEN_WAITS)["task_id"]
        task = executor._bg_tasks[job_id]
        assert _wait_until(lambda: task.chars_read > 0)
        time.sleep(0.3)  # past the stream's own flush window: the reader has called in
        manager.kill_background_job("s-alone", job_id)
        assert _wait_until(lambda: task.done and task.output_ended)
        # Whatever was handed to the loop has run by the time this marker does.
        ran = threading.Event()
        loop.call_soon_threadsafe(ran.set)
        assert ran.wait(5)
    assert hops == []
    assert manager._jobs_timers == {} and manager._jobs_due == set()
    # The list is still there for whoever connects and reads it.
    row = next(r for r in manager.background_jobs("s-alone")["jobs"] if r["id"] == job_id)
    assert row["last_line"] == "ready" and row["killed"] is True


def test_a_frame_that_is_already_due_is_not_asked_for_again(manager, monkeypatch):
    """The reader calls in for every flush. Once a frame is due, that call is a set lookup and
    not a hop to the loop."""
    monkeypatch.setattr(jobs_roster, "JOBS_FRAME_SECONDS", 30.0)
    _session(manager, "s-due")
    executor = _executor(manager, "s-due")
    with _live_loop(manager):
        executor.run_background(SLEEPER)
        _follow(manager, "s-due")
        manager._jobs_changed("s-due", printing=True)
        manager._jobs_changed("s-due", printing=True)
        assert _wait_until(lambda: "s-due" in manager._jobs_due, seconds=2)
        real, manager._loop = manager._loop, _NoLoop()
        try:
            manager._jobs_changed("s-due", printing=True)  # would raise if it reached the loop
        finally:
            manager._loop = real


# -- the row's one line ------------------------------------------------------------------


def test_the_last_line_is_what_a_terminal_would_be_showing():
    assert last_line("built in 4.31s\n") == "built in 4.31s"
    assert last_line("built in 4.31s\r\n") == "built in 4.31s"
    # A progress bar rewrites its own line: the reader sees what is on it now.
    assert last_line(" 10%\r 55%\r 80% done\n") == "80% done"
    assert last_line("") == ""
    long = "x" * (LAST_LINE_CHARS * 3)
    assert len(last_line(long)) == LAST_LINE_CHARS and last_line(long).endswith("…")


def test_the_route_serves_the_same_two_fields(manager):
    """A client that connects reads the list once; it must find the row a frame would carry."""
    _session(manager, "s-route")
    executor = _executor(manager, "s-route")
    job_id = executor.run_background(PRINTS_AND_LEAVES)["task_id"]
    task = executor._bg_tasks[job_id]
    assert _wait_until(lambda: task.done and task.output_ended)
    row = next(r for r in manager.background_jobs("s-route")["jobs"] if r["id"] == job_id)
    # The blank line it ended on is not "the last thing it said".
    assert row["last_line"] == "last words"
    assert row["output_chars"] == task.chars_read == len("one\nlast words\n\n")
