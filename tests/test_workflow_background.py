"""A workflow that runs in the background, as a child session.

Driven through the real SessionManager, engine and child sessions, with a provider that
answers by what it is asked (no network, no model). What must hold: the parent's call
returns at once with a running envelope and its turn ends; the script runs in a runner
child whose agents are real sessions under it; the runner calls no model; the script's
return value is injected into the parent under the runner's id, with the agents' files
and spend; nothing the model calls can reach the runner directly.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time

from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall
from harness.llm.providers.base import TokenUsage
from harness.interface.server.manager import SessionManager
from harness.session.sessions import SessionRecord
from harness.tools.workflow import WORKFLOW_RUNNER, WORKFLOW_STARTED

SCRIPT = """
phase("ask")
a = agent("AGENT one", label="first")
b = agent("AGENT two", label="second")
return {"answers": [a, b]}
"""


class Routed(ProviderClient):
    """The parent asks for the workflow once; an agent answers its own prompt; anything
    else (the parent reading the injected result) is acknowledged."""

    def __init__(self, workflow_args: dict) -> None:
        self.workflow_args = workflow_args
        self.calls: list[list[dict]] = []
        #: How many more times a person's message is answered with the workflow call.
        self.to_call = 1

    def complete(self, *, model, messages, tools=None, **settings):
        self.calls.append(messages)
        users = [str(m.get("content") or "") for m in messages if m.get("role") == "user"]
        last = users[-1] if users else ""
        if "AGENT " in last:
            word = last.split("AGENT ", 1)[1].split()[0]
            return AssistantTurn(
                text=f"report {word}", finish_reason="stop", usage=TokenUsage(input=100, output=10)
            )
        if "<task id=" in last:
            return AssistantTurn(text="read the workflow result", finish_reason="stop")
        if self.to_call > 0 and messages[-1].get("role") == "user":
            self.to_call -= 1
            return AssistantTurn(
                tool_calls=[ToolCall(id="wf-1", name="workflow", arguments=self.workflow_args)],
                finish_reason="tool_calls",
            )
        return AssistantTurn(text="started it", finish_reason="stop")

    def capabilities(self, model):
        return ModelCapabilities()


def _setup(tmp_path, **workflow_args):
    args = {"script": SCRIPT, "meta": {"name": "two-asks", "description": "ask twice"}, **workflow_args}
    provider = Routed(args)
    mgr = SessionManager(workspace=tmp_path, provider=provider)
    mgr.session_store.save(
        SessionRecord(
            session_id="parent-1",
            workspace=str(mgr.default_workspace or "."),
            model="gpt-5.5",
            mode="danger-full-access",
            messages=[],
            title="a conversation",
            agent="reeve",
        )
    )
    return mgr, provider


async def _wait(mgr: SessionManager, timeout: float = 30.0) -> None:
    """Until every background job has ended and delivered its result. A job leaves the
    registry before its result is injected, so the job's own thread (a bare
    manager runs each on one) is what says the injection is done."""
    deadline = time.monotonic() + timeout
    threads: list = []
    while time.monotonic() < deadline:
        threads += [
            t for t in list(mgr._background_tasks.values())
            if isinstance(t, threading.Thread) and t not in threads
        ]
        if not (mgr._background_tasks or mgr._background_parents or any(t.is_alive() for t in threads)):
            break
        await asyncio.sleep(0.02)


def _run(mgr: SessionManager) -> None:
    async def main():
        await mgr.deliver_to_session("parent-1", "audit both")
        await _wait(mgr)

    asyncio.run(main())


def _injected(mgr: SessionManager) -> list[dict]:
    record = mgr.session_store.load("parent-1")
    return [
        m for m in record.messages
        if isinstance(m.get("source"), dict) and m["source"].get("synthetic") == "background_task"
    ]


def test_the_call_returns_at_once_and_the_result_arrives_as_a_message(tmp_path):
    mgr, provider = _setup(tmp_path)
    _run(mgr)

    parent = mgr.session_store.load("parent-1")
    tool_results = [str(m.get("content") or "") for m in parent.messages if m.get("role") == "tool"]
    # The call answered with the running envelope, not the script's value.
    assert len(tool_results) == 1
    assert 'state="running"' in tool_results[0] and WORKFLOW_STARTED.split("\n")[0] in tool_results[0]
    assert "report one" not in tool_results[0]

    (runner_id,) = mgr.children_of("parent-1")
    runner = mgr.session_store.load(runner_id)
    assert runner.kind == "subagent" and runner.agent == WORKFLOW_RUNNER
    agents = mgr.children_of(runner_id)
    assert len(agents) == 2

    injected = _injected(mgr)
    assert len(injected) == 1 and injected[0]["source"]["session_id"] == runner_id
    content = injected[0]["content"]
    assert f'<task id="{runner_id}" state="completed">' in content
    assert "Background task completed: two-asks workflow" in content
    assert json.loads(content.split("<task_result>", 1)[1].split("\n[", 1)[0]) == {
        "answers": ["report one", "report two"]
    }
    # The parent model read it, and the parent's budget was handed the AGENTS' spend.
    assert any("<task id=" in str(m.get("content")) for call in provider.calls for m in call)


def test_a_reopened_session_can_tell_the_run_and_its_report_from_a_subagents(tmp_path):
    """The stored call names its runner (`_presentation.run_id`), so a reload still lists the
    run in the subagents roster; the report says it is a workflow and how many agents worked,
    instead of "Subagent finished" over a runner whose transcript is one call (owner, 2026-10-13)."""
    mgr, _ = _setup(tmp_path)
    _run(mgr)
    (runner_id,) = mgr.children_of("parent-1")
    parent = mgr.session_store.load("parent-1")
    (call,) = [m for m in parent.messages if m.get("role") == "tool"]
    assert call["_presentation"]["run_id"] == runner_id
    (report,) = _injected(mgr)
    assert report["source"]["workflow"] is True and report["source"]["agents"] == 2


def test_the_runner_calls_no_model_and_its_transcript_reads_as_what_happened(tmp_path):
    mgr, provider = _setup(tmp_path)
    _run(mgr)
    (runner_id,) = mgr.children_of("parent-1")
    runner = mgr.session_store.load(runner_id)
    # No request the provider saw came from the runner: its prompt never reached a model.
    assert "workflow the conversation started" in str(runner.messages[1]["content"])
    assert not any("workflow the conversation started" in str(m.get("content")) for call in provider.calls for m in call)
    calls = [m for m in runner.messages if m.get("role") == "assistant" and m.get("tool_calls")]
    assert len(calls) == 1 and calls[0]["tool_calls"][0]["function"]["name"] == "workflow"
    assert runner.messages[-1]["role"] == "assistant"
    assert "report two" in str(runner.messages[-1]["content"])


def test_the_agents_spend_and_files_are_the_runners_result(tmp_path):
    mgr, _ = _setup(tmp_path)
    _run(mgr)
    (runner_id,) = mgr.children_of("parent-1")
    # The settle fold, read back over the agents' own transcripts: each spent 110.
    result = mgr._settle_workflow_result(runner_id, {"task_id": runner_id, "tokens": 0})
    assert result["tokens"] == 220 and result["changed_files"] == []
    first, second = mgr.children_of(runner_id)
    for sid, path, added in ((first, "a.py", 3), (second, "a.py", 2)):
        record = mgr.session_store.load(sid)
        record.messages.append(
            {"role": "notice", "kind": "changes",
             "changes": {"created": [path], "stats": [{"path": path, "added": added, "removed": 0}]}}
        )
        mgr.session_store.save(record)
    files = mgr._settle_workflow_result(runner_id, {})["changed_files"]
    assert files == [{"path": "a.py", "change": "created", "added": 5, "removed": 0}]


def test_the_runner_is_not_a_level_of_nesting(tmp_path):
    """The default `subagent_depth = 1` lets the parent's agents exist; a runner between
    them must not use that level up, or no background script could launch an agent."""
    mgr, _ = _setup(tmp_path)
    _run(mgr)
    (runner_id,) = mgr.children_of("parent-1")
    assert mgr.session_depth(runner_id) == 0
    assert [mgr.session_depth(a) for a in mgr.children_of(runner_id)] == [1, 1]


def test_waiting_is_still_there_when_asked_for(tmp_path):
    mgr, _ = _setup(tmp_path, background=False)
    _run(mgr)
    children = mgr.children_of("parent-1")
    # No runner: the agents are the parent's own children, and the value came in the call.
    assert all(mgr.session_store.load(c).agent != WORKFLOW_RUNNER for c in children)
    parent = mgr.session_store.load("parent-1")
    (result,) = [str(m.get("content") or "") for m in parent.messages if m.get("role") == "tool"]
    assert '"report one"' in result and _injected(mgr) == []


def test_a_resumed_background_run_replays_from_the_parents_journal(tmp_path):
    mgr, provider = _setup(tmp_path)
    _run(mgr)
    asked_before = sum(1 for call in provider.calls if "AGENT " in str(call[-1].get("content")))
    assert asked_before == 2
    provider.workflow_args = {**provider.workflow_args, "resume": True}
    provider.to_call = 1
    _run(mgr)
    asked_after = sum(1 for call in provider.calls if "AGENT " in str(call[-1].get("content")))
    # A second runner really ran, and found the first one's journal beside the PARENT's
    # log: no agent was asked again, and the value it returned is the replayed one.
    first, second = mgr.children_of("parent-1")
    assert mgr.session_store.load(second).agent == WORKFLOW_RUNNER and mgr.children_of(second) == []
    assert asked_after == asked_before
    injected = _injected(mgr)
    assert len(injected) == 2 and injected[-1]["source"]["session_id"] == second
    assert '"report one"' in injected[-1]["content"]


def test_nothing_the_model_calls_reaches_the_runner(tmp_path):
    mgr, _ = _setup(tmp_path)
    _run(mgr)
    (runner_id,) = mgr.children_of("parent-1")

    async def attempts():
        by_name = await mgr.spawn_subagent(
            parent_id="parent-1", description="x", prompt="run it", agent_name=WORKFLOW_RUNNER
        )
        by_id = await mgr.spawn_subagent(
            parent_id="parent-1", description="x", prompt="again", agent_name="general", task_id=runner_id
        )
        return by_name, by_id

    by_name, by_id = asyncio.run(attempts())
    assert "Unknown agent type: workflow-runner" in by_name["error"]
    # A message to a run says it reads none, and how to stop it.
    assert "is a workflow run" in by_id["error"] and "stop=true" in by_id["error"]


def _tool_names(tools) -> set[str]:
    names = set()
    for spec in tools or []:
        if isinstance(spec, dict):
            names.add(str((spec.get("function") or {}).get("name") or spec.get("name") or ""))
        else:
            names.add(str(getattr(spec, "name", "") or getattr(spec, "__name__", "")))
    return names


def test_a_background_runs_agents_hold_the_tools_of_the_session_that_ran_it(tmp_path):
    """A child copies its parent's tools, and a background run's agents had the RUNNER as
    their parent, which holds `workflow` and `task` only: every agent came up without a file
    tool (a real run, 2026-10-13: "read_file is not a registered tool", one reviewer gave up
    "NOT INSPECTED"). They hold what an agent of a foreground run holds."""
    offered: list[set[str]] = []

    class Watching(Routed):
        def complete(self, *, model, messages, tools=None, **settings):
            users = [str(m.get("content") or "") for m in messages if m.get("role") == "user"]
            if users and "AGENT " in users[-1]:
                offered.append(_tool_names(tools))
            return super().complete(model=model, messages=messages, tools=tools, **settings)

    mgr, provider = _setup(tmp_path)
    mgr.provider = Watching(provider.workflow_args)
    _run(mgr)
    assert len(offered) == 2
    for names in offered:
        assert {"read_file", "grep", "run_shell"} <= names, sorted(names)


def test_a_message_to_a_running_workflow_is_refused_not_swallowed(tmp_path):
    """The case the old answer lied about: the run is still going, its agents are at work,
    and a message used to be answered "delivered" into a runner that reads nothing."""
    release = threading.Event()

    class Slow(Routed):
        def complete(self, *, model, messages, tools=None, **settings):
            users = [str(m.get("content") or "") for m in messages if m.get("role") == "user"]
            if users and "AGENT " in users[-1]:
                release.wait(10)
            return super().complete(model=model, messages=messages, tools=tools, **settings)

    mgr, provider = _setup(tmp_path)
    mgr.provider = Slow(provider.workflow_args)

    async def main():
        await mgr.deliver_to_session("parent-1", "audit both")
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            runners = mgr.children_of("parent-1")
            if runners and mgr.children_of(runners[0]):
                break
            await asyncio.sleep(0.02)
        (runner_id,) = mgr.children_of("parent-1")
        answer = await mgr.spawn_subagent(
            parent_id="parent-1", description="", prompt="mind negative numbers",
            agent_name="", task_id=runner_id,
        )
        release.set()
        await _wait(mgr)
        return runner_id, answer

    runner_id, answer = asyncio.run(main())
    assert answer.get("status") != "running"
    assert f"{runner_id} is a workflow run" in answer["error"]
    # Nothing reached the runner's transcript.
    runner = mgr.session_store.load(runner_id)
    assert not any("negative numbers" in str(m.get("content")) for m in runner.messages)


def test_an_agent_of_a_workflow_thinks_at_the_effort_its_script_gave_it(tmp_path, monkeypatch):
    from harness.llm.providers import variants

    monkeypatch.setattr(variants, "levels_for", lambda model, facts=None: ["low", "high"])
    script = 'a = agent("AGENT one", effort="low")\nb = agent("AGENT two", effort="high")\nreturn [a, b]'
    mgr, _ = _setup(tmp_path, script=script)
    _run(mgr)
    (runner_id,) = mgr.children_of("parent-1")
    levels = sorted(mgr.session_store.load(c).thinking for c in mgr.children_of(runner_id))
    assert levels == ["high", "low"]
    assert '"report one"' in _injected(mgr)[-1]["content"]


def test_the_runner_does_not_ask_again_for_the_call_the_parent_approved(tmp_path, monkeypatch):
    """Where a workflow call asks, the ask is the PARENT's: the runner executes that same
    call, and a second ask would park the run on nobody's dock. A workflow is not gated
    today (the risk table declares it a read; each `agent()` is a `task` judged on its
    own), so the test turns the gate on the way a risk override or a later decision
    would: the runner's ruleset, not the policy's silence, is what must keep it quiet."""
    from harness.safety import risk

    monkeypatch.setitem(risk.BUILTIN_RISK, "workflow", risk.RiskClass.EXEC)
    mgr, _ = _setup(tmp_path)
    record = mgr.session_store.load("parent-1")
    record.mode = "interactive"
    mgr.session_store.save(record)
    asked: list[dict] = []
    real = mgr._broker_approver

    def watching(child_id, parent_id):
        approve = real(child_id, parent_id)

        def answer(*a, **kw):
            asked.append({"child": child_id})
            return approve(*a, **kw)

        return answer

    mgr._broker_approver = watching
    args = {"script": SCRIPT, "meta": {"name": "two-asks", "description": "ask twice"}}

    async def main():
        out = await mgr.spawn_workflow(parent_id="parent-1", call_id="wf-9", arguments=args)
        await _wait(mgr)
        return out

    out = asyncio.run(main())
    assert out["status"] == "running"
    assert asked == []
    assert '"report one"' in _injected(mgr)[-1]["content"]


def test_stop_on_the_parent_stops_the_run_and_its_agents(tmp_path):
    import threading

    release = threading.Event()

    class Slow(Routed):
        def complete(self, *, model, messages, tools=None, **settings):
            users = [str(m.get("content") or "") for m in messages if m.get("role") == "user"]
            if users and "AGENT " in users[-1]:
                release.wait(10)
            return super().complete(model=model, messages=messages, tools=tools, **settings)

    mgr, provider = _setup(tmp_path)
    slow = Slow(provider.workflow_args)
    mgr.provider = slow

    async def main():
        await mgr.deliver_to_session("parent-1", "audit both")
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            runners = mgr.children_of("parent-1")
            if runners and mgr.children_of(runners[0]):
                break
            await asyncio.sleep(0.02)
        stopped = mgr.cancel_session("parent-1")
        release.set()
        await _wait(mgr)
        return stopped

    stopped = asyncio.run(main())
    (runner_id,) = mgr.children_of("parent-1")
    assert runner_id in stopped["interrupted"]
    # A person's Stop opens nothing: no result is injected into the parent.
    assert _injected(mgr) == []


def test_the_parent_is_handed_the_agents_spend_and_the_row_the_scripts_phase(tmp_path):
    """The runner's own turn costs nothing, so its result must carry the agents' spend (the
    parent's budget is charged from it), and the parent's row is told the phase the script
    is on while it runs."""
    mgr, provider = _setup(tmp_path)
    handed: list[dict] = []
    frames: list[dict] = []
    real = mgr._inject_background_result

    async def spy(**kwargs):
        handed.append(kwargs["result"])
        await real(**kwargs)

    mgr._inject_background_result = spy

    async def main():
        out = await mgr.spawn_workflow(
            parent_id="parent-1", call_id="wf-7", arguments=provider.workflow_args, on_metadata=frames.append
        )
        await _wait(mgr)
        return out

    out = asyncio.run(main())
    assert handed and handed[0]["task_id"] == out["task_id"]
    assert handed[0]["tokens"] == 220 and handed[0]["changed_files"] == []
    assert frames[0]["background"] is True and frames[0]["title"] == "two-asks workflow"
    assert {"session_id": out["task_id"], "tool": "workflow", "title": "ask"} in frames
    # Its agents' launches move the row too, and the settle frame ends it.
    assert sum(1 for f in frames if f.get("tool") == "task") == 2 and "ended_at" in frames[-1]
