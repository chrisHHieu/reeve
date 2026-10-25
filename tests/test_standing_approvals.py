"""Standing approvals on an automation: a rule that lets one tool reach one
target every time the task runs, granted when the task is made or from a run's approval card,
matched in the permission path, and revoked from the task page.

No network and no model: tools carry hand-made metadata, and a run's approval is answered by
resolving its inbox item.
"""

from __future__ import annotations

import asyncio

import aisuite as ai
import pytest

from harness.integrations.automation import Schedule, ScheduledTask, Scheduler, TaskRun, TaskStore
from harness.integrations.automation.models import grant_entries, rule_entry, rule_parts
from harness.integrations.automation.tools import scheduling_tools
from harness.loop import ApprovalOutcome, PermissionRequest
from harness.safety.permissions import Mode, PermissionEngine, standing_rule_candidate

CHANNEL = "slack:T1/C1"


class _Meta:
    """A tool's metadata: needs approval (an external action) and has a category."""

    def __init__(self, category="messaging", requires_approval=True):
        self.category = category
        self.requires_approval = requires_approval


def _task(**fields) -> ScheduledTask:
    fields.setdefault("title", "Weekly digest")
    fields.setdefault("instructions", "summarize the week and post it")
    fields.setdefault("schedule", Schedule(kind="cron", cron="0 9 * * 1"))
    fields.setdefault("workspace", "/tmp/cw-standing")
    return ScheduledTask(**fields)


def _answers_ok():
    from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient

    class _Ok(ProviderClient):
        def complete(self, *, model, messages, tools=None, **settings):
            return AssistantTurn(text="ok", finish_reason="stop")

        def capabilities(self, model):
            return ModelCapabilities()

    return _Ok()


def _manager(tmp_path, monkeypatch):
    from harness.interface.server.manager import SessionManager

    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    return SessionManager(data_dir=tmp_path / "data", provider=_answers_ok())


def _with_a_run(manager, tmp_path, **task_fields):
    workspace = tmp_path / "ws"
    workspace.mkdir(exist_ok=True)
    task = _task(workspace=str(workspace), agent="reeve", **task_fields)
    manager.task_store.save(task)
    run = TaskRun(task_id=task.id)
    manager.task_store.add_run(run)
    return task, run, workspace


async def _answer_when_parked(manager, session_id, answer, check=None):
    for _ in range(500):
        pending = manager.inbox.pending(session_id)
        if pending:
            if check:
                check(pending[0])
            await manager.resolve_inbox(pending[0].id, answer)
            return
        await asyncio.sleep(0.001)
    raise AssertionError("the approval never parked")


# -- a rule ---------------------------------------------------------------------------------------


def test_a_rule_is_a_tool_and_a_target_and_an_old_rule_is_a_tool_alone():
    assert rule_entry("send_message", CHANNEL) == f"send_message {CHANNEL}"
    assert rule_parts(f"send_message {CHANNEL}") == ("send_message", CHANNEL)
    assert rule_entry("web_search") == "web_search" and rule_parts("web_search") == ("web_search", None)


def test_a_task_adds_each_rule_once_revokes_it_and_lists_both_kinds():
    task = _task(always_allowed_tools=[f"send_message {CHANNEL}", "web_search"])
    assert task.standing_rules() == {"send_message": {CHANNEL}} and task.name_allowed_tools() == {"web_search"}
    assert task.add_rule("send_message", "slack:T1/C2") is True
    assert task.add_rule("send_message", "slack:T1/C2") is False
    assert task.add_rule("send_message", "") is False
    assert task.revoke_rule("send_message slack:T1/C2") is True and task.revoke_rule("send_message slack:T1/C2") is False
    listed = task.public()["always_allowed"]
    assert {"entry": f"send_message {CHANNEL}", "tool": "send_message", "target": CHANNEL} in listed
    assert {"entry": "web_search", "tool": "web_search", "target": None} in listed


def test_only_a_write_to_a_named_target_of_a_tool_that_takes_one_becomes_a_grant():
    grants = grant_entries([
        {"tool": "send_message", "target": CHANNEL, "access": "write"},
        {"tool": "send_message", "target": CHANNEL, "access": "write"},  # the same again
        {"tool": "github_list_commits", "target": "r/x", "access": "read"},  # a read is disclosure
        {"tool": "run_shell", "target": "rm -rf /", "access": "write"},  # a command has no target
        {"tool": "github_create_issue", "target": "r/x", "access": "write"},  # declares no target argument
        {"tool": "send_message", "target": "", "access": "write"},
        "garbage",
    ])
    assert grants == [f"send_message {CHANNEL}"] and grant_entries(None) == []


