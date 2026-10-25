"""The background-jobs panel's backend: `GET /v1/sessions/{id}/jobs` and its Stop.

The engine already owned this state (one `BackgroundTask` per detached command); nothing
exposed it, so a panel had to guess from the transcript. What must hold here: a running job
is reported with its command, its folder, the dialect it was written in and its start stamp;
a finished one with its exit code, its end stamp and a duration; Stop really stops the
process (through the executor's own cross-platform tree kill, not a second path); a job that
had already finished is a no-op reported as one; and a session this process runs nothing for
— unknown, closed, or bound to a remote host — answers with an empty list rather than an
error or somebody else's jobs.

`GET …/jobs/output` is the other half: the tail of ONE job's text for a panel that polls it.
It reads through the NON-consuming `tail()`, so the regression it exists to prevent is pinned
here — the model's `shell_task_output` owns that buffer's incremental cursor, and a panel
poll must not take a byte from it. Its `tail` is query text clamped to `0..20000`, and every
failure (unknown job, unknown session, no engine, remote host) is an honest `ok: false`.

Every job started here is short-lived or killed in teardown: `LocalExecutor.close()`
deliberately leaves background tasks alone (a manager kills process trees at shutdown), so
the fixture does the cleanup itself.
"""

from __future__ import annotations

import os
import sys
import time

import pytest

from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient
from harness.interface.server import SessionManager, create_app
from harness.session.sessions import SessionRecord

_WIN = os.name == "nt"
# Quoted on both platforms: the command is a string the shell parses, and this repo's own
# shell tests learned the same lesson (a bare path with a space becomes two arguments). On
# Windows the `&` call operator is required as well — PowerShell reads a quoted path in
# command position as data, and the job dies with a parse error (measured).
_LAUNCH = f'& "{sys.executable}"' if _WIN else f'"{sys.executable}"'
EXIT_0 = f'{_LAUNCH} -c "import sys; sys.exit(0)"'
EXIT_3 = f'{_LAUNCH} -c "import sys; sys.exit(3)"'
SAYS_READY = f"{_LAUNCH} -c \"print('ready')\""
# Outlives the test by design: the Stop is the only thing that ends it.
SLEEPER = f'{_LAUNCH} -c "import time; time.sleep(30)"'
# Prints, then stays alive: the only way the OUTPUT of a RUNNING job can be read. Killed in
# the test that starts it.
SAYS_READY_THEN_WAITS = f'{_LAUNCH} -c "print(\'ready\'); import time; time.sleep(30)"'
# More than MAX_TAIL_CHARS of output, so a clamped tail is observable.
CHATTY = f"{_LAUNCH} -c \"print('x' * 30000)\""
# Prints and fails: a finished job's final text and its own exit code in one command.
PRINTS_AND_FAILS = f"{_LAUNCH} -c \"print('boom'); import sys; sys.exit(3)\""


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
    # `close()` is not enough: it interrupts what is in flight and leaves background tasks
    # running on purpose. A test box must not carry a 30-second python into the next test.
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


def _session(mgr: SessionManager, session_id: str, **kwargs) -> SessionRecord:
    record = SessionRecord(
        session_id=session_id,
        model="gpt-5.5",
        mode="interactive",
        **{"workspace": str(mgr.default_workspace), **kwargs},
    )
    mgr.session_store.save(record)
    return record


def _executor(mgr: SessionManager, session_id: str):
    engine = mgr.get_engine(session_id)
    assert engine is not None
    return engine.executor


def _rows(mgr: SessionManager, session_id: str) -> list[dict]:
    return mgr.background_jobs(session_id)["jobs"]


def _job(mgr: SessionManager, session_id: str, job_id: str) -> dict:
    return next(row for row in _rows(mgr, session_id) if row["id"] == job_id)


def _tail(mgr: SessionManager, session_id: str, job_id: str, chars=None) -> dict:
    """The panel's poll of one job's output, through the manager (so its clamp applies)."""
    return mgr.background_job_output(session_id, job_id, chars)


