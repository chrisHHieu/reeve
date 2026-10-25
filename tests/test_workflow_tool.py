"""`workflow` — the script-driven subagent orchestration tool.

The runtime, gate and sub-call pipeline are `run_code`'s (already covered there); these
tests pin what makes a workflow a workflow: meta validated before anything runs, the
injected helpers (agent/parallel/pipeline/phase/log/args), the child-failure `None`
versus misuse-fatal split, and the agent cap.
"""

from __future__ import annotations

import asyncio
import json
import threading
from typing import Optional

from harness.loop import TurnEngine
from harness.session.events import EventType
from harness.safety.permissions import Mode, PermissionEngine
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient, StreamChunk, ToolCall
from harness.tools import ToolRegistry

SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "integer"}},
    "required": ["answer"],
    "additionalProperties": False,
}


class _WorkflowProvider(ProviderClient):
    """One `workflow` call, then stop."""

    def __init__(
        self, script: str, meta: dict, args=None, resume: bool = False, call_id: str = "w1"
    ) -> None:
        self.script = script
        self.meta = meta
        self.args = args
        self.resume = resume
        self.call_id = call_id
        self.n = 0

    def complete(self, **kw):
        raise NotImplementedError

    def stream(self, *, model, messages, tools=None, **settings):
        self.n += 1
        if self.n == 1:
            arguments = {"script": self.script, "meta": self.meta}
            if self.args is not None:
                arguments["args"] = self.args
            if self.resume:
                arguments["resume"] = True
            yield StreamChunk(
                turn=AssistantTurn(
                    tool_calls=[
                        ToolCall(id=self.call_id, name="workflow", arguments=arguments)
                    ],
                    finish_reason="tool_calls",
                )
            )
        else:
            yield StreamChunk(turn=AssistantTurn(text="done", finish_reason="stop"))

    def capabilities(self, model):
        return ModelCapabilities()


def _task_tool(log: list[dict]):
    """A stand-in for `task`: echoes text, answers `{"answer": len(prompt)}` under a
    schema, fails on "FAIL", and MEET prompts wait on a barrier so parallelism is
    observable. Signature mirrors the real tool's arguments — and so do the two result
    shapes: a completed child answers with the `<task…>` envelope the real tool renders
    (`render_output`), a failed one with the `{"error": …}` sentence."""

    from harness.tools.task import render_output

    def task(
        description: str = "",
        prompt: str = "",
        subagent_type: str = "",
        output_schema: Optional[dict] = None,
        provider: str = "",
        model: str = "",
    ) -> dict:
        log.append(
            {
                "description": description,
                "prompt": prompt,
                "subagent_type": subagent_type,
                "schema": output_schema,
                "provider": provider,
                "model": model,
            }
        )
        if "FAIL" in prompt:
            return {"error": "Subagent failed (task_id: t): boom"}
        if "MEET" in prompt and barrier is not None:
            barrier.wait()
        if output_schema is not None:
            text = json.dumps({"answer": len(prompt)})
        else:
            text = f"echo:{prompt}"
        return {
            "task_id": "t",
            "status": "completed",
            "report": render_output(session_id="t", state="completed", text=text),
            "tokens": 1,
        }

    # Low-risk like the real `task` (its policy admits it to the parallel set): the
    # sub-call gate lets only parallel-safe calls overlap.
    from aisuite.agents import ToolMetadata

    task.__aisuite_tool_metadata__ = ToolMetadata(name="task", category="agent", risk_level="low")
    return task


barrier: threading.Barrier | None = None


def _run(
    tmp_path,
    script,
    meta=None,
    *,
    args=None,
    log=None,
    options=None,
    task=None,
    resume=False,
    call_id="w1",
):
    from harness.session.session_log import SessionLog
    from harness.tools.workflow import workflow_tool

    log = log if log is not None else []
    registry = ToolRegistry()
    registry.register(_task_tool(log) if task is None else task)
    registry.register(workflow_tool())
    engine = TurnEngine(
        provider=_WorkflowProvider(
            script,
            meta if meta is not None else {"name": "wf", "description": "d"},
            args,
            resume,
            call_id,
        ),
        registry=registry,
        permissions=PermissionEngine(
            workspace_root=str(tmp_path), mode=Mode.DANGER_FULL_ACCESS
        ),
        model="m",
    )
    if options is not None:
        engine.workflow_options = options
    # The session log is what durable replay reads (`workflow/run-start|end`).
    engine.log = SessionLog(tmp_path / "log.jsonl")

    async def go():
        return [e async for e in engine.run("go")]

    events = asyncio.run(go())
    content = [m for m in engine.messages if m.get("role") == "tool"][-1]["content"]
    return events, content, log


