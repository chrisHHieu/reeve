"""Automations: how a schedule reads and when it next fires, the store of tasks and their runs, the
scheduler that runs what is due, the tools an agent makes tasks with, and a run as a conversation
the person can open, continue and be told about.

No network and no model: the scheduler's runner is a function, a run's model is scripted, and the
store is a real SQLite file.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

import pytest

from harness.integrations.automation import Schedule, ScheduledTask, Scheduler, TaskRun, TaskStore, compute_next_run
from harness.integrations.automation.tools import scheduling_tools


def _task(**fields) -> ScheduledTask:
    fields.setdefault("title", "Daily brief")
    fields.setdefault("instructions", "search the web and brief me")
    fields.setdefault("schedule", Schedule(kind="cron", cron="10 19 * * *"))
    fields.setdefault("workspace", "/tmp/cw-auto")
    return ScheduledTask(**fields)


def _replies(*texts):
    from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient

    queued = [AssistantTurn(text=text, finish_reason="stop") for text in texts]

    class _Scripted(ProviderClient):
        def complete(self, *, model, messages, tools=None, **settings):
            return queued.pop(0) if len(queued) > 1 else queued[0]

        def capabilities(self, model):
            return ModelCapabilities()

    return _Scripted()


def _manager(tmp_path, monkeypatch, provider=None):
    from harness.interface.server.manager import SessionManager

    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    return SessionManager(data_dir=tmp_path / "data", **({"provider": provider} if provider else {}))


def _in_a_workspace(manager, tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    task = _task(workspace=str(workspace), agent="reeve")
    manager.task_store.save(task)
    return task, workspace


def _due_now(store, task):
    store._conn.execute("UPDATE scheduled_tasks SET next_run=1.0 WHERE id=?", (task.id,))
    store._conn.commit()


# -- a schedule ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("cron", "reads"),
    [("10 19 * * *", "Every day at ~7:10 PM"), ("0 9 5 * *", "Monthly on day 5 at ~9:00 AM")],
)
def test_a_cron_schedule_reads_as_words(cron, reads):
    assert Schedule("cron", cron=cron).human() == reads


@pytest.mark.parametrize(("dow", "day"), [("0", "Sunday"), ("1", "Monday"), ("6", "Saturday"), ("7", "Sunday")])
def test_a_weekly_schedule_names_cron_s_own_day(dow, day):
    """Cron counts from Sunday, as 0 and as 7."""
    assert day in Schedule("cron", cron=f"0 9 * * {dow}").human()


def test_the_named_day_is_the_day_croniter_fires_on():
    """A Monday-first list indexed by cron's day number labelled every weekly schedule a day late."""
    from croniter import croniter

    monday = datetime(2026, 7, 20, 0, 0)
    for dow in range(7):
        assert croniter(f"0 9 * * {dow}", monday).get_next(datetime).strftime("%A") in Schedule("cron", cron=f"0 9 * * {dow}").human()


def test_a_once_schedule_reads_as_once_and_a_task_has_its_own_thread():
    assert Schedule("once", fire_at="2026-07-01T09:00:00").human().startswith("Once at")
    task = _task()
    assert task.task_session_id == f"__task__{task.id}" and task.public()["schedule"] == "Every day at ~7:10 PM"


def test_a_schedule_in_utc_fires_at_that_utc_time():
    task = _task(schedule=Schedule(kind="cron", cron="10 19 * * *", timezone="UTC"))
    fires = compute_next_run(task, after=datetime(2026, 6, 5, 18, 0, tzinfo=timezone.utc).timestamp())
    assert datetime.fromtimestamp(fires, tz=timezone.utc) == datetime(2026, 6, 5, 19, 10, tzinfo=timezone.utc)


def test_a_schedule_without_a_zone_fires_on_the_machines_clock():
    task = _task()
    assert task.schedule.timezone == "local"
    local = datetime.fromtimestamp(compute_next_run(task)).astimezone()
    assert (local.hour, local.minute) == (19, 10)


def test_a_once_task_in_the_past_never_fires():
    assert compute_next_run(_task(schedule=Schedule(kind="once", fire_at="2020-01-01T00:00:00+00:00"))) is None