def _wait_until(predicate, *, seconds: float = 20.0) -> bool:
    """Bounded wait on the PROCESS (never by reading output, which moves the cursor the
    model's `shell_task_output` owns). A hung box fails the assert, it does not hang."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


def _drained(task, *, seconds: float = 20.0) -> bool:
    """Wait until the process is gone AND its reader thread has stopped appending.

    `status == "finished"` is `poll()`, which answers as soon as the process exited while the
    last bytes may still be sitting in the pipe. Reading a finished job's buffer to compare
    against needs this narrower fact. Nothing here touches the cursor.
    """
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        before = task.chars_read
        time.sleep(0.05)
        if task.done and task.chars_read == before:
            return True
    return False


# -- the read --------------------------------------------------------------------------


def test_a_running_job_reads_running_then_finished_with_a_real_exit_code(manager):
    _session(manager, "s-jobs")
    executor = _executor(manager, "s-jobs")
    started = executor.run_background(EXIT_0)
    job_id = started["task_id"]

    running = _job(manager, "s-jobs", job_id)
    assert running["status"] == "running"
    assert running["exit_code"] is None
    assert running["ended_at"] is None and running["duration_ms"] is None
    assert running["command"] == EXIT_0
    assert running["cwd"] == executor.cwd  # where the command ran, not where the panel reads
    assert running["shell"] in {shell.kind for shell in executor.shells()}
    assert running["killed"] is False

    assert _wait_until(
        lambda: _job(manager, "s-jobs", job_id)["status"] == "finished"
    ), f"the job never finished: {_job(manager, 's-jobs', job_id)}"
    done = _job(manager, "s-jobs", job_id)
    assert done["exit_code"] == 0
    assert done["ended_at"] >= done["started_at"]
    assert done["duration_ms"] >= 0
    assert done["killed"] is False


def test_a_failing_job_reports_a_nonzero_exit_code(manager):
    """The code a background task reports is the SHELL's own: a background script runs as-is,
    with no trailer to read, so bash (POSIX) hands back the command's 3 while PowerShell
    collapses any failing command to 1. Either one is a failure — the fact the panel shows."""
    _session(manager, "s-fail")
    executor = _executor(manager, "s-fail")
    started = executor.run_background(EXIT_3)
    job_id = started["task_id"]

    assert _wait_until(
        lambda: _job(manager, "s-fail", job_id)["status"] == "finished"
    ), f"the job never finished: {_job(manager, 's-fail', job_id)}"
    exit_code = _job(manager, "s-fail", job_id)["exit_code"]
    assert exit_code not in (None, 0)
    if not _WIN:
        assert exit_code == 3


def test_the_panel_read_leaves_the_output_cursor_alone(manager):
    """Reading the panel must not eat the buffer `shell_task_output` reads incrementally: the
    model's next look at the task would show nothing, and the log is the only record of what
    the command printed. Both panel reads are checked — the list and the output tail."""
    _session(manager, "s-read")
    executor = _executor(manager, "s-read")
    started = executor.run_background(SAYS_READY)
    job_id = started["task_id"]
    assert _wait_until(
        lambda: _job(manager, "s-read", job_id)["status"] == "finished"
    ), "the job never finished"

    _rows(manager, "s-read")
    assert "ready" in _tail(manager, "s-read", job_id, 4_000)["output"]
    _rows(manager, "s-read")
    assert "ready" in _tail(manager, "s-read", job_id, 4_000)["output"]

    # The MODEL's read still gets everything the panel was just shown.
    assert "ready" in executor.background_output(job_id)["output"]
    # …and the panel still shows it once the model has read it: the model consumes a cursor,
    # not the buffer, so a second panel poll is unchanged while the model gets nothing new.
    assert executor.background_output(job_id)["output"] == ""
    assert "ready" in _tail(manager, "s-read", job_id, 4_000)["output"]


def test_jobs_are_newest_first(manager):
    _session(manager, "s-order")
    executor = _executor(manager, "s-order")
    older = executor.run_background(EXIT_0)
    newer = executor.run_background(SLEEPER)
    try:
        ids = [row["id"] for row in _rows(manager, "s-order")]
        assert ids.index(newer["task_id"]) < ids.index(older["task_id"])
    finally:
        executor.background_kill(newer["task_id"])


def test_a_session_with_no_engine_reports_nothing(manager):
    """The app restarted, or the session was never opened here: this process is running
    nothing for it, which is an empty list and not a failure."""
    assert manager.background_jobs("never-opened") == {"jobs": []}
    assert manager.background_jobs("") == {"jobs": []}


# -- the output tail (a panel polling one job) ------------------------------------------


def test_a_running_job_tail_returns_what_it_has_printed(manager):
    """The panel's poll of a job that has not exited: the text so far, and `truncated` false
    because all of it fits the tail it asked for."""
    _session(manager, "s-tail-live")
    executor = _executor(manager, "s-tail-live")
    started = executor.run_background(SAYS_READY_THEN_WAITS)
    job_id = started["task_id"]
    try:
        assert _wait_until(
            lambda: "ready" in _tail(manager, "s-tail-live", job_id)["output"]
        ), "the job never printed while running"
        body = _tail(manager, "s-tail-live", job_id)
        assert body["ok"] is True
        assert body["id"] == job_id
        assert body["status"] == "running" and body["exit_code"] is None
        assert body["output"].strip() == "ready"
        assert body["truncated"] is False
        assert body["total_chars"] == len(body["output"])
    finally:
        executor.background_kill(job_id)


def test_a_tail_shorter_than_the_output_returns_the_end_and_says_it_cut(manager):
    """The last N characters, not the first: a panel showing a log wants the verdict at the
    bottom. `total_chars` is the buffer's own count, so "cut" is measured rather than guessed."""
    _session(manager, "s-tail-cut")
    executor = _executor(manager, "s-tail-cut")
    started = executor.run_background(CHATTY)
    job_id = started["task_id"]
    task = executor._bg_tasks[job_id]
    assert _drained(task), "the job's output never settled"

    whole = task.tail(10 ** 9)  # the buffer itself, for the arithmetic the route reports
    assert len(whole) >= 30_000

    body = _tail(manager, "s-tail-cut", job_id, 100)
    assert body["ok"] is True
    assert body["status"] == "finished" and body["exit_code"] == 0
    assert body["output"] == whole[-100:]
    assert len(body["output"]) == 100 and set(body["output"].strip()) == {"x"}
    assert body["truncated"] is True
    assert body["total_chars"] == len(whole)  # the honest total, not the returned length