def test_meta_is_validated_before_anything_runs(tmp_path):
    _, content, log = _run(
        tmp_path,
        "return agent('hi')",
        meta={"name": "wf", "description": ""},
    )
    assert "meta.description must be a non-empty string" in content
    assert log == []


def test_agent_returns_text_and_schema_objects(tmp_path):
    script = """
first = agent("hello", label="greeting")
second = agent(
    "world",
    schema={"type": "object", "properties": {"answer": {"type": "integer"}}, "required": ["answer"]},
)
return {"first": first, "second": second}
"""
    _, content, log = _run(tmp_path, script)
    value = json.loads(content.split("\n[")[0])
    assert value == {"first": "echo:hello", "second": {"answer": 5}}
    assert [c["subagent_type"] for c in log] == ["general", "general"]
    assert log[0]["schema"] is None and log[1]["schema"] is not None


def test_agent_opts_pick_the_childs_llm_route(tmp_path):
    """The delegation options: `provider`/`model` on ONE `agent()` call, both alone and
    together, reach the underlying `task` call as its route parameters."""
    script = """
a = agent("cheap sweep", provider="openai")
b = agent("bare model", model="gpt-5.6-mini")
c = agent("both", provider="anthropic", model="claude-x")
return [a, b, c]
"""
    _, content, log = _run(tmp_path, script)
    value = json.loads(content.split("\n[")[0])
    assert value == ["echo:cheap sweep", "echo:bare model", "echo:both"]
    assert [c["provider"] for c in log] == ["openai", "", "anthropic"]
    assert [c["model"] for c in log] == ["", "gpt-5.6-mini", "claude-x"]


def test_a_failed_child_is_none_not_an_error(tmp_path):
    events, content, _ = _run(tmp_path, 'a = agent("FAIL please")\nreturn {"got": a}')
    value = json.loads(content.split("\n[")[0])
    assert value == {"got": None}
    # The reason must not vanish with the None: it rides the run's narration, so a
    # silent `None` is not the only trace of a failed child.
    deltas = [e for e in events if e.type == EventType.TOOL_OUTPUT_DELTA]
    text = "".join(str(e.data.get("text") or "") for e in deltas)
    assert "agent failed" in text and "boom" in text


def test_parallel_agents_really_overlap(tmp_path):
    global barrier
    barrier = threading.Barrier(2, timeout=8)
    try:
        script = """
out = parallel([
    lambda: agent("MEET a"),
    lambda: agent("MEET b"),
])
return out
"""
        _, content, _ = _run(tmp_path, script)
    finally:
        barrier = None
    value = json.loads(content.split("\n[")[0])
    assert value == ["echo:MEET a", "echo:MEET b"]


def test_pipeline_drops_a_failing_stage_per_item(tmp_path):
    script = """
def double(value, item, index):
    return value * 2

def boom(value, item, index):
    if value > 2:
        raise RuntimeError("too big")
    return value + 1

return pipeline([1, 2], double, boom)
"""
    _, content, _ = _run(tmp_path, script)
    value = json.loads(content.split("\n[")[0])
    assert value == [3, None]


def test_args_reach_the_script_and_log_lines_show_up(tmp_path):
    script = """
log("starting")
phase("gather")
return args
"""
    events, content, _ = _run(tmp_path, script, args={"files": ["a", "b"]})
    value = json.loads(content.split("\n[")[0])
    assert value == {"files": ["a", "b"]}
    # Narration is a real event on the workflow call (the live row), not just output text.
    deltas = [e for e in events if e.type == EventType.TOOL_OUTPUT_DELTA]
    text = "".join(str(e.data.get("text") or "") for e in deltas)
    assert "[workflow]" not in text
    assert "starting" in text and "▸ gather" in text
    assert all(e.data["call_id"] == "w1" for e in deltas)


def test_the_agent_cap_kills_the_run(tmp_path):
    from harness.tools.workflow import MAX_TOTAL_AGENTS

    script = "\n".join(f"agent('n{i}')" for i in range(MAX_TOTAL_AGENTS + 2))
    _, content, log = _run(tmp_path, script)
    assert "agent cap reached" in content
    assert len(log) == MAX_TOTAL_AGENTS