@pytest.mark.skipif(not hasattr(time, "tzset"), reason="POSIX only: faking the machine's zone needs TZ and tzset; a named zone is covered above.")
def test_a_once_task_set_in_summer_for_a_winter_date_fires_at_its_wall_clock_time(monkeypatch):
    """Bound to the offset in effect when it was computed, it fired an hour off."""
    monkeypatch.setenv("TZ", "America/New_York")
    time.tzset()
    try:
        fires = compute_next_run(_task(schedule=Schedule(kind="once", fire_at="2026-12-25T08:00:00")), after=datetime(2026, 7, 1, 12, 0).timestamp())
        local = datetime.fromtimestamp(fires)
        assert (local.hour, local.minute, local.date()) == (8, 0, datetime(2026, 12, 25).date())
    finally:
        monkeypatch.delenv("TZ", raising=False)
        time.tzset()


# -- the store -----------------------------------------------------------------------------------


def test_a_task_is_saved_listed_due_once_its_time_passes_and_deleted(tmp_path):
    store = TaskStore(tmp_path / "auto.db")
    task = _task(schedule=Schedule(kind="cron", cron="* * * * *"))
    store.save(task)
    assert store.get(task.id).title == "Daily brief" and [t.id for t in store.list()] == [task.id]
    assert [t.id for t in store.due(now=task.next_run + 1)] == [task.id]
    task.enabled = False
    store.save(task)
    assert store.due(now=task.next_run + 1 if task.next_run else 9e9) == []  # a paused task is never due
    assert store.delete(task.id) is True and store.get(task.id) is None


def test_a_tasks_runs_are_kept(tmp_path):
    store = TaskStore(tmp_path / "auto.db")
    task = _task()
    store.save(task)
    store.add_run(TaskRun(task_id=task.id, status="ok", result_text="hi"))
    store.add_run(TaskRun(task_id=task.id, status="error", error="boom"))
    runs = store.runs(task.id)
    assert len(runs) == 2 and runs[0].status in ("ok", "error")


# -- the scheduler -------------------------------------------------------------------------------


async def test_a_due_task_is_run_once_and_moved_to_its_next_time(tmp_path):
    store = TaskStore(tmp_path / "auto.db")
    task = _task(schedule=Schedule(kind="cron", cron="* * * * *"))
    store.save(task)
    _due_now(store, task)  # `save` works next_run out again, so it is set in the table
    ran: list[str] = []
    first = asyncio.Event()

    async def runner(t, trigger):
        ran.append(t.id)
        first.set()
        return TaskRun(task_id=t.id, status="ok", trigger=trigger)

    scheduler = Scheduler(store, runner, tick_seconds=0.05)
    scheduler.start()
    # Stopped at the first run: a fixed sleep across a minute boundary lets an every-minute
    # task come due again and fire twice, rightly.
    await asyncio.wait_for(first.wait(), timeout=5.0)
    await scheduler.stop()
    assert ran == [task.id]
    advanced = store.get(task.id)
    assert (advanced.run_count, advanced.last_status) == (1, "ok") and advanced.next_run is not None and advanced.next_run > 1.0


async def test_a_run_started_while_one_is_going_is_skipped(tmp_path):
    store = TaskStore(tmp_path / "auto.db")
    task = _task()
    store.save(task)
    release = asyncio.Event()
    started = []

    async def slow(t, trigger):
        started.append(t.id)
        await release.wait()
        return TaskRun(task_id=t.id, status="ok")

    scheduler = Scheduler(store, slow)
    first = asyncio.create_task(scheduler.run_task(task, trigger="manual"))
    await asyncio.sleep(0.02)
    assert await scheduler.run_task(task, trigger="manual") is None and len(started) == 1
    release.set()
    await first


# -- the agent's tools ---------------------------------------------------------------------------


def _scheduling(store, origin, workspace="/tmp/ws"):
    return {tool.__name__: tool for tool in scheduling_tools(store, origin=origin, default_workspace=workspace)}


def test_a_task_the_agent_makes_is_confirmed_listed_and_remembers_where_it_came_from(tmp_path):
    store = TaskStore(tmp_path / "auto.db")
    tools = _scheduling(store, {"surface": "reeve", "session_id": "s1", "workspace": "/tmp/ws", "agent": "reeve"})
    create = tools["create_scheduled_task"]
    made = create(title="Brief", instructions="brief me", cron="10 19 * * *")
    assert made["ok"] and made["schedule"] == "Every day at ~7:10 PM"
    assert create.__aisuite_tool_metadata__.requires_approval is True  # the person confirms it on a card
    [listed] = tools["list_scheduled_tasks"]()["tasks"]
    assert listed["title"] == "Brief"
    saved = store.list()[0]
    assert (saved.origin_session_id, saved.workspace) == ("s1", "/tmp/ws")
    assert "invalid cron" in create(title="x", instructions="y", cron="not-a-cron")["error"]
    assert "error" in create(title="x", instructions="y")  # neither a cron nor a time