def test_the_tail_clamps_a_huge_a_negative_and_a_non_numeric_query(manager):
    """`tail` is query text a panel composes: a number outside `0..20000` takes the nearer
    bound and text that is not a number takes the default, so a poll always answers."""
    _session(manager, "s-tail-clamp")
    executor = _executor(manager, "s-tail-clamp")
    started = executor.run_background(CHATTY)
    job_id = started["task_id"]
    assert _drained(executor._bg_tasks[job_id]), "the job's output never settled"

    huge = _tail(manager, "s-tail-clamp", job_id, 10 ** 9)
    assert huge["ok"] is True
    assert len(huge["output"]) == 20_000  # the ceiling, observable in the returned length
    assert huge["truncated"] is True
    total = huge["total_chars"]

    negative = _tail(manager, "s-tail-clamp", job_id, -5)
    assert negative["output"] == "" and negative["truncated"] is True
    assert negative["total_chars"] == total  # the metadata is unaffected by the clamp

    zero = _tail(manager, "s-tail-clamp", job_id, 0)
    assert zero["output"] == "" and zero["truncated"] is True

    unparseable = _tail(manager, "s-tail-clamp", job_id, "abc")
    assert len(unparseable["output"]) == 4_000  # the default — not 0, not the ceiling
    assert unparseable["truncated"] is True

    in_range_text = _tail(manager, "s-tail-clamp", job_id, "500")
    assert len(in_range_text["output"]) == 500 and in_range_text["truncated"] is True

    # The executor clamps on its own too, for a caller that never went through the manager.
    assert len(executor.background_tail(job_id, 10 ** 9)["output"]) == 20_000


def test_a_finished_job_still_reports_its_output_and_exit(manager):
    """A finished job keeps its text: the panel opens the job it was told about, which may
    well have ended while the user was looking elsewhere."""
    _session(manager, "s-tail-done")
    executor = _executor(manager, "s-tail-done")
    started = executor.run_background(PRINTS_AND_FAILS)
    job_id = started["task_id"]
    task = executor._bg_tasks[job_id]
    assert _drained(task), "the job's output never settled"

    body = _tail(manager, "s-tail-done", job_id)
    assert body["ok"] is True and body["id"] == job_id
    assert body["status"] == "finished"
    assert body["exit_code"] not in (None, 0)  # the command's own, not the panel's guess
    assert "boom" in body["output"]
    assert body["truncated"] is False
    assert body["total_chars"] == len(task.tail(10 ** 9))