def test_the_agent_cap_is_fatal_inside_parallel_too(tmp_path):
    """The discipline: a tripped cap kills the script — it must not dissolve into the
    same per-item `None` an ordinary child failure produces."""
    from harness.tools.workflow import MAX_TOTAL_AGENTS

    calls = "\n".join(
        f"    lambda: agent('n{i}')," for i in range(MAX_TOTAL_AGENTS + 4)
    )
    script = f"return parallel([\n{calls}\n])"
    _, content, _ = _run(tmp_path, script)
    assert "agent cap reached" in content


def test_the_configured_caps_win_over_the_defaults(tmp_path):
    """`workflow_max_agents` / `workflow_max_tokens` from config ride the run's options:
    the prelude gets them as literals instead of the shipped defaults."""
    _, content, log = _run(
        tmp_path,
        "\n".join(f"agent('n{i}')" for i in range(4)),
        options={"timeout": 180.0, "max_agents": 2, "max_tokens": 0, "concurrency": 2},
    )
    assert "agent cap reached (2)" in content
    assert len(log) == 2

    _, content, _ = _run(
        tmp_path,
        "\n".join(f"agent('n{i}')" for i in range(8)),
        options={"timeout": 180.0, "max_agents": 24, "max_tokens": 5, "concurrency": 2},
    )
    assert "token budget reached (5)" in content


def test_a_sub_call_is_announced_like_run_code(tmp_path):
    events, _, _ = _run(tmp_path, "return agent('one')")
    proposed = [e for e in events if e.type == EventType.TOOL_PROPOSED]
    assert proposed[0].data["name"] == "workflow"
    assert proposed[1].data["name"] == "task"
    assert proposed[1].data["in_program"] is True


def test_the_script_may_not_import_or_touch_the_machine(tmp_path):
    """A workflow script only coordinates — the agents do the work. Both refusals happen
    BEFORE anything runs, so no agent is launched for a script that cannot be allowed."""
    for script, needle in (
        ("import os\nreturn 1", "import os is not available"),
        ("from pathlib import Path\nreturn 1", "import pathlib is not available"),
        ("import json, subprocess\nreturn 1", "import subprocess is not available"),
        ("from . import x\nreturn 1", "import (relative) is not available"),
        ("import typing\nreturn 1", "import typing is not available"),
        ("return open('/etc/passwd').read()", "not available in a workflow script"),
        ("return eval('1+1')", "not available in a workflow script"),
        ("return ().__class__.__bases__", "attribute access"),
    ):
        _, content, log = _run(tmp_path, script)
        assert needle in content, (script, content)
        assert log == [], script


def test_a_per_run_value_is_refused_with_the_way_round_it(tmp_path):
    """A resumed run replays by position and arguments: a clock or a random number would
    replay the wrong results, so the refusal says to pass the value in `args`."""
    for module in ("time", "random", "datetime", "uuid"):
        _, content, log = _run(tmp_path, f"import {module}\nreturn 1")
        assert f"import {module} is not available" in content and "in `args`" in content
        assert log == []


def test_the_pure_std_lib_imports_and_the_builtins_real_scripts_used(tmp_path):
    """Measured on the session store (2026-10-05): 4 of 7 failed workflow runs refused
    `import json`, and two more died on `type`. They run now, inside the child."""
    script = """
import json
import re
from collections import Counter
import itertools as it
words = re.findall(r"[a-z]+", "b a b c")
counts = Counter(words)
return {
    "top": counts.most_common(1)[0][0],
    "pairs": len(list(it.combinations(sorted(counts), 2))),
    "kind": type(counts).__name__,
    "same": json.loads(json.dumps([1])) == [1],
    "chr": chr(65),
    "callable": callable(len),
}
"""
    _, content, log = _run(tmp_path, script)
    assert json.loads(content.split("\n[")[0]) == {
        "top": "b", "pairs": 3, "kind": "Counter", "same": True, "chr": "A", "callable": True,
    }
    assert log == []


def test_the_child_enforces_the_import_list_on_its_own():
    """The AST check is the readable half; the child's `__import__` is the enforced half
    (a dynamic import the AST check cannot see still stops)."""
    import pytest

    from harness.tools.code_child_sandbox import _restricted_builtins

    guarded = _restricted_builtins()["__import__"]
    assert guarded("json").dumps([1]) == "[1]"
    for name, level in (("os", 0), ("json", 1), ("os.path", 0)):
        with pytest.raises(ImportError):
            guarded(name, level=level)