def test_a_paused_task_has_no_next_run_and_a_deleted_one_cannot_be_updated(tmp_path):
    store = TaskStore(tmp_path / "auto.db")
    tools = _scheduling(store, {"workspace": "/tmp/ws"})
    task_id = tools["create_scheduled_task"](title="X", instructions="do", cron="0 9 * * *")["id"]
    assert tools["update_scheduled_task"](id=task_id, enabled=False)["task"]["enabled"] is False
    assert store.get(task_id).next_run is None
    assert tools["delete_scheduled_task"](id=task_id)["ok"] is True
    assert tools["update_scheduled_task"](id=task_id)["error"]


# -- a run is a conversation ---------------------------------------------------------------------


async def test_a_scheduled_run_is_a_conversation_that_can_be_opened_and_continued(tmp_path, monkeypatch):
    from harness.interface.server.manager.helpers import last_assistant_text

    manager = _manager(tmp_path, monkeypatch, _replies("Daily brief: all quiet.", "Sure — here is more detail."))
    task, workspace = _in_a_workspace(manager, tmp_path)
    run = await manager._run_scheduled_task(task, trigger="manual")
    assert run.status == "ok" and run.session_id == f"__run__{run.run_id}" and run.result_text == "Daily brief: all quiet."
    record = manager.session_store.load(run.session_id)
    assert record is not None and record.workspace
    assert any("Scheduled run" in (m.get("content") or "") for m in record.messages)
    engine = manager.get_engine(run.session_id, workspace=str(workspace), agent="reeve")
    async for _ in engine.run("tell me more"):
        pass
    assert last_assistant_text(engine.messages) == "Sure — here is more detail."


def test_a_run_cannot_schedule_another_task(tmp_path, monkeypatch):
    """Told "every day at 5:32pm, prepare…", a run holding create_scheduled_task makes another
    automation instead of doing the work."""
    manager = _manager(tmp_path, monkeypatch, _replies("ok"))
    task, _ = _in_a_workspace(manager, tmp_path)
    names = set(manager._build_task_engine(task, session_id="__run__test").registry.names())
    assert not {"create_scheduled_task", "update_scheduled_task"} & names
    assert "write_file" in names  # it can still make what it was asked for


async def test_a_run_now_is_prepared_driven_live_and_then_recorded(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch, _replies("Done — briefing ready."))
    task, workspace = _in_a_workspace(manager, tmp_path)
    prepared = manager.prepare_manual_run(task.id)
    assert prepared["ok"] and prepared["session_id"] == f"__run__{prepared['run_id']}" and prepared["agent"] == "reeve"
    # The prompt carries the instructions as they are, framed to be done now rather than scheduled.
    assert task.instructions in prepared["prompt"]
    assert "do not create or modify any scheduled tasks" in prepared["prompt"]
    assert manager.task_store.runs(task.id)[0].status == "running"
    # The GUI drives the run over the session; finishing it records how it went.
    engine = manager.get_engine(prepared["session_id"], workspace=str(workspace), agent="reeve")
    async for _ in engine.run(prepared["prompt"]):
        pass
    manager.save(prepared["session_id"], engine)
    finished = manager.finalize_manual_run(task.id, prepared["run_id"])
    assert finished["ok"] and finished["run"]["status"] == "ok" and finished["run"]["result_text"] == "Done — briefing ready."
    assert manager.task_store.get(task.id).run_count == 1


async def test_scheduled_run_broadcasts_run_started_event(tmp_path, monkeypatch):
    """The moment a scheduled run starts, every /ws/events socket hears
    automation_run_started (the top-right toast). Dead sockets drop silently."""
    manager = _manager(tmp_path, monkeypatch, _replies("done"))
    task, _ = _in_a_workspace(manager, tmp_path)
    heard: list = []

    async def listener(message):
        heard.append(message)

    async def dead(message):
        raise RuntimeError("socket gone")

    manager.register_event_client(listener)
    manager.register_event_client(dead)
    run = await manager._run_scheduled_task(task, trigger="schedule")
    [event] = [m for m in heard if m["type"] == "automation_run_started"]
    assert event["data"] == {**event["data"], "task_id": task.id, "task_title": task.title, "session_id": run.session_id, "trigger": "schedule"}
    assert dead not in manager._event_clients  # dropped, not fatal


# -- the Automations page ------------------------------------------------------------------------