def test_the_tail_answers_honestly_for_a_session_or_job_it_does_not_have(manager):
    """Same vocabulary as Stop: a sentence, never an exception. An id off a stale list is an
    error rather than an empty read — a panel named it and deserves to be told it is gone."""
    unknown = _tail(manager, "never-opened", "bg-1")
    assert unknown["ok"] is False and unknown["error"]
    assert "not running here" in unknown["error"]

    # A session row with no engine here (the app restarted): still nothing to read.
    _session(manager, "s-record-only")
    assert _tail(manager, "s-record-only", "bg-1")["ok"] is False

    _session(manager, "s-tail-known")
    _executor(manager, "s-tail-known")
    missing = _tail(manager, "s-tail-known", "bg-99")
    assert missing == {"ok": False, "error": "No job bg-99 in this session."}
    unnamed = _tail(manager, "s-tail-known", "")
    assert unnamed["ok"] is False and unnamed["error"]

    _session(manager, "s-tail-remote", workspace="/srv/app", environment_id="env-1")
    remote = _tail(manager, "s-tail-remote", "bg-1")
    assert remote["ok"] is False and "remote host" in remote["error"]


# -- observing one job (the stream's gate) ----------------------------------------------


def test_observe_counts_up_and_down_and_clamps_at_zero(manager):
    """The observe call itself: `on` increments, `off` decrements, and a release with nothing
    held (or one too many) clamps at zero instead of going negative — which would silence a
    job that IS being watched. The job runs while all of this happens, so the count is the
    live job's."""
    _session(manager, "s-observe")
    executor = _executor(manager, "s-observe")
    started = executor.run_background(SAYS_READY_THEN_WAITS)
    job_id = started["task_id"]
    try:
        assert manager.shell_observed_count("s-observe", job_id) == 0  # nobody watching yet
        first = manager.observe_background_job("s-observe", job_id, True)
        assert first == {"ok": True, "id": job_id, "observing": 1}
        assert manager.shell_observed_count("s-observe", job_id) == 1
        assert manager.observe_background_job("s-observe", job_id, True)["observing"] == 2
        assert manager.observe_background_job("s-observe", job_id, False)["observing"] == 1
        assert manager.observe_background_job("s-observe", job_id, False)["observing"] == 0
        assert manager.observe_background_job("s-observe", job_id, False)["observing"] == 0
        assert manager.shell_observed_count("s-observe", job_id) == 0
    finally:
        executor.background_kill(job_id)


def test_observe_answers_honestly_for_a_session_or_job_it_does_not_have(manager):
    """Same vocabulary as Stop and the tail: a sentence, never an exception. An id off a stale
    list is an ERROR rather than a silent no-op — the client named it and deserves to know its
    count is gone — and it names the id exactly as the sibling routes do."""
    _session(manager, "s-observe-known")
    _executor(manager, "s-observe-known")
    missing = manager.observe_background_job("s-observe-known", "bg-99", True)
    assert missing == {"ok": False, "error": "No job bg-99 in this session."}
    unnamed = manager.observe_background_job("s-observe-known", "  ", True)
    assert unnamed == {"ok": False, "error": "No job was named."}

    # A session this process runs nothing for: the same sentence the tail route uses.
    unknown = manager.observe_background_job("never-opened", "bg-1", True)
    assert unknown["ok"] is False and "not running here" in unknown["error"]

    # A session row with no engine here (the app restarted): still nothing to observe, and no
    # count invented for it.
    _session(manager, "s-observe-record-only")
    record_only = manager.observe_background_job("s-observe-record-only", "bg-1", True)
    assert record_only["ok"] is False and "not running here" in record_only["error"]

    # A remote-bound session: its shell runs on the other host, so there is no local job here
    # to stream — the tail route's remote sentence, for observing.
    _session(manager, "s-observe-remote", workspace="/srv/app", environment_id="env-1")
    remote = manager.observe_background_job("s-observe-remote", "bg-1", True)
    assert remote["ok"] is False and "remote host" in remote["error"]