def test_the_body_runs_without_the_dangerous_builtins(tmp_path):
    """The AST guard is the readable half; the enforced half is the child namespace's
    restricted `__builtins__` — a name like `open` simply does not exist there."""
    _, content, _ = _run(tmp_path, "x = open\nreturn 1")
    assert "NameError" in content and "open" in content


def test_json_is_in_scope_without_an_import(tmp_path):
    """A script glues structured results together, so `json` is in scope without an
    import — the one module the coordination job always needs."""
    script = 'return json.loads(json.dumps({"n": 2}))["n"]'
    _, content, _ = _run(tmp_path, script)
    assert json.loads(content.split("\n[")[0]) == 2


def test_a_real_task_tool_envelope_is_unwrapped_end_to_end(tmp_path):
    """The regression a live session hit: `task` answers the parent MODEL with the
    `<task id=…><task_result>…` envelope, and the script received that envelope as
    `agent()`'s value — so a schema run tried `json.loads("<task id=…")` and every agent
    came back None while the run looked successful. This drives the REAL task tool with
    a fake manager spawner (its manager branch, `_spawned`) through the workflow."""
    from harness.tools.task import task_tools

    spawned: list[dict] = []

    def spawner(**kwargs):
        spawned.append(kwargs)
        text = (
            json.dumps({"answer": 7})
            if kwargs.get("output_schema")
            else "the child's answer"
        )
        return {
            "task_id": "child-1",
            "status": "completed",
            "report": text,
            "tokens": 3,
        }

    script = (
        'r = agent("question", schema={"type": "object", "properties": '
        '{"answer": {"type": "integer"}}, "required": ["answer"]})\n'
        't = agent("plain")\n'
        'return {"structured": r, "text": t}'
    )

    def build() -> object:
        return next(
            t
            for t in task_tools(
                workspace=tmp_path,
                provider=_WorkflowProvider(script, {"name": "wf", "description": "d"}),
                model="m",
                session_id="parent-1",
                spawner=spawner,
            )
            if getattr(t, "__name__", "") == "task"
        )

    _, content, _ = _run(tmp_path, script, task=build())
    value = json.loads(content.split("\n[")[0])
    assert value == {"structured": {"answer": 7}, "text": "the child's answer"}
    assert len(spawned) == 2


def _counting_task(calls: list[str]):
    """A task stub whose answer names the call's ordinal, so a re-run is observable."""
    from harness.tools.task import render_output

    def task(
        description: str = "",
        prompt: str = "",
        subagent_type: str = "",
        output_schema: Optional[dict] = None,
    ) -> dict:
        calls.append(prompt)
        if "FAIL" in prompt:
            return {"error": "Subagent failed (task_id: t): boom"}
        return {
            "task_id": "t",
            "status": "completed",
            "report": render_output(session_id="t", state="completed", text=f"echo:{len(calls)}"),
            "tokens": 1,
        }

    # Low-risk like the real `task` (its policy admits it to the parallel set): the
    # sub-call gate lets only parallel-safe calls overlap.
    from aisuite.agents import ToolMetadata

    task.__aisuite_tool_metadata__ = ToolMetadata(name="task", category="agent", risk_level="low")
    return task


#: A workflow that finishes one agent and then dies — the shape resume exists for.
_DEAD_SCRIPT = 'a = agent("one")\nlog("got " + str(a))\nraise RuntimeError("after")'


def test_start_lets_the_script_work_while_children_run(tmp_path):
    """`start()` is not a slower `agent()`: two children meet on a barrier while the
    script is still between the launches, and `collect()` gathers them in script order —
    here the SECOND one first, which a synchronous implementation could not survive."""
    global barrier
    barrier = threading.Barrier(2, timeout=8)
    try:
        script = """
a = start("MEET a", label="a")
b = start("MEET b", label="b")
log("launched")
second = collect(b)
first = collect(a)
return [second, first]
"""
        _, content, _ = _run(tmp_path, script)
    finally:
        barrier = None
    value = json.loads(content.split("\n[")[0])
    assert value == ["echo:MEET b", "echo:MEET a"]