def test_the_automations_api_lists_pauses_reads_and_deletes_a_task(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from harness.interface.server.app import create_app

    manager = _manager(tmp_path, monkeypatch)
    task = _task(workspace=str(tmp_path / "ws"))
    manager.task_store.save(task)
    client = TestClient(create_app(manager))
    [listed] = client.get("/v1/automations").json()["tasks"]
    assert (listed["title"], listed["schedule"]) == ("Daily brief", "Every day at ~7:10 PM")
    assert client.patch(f"/v1/automations/{task.id}", json={"enabled": False}).json()["task"]["enabled"] is False
    assert client.get(f"/v1/automations/{task.id}").json()["task"]["id"] == task.id
    assert client.delete(f"/v1/automations/{task.id}").json()["ok"] is True


def test_runs_since_the_page_was_seen_are_counted_and_the_newest_says_whether_it_failed(tmp_path, monkeypatch):
    """The sidebar's badge."""
    manager = _manager(tmp_path, monkeypatch)
    task = manager.task_store.save(_task())
    # The times are set, not read off the clock: two runs made in one tick tied, and which of
    # them was "newest" was then a coin toss (seen once in a full run under load).
    now = time.time()
    manager.task_store.add_run(TaskRun(task_id=task.id, status="ok", started_at=now - 20))
    manager.task_store.add_run(TaskRun(task_id=task.id, status="error", started_at=now - 10))

    def row():
        return manager.list_automations()["tasks"][0]

    assert (row()["unseen_runs"], row()["unseen_failed"]) == (2, True)
    assert manager.mark_automation_seen(task.id)["ok"]
    assert (row()["unseen_runs"], row()["unseen_failed"]) == (0, False)
    manager.task_store.add_run(TaskRun(task_id=task.id, status="ok", started_at=manager.task_store.get(task.id).seen_runs_at + 1))
    assert (row()["unseen_runs"], row()["unseen_failed"]) == (1, False)
    assert not manager.mark_automation_seen("task-nope")["ok"]


def test_the_list_says_what_each_automation_last_came_to(tmp_path, monkeypatch):
    """A row of the Automations list carries its newest run, so it can say running, done or
    failed without a request per row; an automation that never ran carries none."""
    manager = _manager(tmp_path, monkeypatch)
    ran = manager.task_store.save(_task())
    manager.task_store.save(_task())
    manager.task_store.add_run(TaskRun(task_id=ran.id, status="error", error="the site did not answer", started_at=100.0))
    newest = TaskRun(task_id=ran.id, started_at=200.0)  # still running
    manager.task_store.add_run(newest)
    rows = {row["id"]: row for row in manager.list_automations()["tasks"]}
    last = rows[ran.id]["latest_run"]
    assert last["run_id"] == newest.run_id and last["status"] == "running"
    assert last["attention"] == 0 and last["attention_kind"] is None
    assert [row["latest_run"] for task_id, row in rows.items() if task_id != ran.id] == [None]


def test_a_run_that_waits_on_the_person_says_so_and_is_found_in_the_session_list(tmp_path, monkeypatch):
    """A run's conversation is an internal thread and no row of the session list. While it
    holds a prompt for the person it is one, named after its automation, and the run reads as
    waiting in the automation's own list and detail. Answered, it is hidden again."""
    from harness.session.sessions import SessionRecord

    manager = _manager(tmp_path, monkeypatch)
    task = manager.task_store.save(_task())
    run = TaskRun(task_id=task.id)
    manager.task_store.add_run(run)
    for sid in (run.session_id, task.task_session_id, "plain"):
        manager.session_store.save(SessionRecord(session_id=sid, workspace=str(tmp_path), model="gpt-5.5", mode="workspace-write", agent="reeve", title=sid))

    def listed():
        return {row["session_id"]: row for row in manager.list_sessions()}

    assert set(listed()) == {"plain"}  # a run that waits on nothing stays hidden
    item = manager.inbox.add_approval(run.session_id, "Approve: write_file")
    # A prompt on another internal thread does not make that thread a row.
    manager.inbox.add_approval(task.task_session_id, "Approve: run_shell")
    rows = listed()
    assert set(rows) == {"plain", run.session_id}
    row = rows[run.session_id]
    assert row["title"] == task.title and row["automation"] == {"id": task.id, "title": task.title}
    assert row["attention"] == 1 and row["attention_kind"] == "approval"
    assert "automation" not in rows["plain"]
    last = manager.list_automations()["tasks"][0]["latest_run"]
    assert last["attention"] == 1 and last["attention_kind"] == "approval"
    detail = manager.get_automation(task.id)["runs"][0]
    assert detail["attention"] == 1 and detail["attention_kind"] == "approval"
    assert manager.inbox.resolve(item.id, "allow")
    assert set(listed()) == {"plain"}
    assert manager.get_automation(task.id)["runs"][0]["attention"] == 0