def test_the_observe_route_serves_the_panel(manager):
    """The wire shape a panel binds to, key for key: request `{id, on}`, answer
    `{ok, id, observing}` — and a job that is already over answers with a sentence, because its
    count was dropped when it settled and there is nothing left to stream."""
    from fastapi.testclient import TestClient

    _session(manager, "s-wire-observe")
    executor = _executor(manager, "s-wire-observe")
    started = executor.run_background(SAYS_READY_THEN_WAITS)
    job_id = started["task_id"]
    client = TestClient(create_app(manager))
    try:
        on = client.post(
            "/v1/sessions/s-wire-observe/jobs/observe", json={"id": job_id, "on": True}
        )
        assert on.status_code == 200
        assert on.json() == {"ok": True, "id": job_id, "observing": 1}
        off = client.post(
            "/v1/sessions/s-wire-observe/jobs/observe", json={"id": job_id, "on": False}
        )
        assert off.json() == {"ok": True, "id": job_id, "observing": 0}

        # A body with no `on` at all means "start watching" (the panel's open).
        again = client.post(
            "/v1/sessions/s-wire-observe/jobs/observe", json={"id": job_id}
        ).json()
        assert again == {"ok": True, "id": job_id, "observing": 1}

        gone = client.post(
            "/v1/sessions/s-wire-observe/jobs/observe", json={"id": "bg-88", "on": True}
        ).json()
        assert gone == {"ok": False, "error": "No job bg-88 in this session."}
        assert (
            client.post(
                "/v1/sessions/nope/jobs/observe", json={"id": "bg-1", "on": True}
            ).json()["ok"]
            is False
        )

        # The job ends, and the count dies with it (the settle edge): observing a job that
        # is over is a sentence, not a resurrected count.
        executor.background_kill(job_id)
        assert _wait_until(
            lambda: manager.shell_observed_count("s-wire-observe", job_id) == 0
        ), "a settled job kept its observation count"
        settled = client.post(
            "/v1/sessions/s-wire-observe/jobs/observe", json={"id": job_id, "on": True}
        ).json()
        assert settled == {
            "ok": False,
            "error": f"Job {job_id} has finished; there is nothing left to stream.",
        }
    finally:
        executor.background_kill(job_id)


# -- the stop --------------------------------------------------------------------------


def test_stop_ends_a_long_running_job(manager):
    _session(manager, "s-stop")
    executor = _executor(manager, "s-stop")
    started = executor.run_background(SLEEPER)
    job_id = started["task_id"]
    task = executor._bg_tasks[job_id]
    assert _job(manager, "s-stop", job_id)["status"] == "running"

    result = manager.kill_background_job("s-stop", job_id)

    assert result["ok"] is True
    assert result["already_finished"] is False
    assert result["status"] == "killed" and result["exit_code"] is not None
    # The PROCESS is gone, not just the bookkeeping: `poll()` reaps the child we spawned.
    assert task.proc.poll() is not None, "the process outlived the Stop"
    done = _job(manager, "s-stop", job_id)
    assert done["status"] == "finished" and done["killed"] is True
    assert done["duration_ms"] is not None


def test_stop_on_a_finished_job_is_an_honest_no_op(manager):
    """The panel's Stop can land after the job finished on its own. That is not an error and
    not a kill: the exit code it reports is the command's own, and the job is not relabelled
    as something somebody stopped."""
    _session(manager, "s-late")
    executor = _executor(manager, "s-late")
    started = executor.run_background(EXIT_0)
    job_id = started["task_id"]
    assert _wait_until(
        lambda: _job(manager, "s-late", job_id)["status"] == "finished"
    ), "the job never finished"

    result = manager.kill_background_job("s-late", job_id)

    assert result["ok"] is True
    assert result["already_finished"] is True
    assert result["exit_code"] == 0
    assert _job(manager, "s-late", job_id)["killed"] is False