@pytest.mark.parametrize(
    ("tool", "arguments", "metadata", "target"),
    [
        ("send_message", {"target": "slack:C1"}, _Meta(), "slack:C1"),
        ("run_shell", {"command": "ls"}, _Meta(), None),  # a command is never eligible
        ("github_create_issue", {"repo": "x"}, _Meta("connector"), None),  # no declared target argument
        ("send_message", {"text": "hi"}, _Meta(), None),  # the call names no target
        ("write_file", {"path": "a"}, None, None),  # local writes are scoped by path instead
    ],
)
def test_which_calls_could_become_a_standing_rule(tool, arguments, metadata, target):
    assert standing_rule_candidate(tool, arguments, metadata) == target


# -- matching ------------------------------------------------------------------------------------


def test_a_rule_allows_its_target_and_asks_for_any_other(tmp_path):
    engine = PermissionEngine(workspace_root=tmp_path, task_rules={"send_message": {CHANNEL}})
    allowed = engine.evaluate("send_message", {"target": CHANNEL, "text": "hi"}, _Meta())
    assert allowed.allowed and allowed.rule == f"send_message → {CHANNEL}"
    other = engine.evaluate("send_message", {"target": "slack:T1/C2", "text": "hi"}, _Meta())
    assert not other.allowed and other.needs_user


def test_a_rule_reaches_a_connector_tool_that_a_session_allowance_never_does(tmp_path):
    """Session allowances leave connector tools out; a task rule's exact target is what makes
    letting one through safe (gap 4)."""
    engine = PermissionEngine(workspace_root=tmp_path, task_rules={"redmine_comment_issue": {"42"}})
    engine.allow_tool_for_session("redmine_comment_issue")
    metadata = _Meta(category="connector")
    assert not engine.evaluate("redmine_comment_issue", {"issue_id": "41", "notes": "x"}, metadata).allowed
    hit = engine.evaluate("redmine_comment_issue", {"issue_id": "42", "notes": "x"}, metadata)
    assert hit.allowed and "standing rule" in hit.reason


def test_a_rule_adds_to_the_runs_permissions_and_never_lifts_plan_mode(tmp_path):
    engine = PermissionEngine(workspace_root=tmp_path, plan=True, task_rules={"send_message": {"slack:C1"}})
    assert not engine.evaluate("send_message", {"target": "slack:C1"}, _Meta()).allowed


# -- granting ------------------------------------------------------------------------------------


def test_the_agent_can_ask_for_grants_when_it_makes_a_task_but_never_when_it_updates_one(tmp_path):
    store = TaskStore(tmp_path / "auto.db")
    tools = {tool.__name__: tool for tool in scheduling_tools(store, origin={"workspace": str(tmp_path)}, default_workspace=str(tmp_path))}
    create, update = tools["create_scheduled_task"], tools["update_scheduled_task"]
    assert "permissions" in create.__tool_schema__["function"]["parameters"]["properties"]
    made = create(title="Weekly digest", instructions="post it", cron="0 9 * * 1", permissions=[
        {"tool": "send_message", "target": CHANNEL, "access": "write"},
        {"tool": "github_list_commits", "target": "r/x", "access": "read"},
    ])
    assert made["ok"] and made["always_allowed"] == [f"send_message {CHANNEL}"]
    assert store.get(made["id"]).always_allowed_tools == [f"send_message {CHANNEL}"]
    # After that, only a person adds a rule.
    assert "permissions" not in update.__tool_schema__["function"]["parameters"]["properties"]


def test_a_run_session_is_traced_back_to_its_task(tmp_path):
    store = TaskStore(tmp_path / "auto.db")
    task = _task()
    store.save(task)
    run = TaskRun(task_id=task.id)
    store.add_run(run)
    owner = store.task_for_run_session(run.session_id)
    assert owner is not None and owner.id == task.id
    assert store.task_for_run_session("session-xyz") is None and store.task_for_run_session("__run__missing") is None


async def test_allow_every_time_on_a_runs_card_keeps_a_rule_for_exactly_that_target(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch)
    task, run, _ = _with_a_run(manager, tmp_path)
    request = PermissionRequest(tool_name="send_message", arguments={"target": CHANNEL, "text": "digest"}, metadata=_Meta(), reason="requires approval", tool_call_id="tc1")

    def offers_a_rule(item):
        # What the card needs to offer "Allow every time": the task and the exact target.
        assert item.data["task_id"] == task.id and item.data["standing_target"] == CHANNEL

    outcome, _ = await asyncio.gather(manager._scheduled_approver(task, run.session_id)(request), _answer_when_parked(manager, run.session_id, "always_task", offers_a_rule))
    assert outcome is ApprovalOutcome.ONCE
    assert f"send_message {CHANNEL}" in manager.task_store.get(task.id).always_allowed_tools
    engine = manager._build_task_engine(manager.task_store.get(task.id), session_id=run.session_id)
    hit = engine.permissions.evaluate("send_message", {"target": CHANNEL}, _Meta())
    assert hit.allowed and hit.rule
    assert not engine.permissions.evaluate("send_message", {"target": "slack:T1/C2"}, _Meta()).allowed