def test_a_started_child_finishes_even_if_the_script_never_collects(tmp_path):
    """Fire-and-forget still has a bounded lifetime: the host waits for every in-flight
    sub-call before returning the run's result, so an uncollected child is never
    abandoned mid-turn (and its success lands in the journal for a later resume)."""
    from harness.tools.workflow_journal import run_key

    def task(
        description: str = "",
        prompt: str = "",
        subagent_type: str = "",
        output_schema: Optional[dict] = None,
    ) -> dict:
        threading.Event().wait(0.2)  # long enough that a non-waiting host returns first
        return {"task_id": "t", "status": "completed", "report": "late", "tokens": 1}

    script = 'h = start("forget me")\nreturn "done"'
    _, content, _ = _run(tmp_path, script, task=task)
    assert json.loads(content.split("\n[")[0]) == "done"
    journal = (
        tmp_path / "workflows" / f"log.{run_key(name='wf', script=script, args=None)}.jsonl"
    )
    entries = [
        json.loads(line)
        for line in journal.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert [e for e in entries if e.get("i") == 1], "the uncollected child must still finish"


def test_collect_reads_a_structured_child(tmp_path):
    script = """
handle = start("world", schema={"type": "object", "properties": {"answer": {"type": "integer"}}, "required": ["answer"]})
return collect(handle)
"""
    _, content, _ = _run(tmp_path, script)
    assert json.loads(content.split("\n[")[0]) == {"answer": 5}


def test_collect_reports_a_failed_child_as_none(tmp_path):
    events, content, _ = _run(tmp_path, 'h = start("FAIL please")\nreturn {"got": collect(h)}')
    assert json.loads(content.split("\n[")[0]) == {"got": None}
    text = "".join(
        str(e.data.get("text") or "")
        for e in events
        if e.type == EventType.TOOL_OUTPUT_DELTA
    )
    assert "agent failed" in text and "boom" in text


def test_start_counts_toward_the_agent_cap(tmp_path):
    _, content, _ = _run(
        tmp_path,
        'start("one", label="one")\nstart("two", label="two")\nreturn 1',
        options={"timeout": 180.0, "max_agents": 1, "max_tokens": 0, "concurrency": 2},
    )
    assert "agent cap reached (1)" in content


def test_a_rerun_without_resume_buys_the_agents_again(tmp_path):
    calls: list[str] = []
    task = _counting_task(calls)
    script = 'return agent("one")'
    _, first, _ = _run(tmp_path, script, task=task)
    _, second, _ = _run(tmp_path, script, task=task)
    assert json.loads(first.split("\n[")[0]) == "echo:1"
    assert json.loads(second.split("\n[")[0]) == "echo:2"
    assert len(calls) == 2


def test_resume_replays_the_agents_a_dead_run_already_finished(tmp_path):
    calls: list[str] = []
    task = _counting_task(calls)
    _, first, _ = _run(tmp_path, _DEAD_SCRIPT, task=task)
    assert "after" in first and len(calls) == 1

    events, second, _ = _run(tmp_path, _DEAD_SCRIPT, task=task, resume=True, call_id="w2")
    assert "after" in second
    assert len(calls) == 1, "the completed agent must be replayed, not bought again"
    text = "".join(
        str(e.data.get("text") or "")
        for e in events
        if e.type == EventType.TOOL_OUTPUT_DELTA
    )
    assert "got echo:1" in text
    assert "replayed agent 1" in text
    entries = [
        json.loads(line)
        for line in (tmp_path / "log.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    ends = [e for e in entries if e.get("type") == "workflow/run-end"]
    assert ends[-1].get("replayed") == 1
    # The events the engine actually wrote must satisfy the workflow-log invariant:
    # run-start/end paired, every live dispatch preceded by its start, no replayed
    # dispatch claiming it ran.
    from harness.session.invariants import audit_workflow_log, open_workflow_runs

    assert audit_workflow_log(entries) == []
    assert open_workflow_runs(entries) == []


def test_resume_ignores_an_entry_written_for_other_arguments(tmp_path):
    """Position alone is not identity: the journal entry only counts when the call's exact
    arguments still fingerprint the same — otherwise the script (clock, args) changed."""
    from harness.tools.workflow_journal import run_key

    calls: list[str] = []
    task = _counting_task(calls)
    script = 'return agent("one")'
    journal = (
        tmp_path / "workflows" / f"log.{run_key(name='wf', script=script, args=None)}.jsonl"
    )
    journal.parent.mkdir(parents=True)
    journal.write_text(
        json.dumps(
            {
                "i": 1,
                "fp": "0000000000000000",
                "result": {
                    "task_id": "stale",
                    "status": "completed",
                    "report": "stale",
                    "tokens": 0,
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    _, content, _ = _run(tmp_path, script, task=task, resume=True)
    assert len(calls) == 1
    assert json.loads(content.split("\n[")[0]) == "echo:1"


def test_a_failed_child_is_not_journalled(tmp_path):
    """Resume exists to continue work that SUCCEEDED; replaying a failure would make the
    resume indistinguishable from never having tried."""
    calls: list[str] = []
    task = _counting_task(calls)
    script = 'a = agent("FAIL please")\nraise RuntimeError("after")'
    _, first, _ = _run(tmp_path, script, task=task)
    assert "after" in first and len(calls) == 1

    _, second, _ = _run(tmp_path, script, task=task, resume=True)
    assert "after" in second
    assert len(calls) == 2


def test_a_died_run_tells_the_model_how_to_resume(tmp_path):
    """The feature is only reachable if the failure names it: a timed-out run's error
    carries the `resume=true` instruction, and the journal is there to honour it."""
    stop = threading.Event()
    calls: list[str] = []

    def task(
        description: str = "",
        prompt: str = "",
        subagent_type: str = "",
        output_schema: Optional[dict] = None,
    ) -> dict:
        calls.append(prompt)
        stop.wait(0.6)
        return {"task_id": "t", "status": "completed", "report": "late", "tokens": 1}

    _, content, _ = _run(
        tmp_path,
        'return agent("slow")',
        task=task,
        options={"timeout": 0.05, "max_agents": 24, "max_tokens": 0, "concurrency": 2},
    )
    assert "resume=true" in content
    assert (tmp_path / "workflows").exists()


def test_the_run_is_recorded_start_to_end(tmp_path):
    _run(tmp_path, "phase('gather')\nreturn agent('one')")
    entries = [
        json.loads(line)
        for line in (tmp_path / "log.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    kinds = [entry.get("type") for entry in entries]
    assert kinds.count("workflow/run-start") == 1
    assert kinds.count("workflow/run-end") == 1
    start = next(e for e in entries if e.get("type") == "workflow/run-start")
    end = next(e for e in entries if e.get("type") == "workflow/run-end")
    assert start["root_call_id"] == end["root_call_id"] == "w1"
    assert start["name"] == "wf" and end["status"] == "ok"
    assert end["agents"] == 1
    assert [e.get("kind") for e in entries if e.get("type") == "workflow/note"] == ["phase"]

    from harness.session.invariants import audit_workflow_log

    assert audit_workflow_log(entries) == []


def test_a_backgrounded_child_is_a_failure_not_its_notice(tmp_path):
    """Orchestration audit O1: a child that moved to the background answers with the
    running envelope; `agent()` returned that notice's text as the child's answer (and
    `None` under a schema, with no reason). The manager no longer backgrounds a program's
    call, and the prelude never reads the notice as an answer either."""
    from harness.tools.task import BACKGROUND_STARTED, render_output

    def task(
        description: str = "",
        prompt: str = "",
        subagent_type: str = "",
        output_schema: Optional[dict] = None,
        provider: str = "",
        model: str = "",
    ) -> dict:
        """A child that has moved to the background."""
        return {
            "task_id": "t",
            "status": "running",
            "report": render_output(session_id="t", state="running", text=BACKGROUND_STARTED),
        }

    events, content, _ = _run(tmp_path, 'return {"plain": agent("slow audit")}', task=task)
    value = json.loads(content.split("\n[")[0])
    assert value == {"plain": None}
    text = "".join(
        str(e.data.get("text") or "") for e in events if e.type == EventType.TOOL_OUTPUT_DELTA
    )
    assert "moved to the background" in text


def test_a_runs_facts_say_how_it_ended(tmp_path):
    """What counts a run reads data, not the text written for the model."""
    from harness.tools.workflow import WORKFLOW_CONTRACT

    facts = WORKFLOW_CONTRACT.facts
    assert facts({}, {"error": "import os is not available", "refused": "script"}) == {
        "outcome": "refused", "rule": "script",
    }
    assert facts({}, {"returned": 1, "tool_calls": 2}) == {"outcome": "ok", "agents": 2}
    assert facts({}, {"error": "boom", "tool_calls": 1}) == {"outcome": "error", "agents": 1}
    assert facts({}, {"status": "running", "task_id": "t"}) == {"outcome": "background", "task_id": "t"}
    # And the refusal is marked where it happens, before anything runs: the model reads
    # the sentence, the log row carries the fact the harness review counts.
    _, content, _ = _run(tmp_path, "import os\nreturn 1")
    assert "import os is not available" in content and "refused" not in content
    rows = [
        json.loads(line)
        for line in (tmp_path / "log.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    (row,) = [r for r in rows if r.get("type") == "tool/result" and r.get("name") == "workflow"]
    assert row["facts"] == {"outcome": "refused", "rule": "script"}