def test_stop_answers_honestly_for_a_session_or_job_it_does_not_have(manager):
    assert manager.kill_background_job("never-opened", "bg-1")["ok"] is False
    assert "not running here" in manager.kill_background_job("never-opened", "bg-1")["error"]

    _session(manager, "s-known")
    _executor(manager, "s-known")
    missing = manager.kill_background_job("s-known", "bg-99")
    assert missing["ok"] is False and "bg-99" in missing["error"]
    unnamed = manager.kill_background_job("s-known", "")
    assert unnamed["ok"] is False and unnamed["error"]


def test_a_remote_bound_session_has_no_local_jobs(manager):
    """Its shell runs on the other host. An empty list is the truth about THIS process —
    reporting a local executor's jobs as the remote session's would be somebody else's."""
    _session(manager, "s-remote", workspace="/srv/app", environment_id="env-1")

    assert manager.background_jobs("s-remote") == {"jobs": []}
    stopped = manager.kill_background_job("s-remote", "bg-1")
    assert stopped["ok"] is False and "remote host" in stopped["error"]

    # …and the read stays empty for a LIVE remote session for the same reason: the remote
    # executor has no jobs view at all, so the `callable` check above already covers it.
    from harness.orchestration.remote_env.executor import RemoteExecutor

    assert not hasattr(RemoteExecutor, "background_jobs")


# -- the wire --------------------------------------------------------------------------


def test_the_routes_serve_the_panel(manager):
    from fastapi.testclient import TestClient

    _session(manager, "s-wire")
    executor = _executor(manager, "s-wire")
    started = executor.run_background(SLEEPER)
    client = TestClient(create_app(manager))

    body = client.get("/v1/sessions/s-wire/jobs").json()
    assert [row["id"] for row in body["jobs"]] == [started["task_id"]]
    row = body["jobs"][0]
    assert row["status"] == "running" and row["command"] == SLEEPER
    # The wire shape a panel binds to, key for key.
    assert set(row) == {
        "id",
        "command",
        "cwd",
        "shell",
        "status",
        "started_at",
        "ended_at",
        "exit_code",
        "duration_ms",
        "killed",
        "output_chars",
        "last_line",
    }

    stopped = client.post(
        "/v1/sessions/s-wire/jobs/kill", json={"id": started["task_id"]}
    ).json()
    assert stopped["ok"] is True and stopped["already_finished"] is False

    # A second Stop, the job now over: a no-op, not an error.
    again = client.post(
        "/v1/sessions/s-wire/jobs/kill", json={"id": started["task_id"]}
    ).json()
    assert again["ok"] is True and again["already_finished"] is True

    # An unknown session reads empty; the Stop says why it cannot help.
    assert client.get("/v1/sessions/nope/jobs").json() == {"jobs": []}
    unknown = client.post("/v1/sessions/nope/jobs/kill", json={"id": "bg-1"}).json()
    assert unknown["ok"] is False and unknown["error"]


def test_the_output_route_serves_the_panel(manager):
    """The tail route's wire shape, key for key, and a `tail` that is not a number answering
    with a body instead of FastAPI's 422 — this one is parsed as text on purpose."""
    from fastapi.testclient import TestClient

    _session(manager, "s-wire-out")
    executor = _executor(manager, "s-wire-out")
    started = executor.run_background(SAYS_READY)
    job_id = started["task_id"]
    assert _drained(executor._bg_tasks[job_id]), "the job's output never settled"
    client = TestClient(create_app(manager))

    response = client.get(f"/v1/sessions/s-wire-out/jobs/output?id={job_id}&tail=abc")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True and body["id"] == job_id
    assert body["status"] == "finished" and body["exit_code"] == 0
    assert isinstance(body["output"], str) and isinstance(body["total_chars"], int)
    assert set(body) == {
        "ok",
        "id",
        "status",
        "exit_code",
        "output",
        "truncated",
        "total_chars",
    }

    # No `tail` at all: the default, and still this shape.
    default = client.get(f"/v1/sessions/s-wire-out/jobs/output?id={job_id}").json()
    assert default["ok"] is True and default["truncated"] is False
    assert "ready" in default["output"]

    # An unknown session and a job that is not there both answer with a sentence.
    assert client.get("/v1/sessions/nope/jobs/output?id=bg-1").json()["ok"] is False
    missing = client.get(
        "/v1/sessions/s-wire-out/jobs/output?id=bg-77"
    ).json()
    assert missing["ok"] is False and "bg-77" in missing["error"]