async def test_an_old_name_only_rule_runs_unasked_and_a_denied_call_adds_nothing(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch)
    task, run, _ = _with_a_run(manager, tmp_path, always_allowed_tools=["web_search"])
    approver = manager._scheduled_approver(task, run.session_id)
    assert await approver(PermissionRequest("web_search", {"query": "x"}, _Meta(), "", tool_call_id="tc1")) is ApprovalOutcome.ONCE
    assert not manager.inbox.pending(run.session_id)
    request = PermissionRequest("send_message", {"target": CHANNEL}, _Meta(), "", tool_call_id="tc2")
    outcome, _ = await asyncio.gather(approver(request), _answer_when_parked(manager, run.session_id, "deny"))
    assert outcome is ApprovalOutcome.DENY
    assert manager.task_store.get(task.id).always_allowed_tools == ["web_search"]


def test_a_rule_is_kept_only_from_a_run_session_and_for_an_eligible_call(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch)
    task = _task()
    manager.task_store.save(task)
    run = TaskRun(task_id=task.id)  # the session an automation's run works in
    manager.task_store.add_run(run)
    assert not manager.mint_task_rule("session-1", "send_message", {"target": "slack:C1"}, _Meta())
    assert not manager.mint_task_rule(run.session_id, "run_shell", {"command": "ls"}, _Meta())
    assert not manager.mint_task_rule(run.session_id, "github_create_issue", {"repo": "x"}, _Meta())
    assert manager.mint_task_rule(run.session_id, "send_message", {"target": "slack:C1"}, _Meta())
    assert manager.task_store.get(task.id).always_allowed_tools == ["send_message slack:C1"]


def test_a_run_engine_rebuilt_from_the_session_carries_the_tasks_rules(tmp_path, monkeypatch):
    """"Run now" and a resumed run build their engine from the session."""
    manager = _manager(tmp_path, monkeypatch)
    _, run, workspace = _with_a_run(manager, tmp_path, always_allowed_tools=[f"send_message {CHANNEL}"])
    engine = manager.get_engine(run.session_id, workspace=str(workspace), agent="reeve")
    assert engine.permissions.task_rules == {"send_message": {CHANNEL}}


def test_grants_at_creation_and_a_revoke_from_the_task_page(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch)
    made = manager.create_automation({"title": "Weekly digest", "instructions": "post it", "cron": "0 9 * * 1", "permissions": [
        {"tool": "send_message", "target": CHANNEL, "access": "write"},
        {"tool": "github_list_commits", "target": "r/x", "access": "read"},
    ]})
    assert made["ok"] and made["task"]["always_allowed"] == [{"entry": f"send_message {CHANNEL}", "tool": "send_message", "target": CHANNEL}]
    revoked = manager.update_automation(made["task"]["id"], {"revoke": f"send_message {CHANNEL}"})
    assert revoked["ok"] and revoked["task"]["always_allowed"] == []
    assert manager.task_store.get(made["task"]["id"]).always_allowed_tools == []


# -- the scheduler -------------------------------------------------------------------------------


async def test_a_run_waiting_for_a_person_does_not_hold_up_the_other_tasks(tmp_path):
    store = TaskStore(tmp_path / "auto.db")
    blocked, quick = _task(title="blocked"), _task(title="quick")
    for task in (blocked, quick):
        store.save(task)
        store._conn.execute("UPDATE scheduled_tasks SET next_run=1.0 WHERE id=?", (task.id,))
    store._conn.commit()
    answered = asyncio.Event()

    async def runner(task, trigger):
        if task.id == blocked.id:
            await answered.wait()  # an approval nobody has answered yet
        return TaskRun(task_id=task.id, status="ok", trigger=trigger)

    scheduler = Scheduler(store, runner, tick_seconds=0.05)
    scheduler.start()
    await asyncio.sleep(0.2)
    assert store.get(quick.id).run_count == 1 and store.get(blocked.id).run_count == 0
    answered.set()
    await asyncio.sleep(0.1)
    assert store.get(blocked.id).run_count == 1
    await scheduler.stop()


async def test_a_due_snapshot_from_before_a_run_finished_does_not_run_it_again(tmp_path):
    # Flaked under the full suite as run_count == 2: a tick read `due()` while the previous
    # run was still in flight and spawned a run from that snapshot; the in-flight run then
    # finished and left `_running_ids` before the spawned one started, so the overlap guard
    # let a second run through. An automation that posts somewhere posted twice.
    store = TaskStore(tmp_path / "auto.db")
    task = _task(title="once")
    store.save(task)
    runs: list[str] = []

    async def runner(t, trigger):
        runs.append(t.id)
        return TaskRun(task_id=t.id, status="ok", trigger=trigger)

    scheduler = Scheduler(store, runner, tick_seconds=60)
    stale = store.get(task.id)  # what the tick read while the first run was still going
    await scheduler.run_task(store.get(task.id), trigger="schedule")  # the in-flight run lands
    assert await scheduler.run_task(stale, trigger="schedule") is None
    assert runs == [task.id] and store.get(task.id).run_count == 1


# -- what the engine says about a rule -----------------------------------------------------------


def test_the_approval_names_the_target_a_rule_could_pin_and_an_allowed_call_cites_its_rule(tmp_path):
    from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall
    from harness.loop import TurnEngine
    from harness.session.events import EventType
    from harness.tools import ToolRegistry

    def send_message(target: str, text: str):
        return {"ok": True}

    send_message.__aisuite_tool_metadata__ = ai.ToolMetadata(name="send_message", category="messaging", risk_level="medium", capabilities=["messaging"], requires_approval=True)
    send_message.__tool_schema__ = {"type": "function", "function": {"name": "send_message", "description": "send", "parameters": {"type": "object", "properties": {}}}}
    registry = ToolRegistry()
    registry.register_all([send_message])

    class _Posts(ProviderClient):
        def __init__(self):
            self._turns = [
                AssistantTurn(tool_calls=[ToolCall(id="c1", name="send_message", arguments={"target": CHANNEL, "text": "hi"})], finish_reason="tool_calls"),
                AssistantTurn(text="sent", finish_reason="stop"),
            ]

        def complete(self, *, model, messages, tools=None, **settings):
            return self._turns.pop(0)

        def capabilities(self, model):
            return ModelCapabilities()

    def events(**engine_fields):
        engine = TurnEngine(provider=_Posts(), registry=registry, model="gpt-5.5", **engine_fields)

        async def run():
            return [event async for event in engine.run("post it")]

        return asyncio.run(run())

    async def approve_once(request):
        return ApprovalOutcome.ONCE

    asked = next(e for e in events(permissions=PermissionEngine(workspace_root=tmp_path), approver=approve_once) if e.type == EventType.PERMISSION_REQUIRED)
    assert asked.data["standing_target"] == CHANNEL

    audit: list[dict] = []
    granted = events(permissions=PermissionEngine(workspace_root=tmp_path, task_rules={"send_message": {CHANNEL}}), audit_sink=audit.append)
    assert not any(e.type == EventType.PERMISSION_REQUIRED for e in granted)
    assert next(e for e in granted if e.type == EventType.TOOL_FINISHED).data["standing_rule"] == f"send_message → {CHANNEL}"
    # Every call a rule allowed writes an audit row that cites it.
    cited = [row for row in audit if row.get("stage") == "auto_allowed"]
    assert cited and f"send_message → {CHANNEL}" in cited[0]["reason"]


async def test_task_mode_reaches_the_run_engine(tmp_path, monkeypatch):
    """The editor's permission control is the chat composer's mode list, stored on the
    task: bypass builds a bypass engine, discuss a read-only one, and no mode keeps the
    unattended default — interactive, with the deliverable-write auto-allow."""
    manager = _manager(tmp_path, monkeypatch)
    workspace = tmp_path / "ws"
    workspace.mkdir()

    def mode(**fields):
        return manager._build_task_engine(_task(workspace=str(workspace), agent="reeve", **fields), session_id=f"run-{fields.get('mode', 'legacy')}").permissions.mode

    assert mode(mode="auto") is Mode.DANGER_FULL_ACCESS
    assert mode(mode="discuss") is Mode.READ_ONLY
    assert mode() is Mode.WORKSPACE_WRITE
    legacy = _task(workspace=str(workspace), agent="reeve")
    request = PermissionRequest(tool_name="write_file", arguments={"path": str(workspace / "out.md"), "content": "x"}, metadata=_Meta(), reason="requires approval", tool_call_id="tc1")
    assert await asyncio.wait_for(manager._scheduled_approver(legacy, "run-legacy")(request), timeout=5) is ApprovalOutcome.ONCE
