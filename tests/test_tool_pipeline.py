"""Tool execution pipeline (harness/tools/pipeline.py) — the three points a concern
attaches to a tool call without being written into the turn engine.

The pipeline is a mechanism, not a policy, so the tests are about mechanism: what runs,
in what order, and what happens when a listener misbehaves. Its failure policy is
deliberately asymmetric and that asymmetry is the part most likely to be "simplified"
by a later reader, so it gets the most coverage:

    pre_execute   raises -> BLOCKS   a gate that crashed is not consent
    execute       raises -> ERROR    same shape as the tool itself raising
    post_execute  raises -> KEEPS    bounding is not a permission decision
"""

from __future__ import annotations

from aisuite.agents import ToolMetadata

import asyncio


from harness.loop import TurnEngine
from harness.session.events import EventType
from harness.safety.permissions import PermissionEngine
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall
from harness.tools import ToolRegistry
from harness.tools.pipeline import BLOCKED, ERROR, OK, Block, ToolExecution, ToolPipeline, ToolResult


def _exec(name="t", arguments=None):
    return ToolExecution(name=name, arguments=arguments or {}, call_id="c1")


def _run_ok(value="raw"):
    async def run():
        return ToolResult(value=value, status=OK)

    return run


def _dispatch(pipeline, run=None, execution=None):
    return asyncio.run(pipeline.dispatch(execution or _exec(), run or _run_ok()))


# -- nothing registered: dispatch is a pass-through -----------------------------


def test_empty_pipeline_returns_the_run_result_untouched():
    out = _dispatch(ToolPipeline())
    assert (out.value, out.status, out.ok) == ("raw", OK, True)


def test_empty_pipeline_propagates_an_error_status():
    async def run():
        return ToolResult(value={"error": "boom"}, status=ERROR)

    out = _dispatch(ToolPipeline(), run)
    assert out.status == ERROR and out.ok is False


# -- pre_execute ----------------------------------------------------------------


def test_pre_execute_can_block_and_the_tool_never_runs():
    ran = []

    async def run():
        ran.append(1)
        return ToolResult(value="raw")

    async def veto(execution, nxt):
        return Block("looping on the same call")

    pipeline = ToolPipeline()
    pipeline.on_pre_execute(veto)
    out = _dispatch(pipeline, run)

    assert ran == []
    assert out.status == BLOCKED
    assert out.value["reason"] == "looping on the same call"


def test_a_refusal_names_the_rule_and_the_reason_in_the_error():
    """Never a bare `tool call blocked`: the model must be able to tell a policy refusal
    from a transient failure and act on it. Session 435e0be4 got the bare string five
    times on one file and abandoned an edit it could have unlocked with one read."""

    async def veto(execution, nxt):
        return Block("config.py already exists and you have not read it", rule="read-before-edit")

    pipeline = ToolPipeline()
    pipeline.on_pre_execute(veto)
    out = _dispatch(pipeline)

    assert out.status == BLOCKED
    assert out.value["rule"] == "read-before-edit"
    assert out.value["reason"] == "config.py already exists and you have not read it"
    assert out.value["error"] == (
        "tool call blocked by read-before-edit: "
        "config.py already exists and you have not read it"
    )


def test_a_refusal_with_no_producer_detail_says_that_much():
    async def veto(execution, nxt):
        return Block("")

    pipeline = ToolPipeline()
    pipeline.on_pre_execute(veto)
    out = _dispatch(pipeline)

    assert out.status == BLOCKED
    assert out.value["error"] != "tool call blocked"


def test_pre_execute_that_only_observes_lets_the_call_through():
    seen = []

    async def observe(execution, nxt):
        seen.append(execution.name)
        return await nxt()

    pipeline = ToolPipeline()
    pipeline.on_pre_execute(observe)
    assert _dispatch(pipeline).value == "raw"
    assert seen == ["t"]


def test_pre_execute_may_rewrite_the_arguments():
    async def rewrite(execution, nxt):
        execution.arguments["path"] = "corrected.txt"
        return await nxt()

    args = {"path": "typo.txt"}
    pipeline = ToolPipeline()
    pipeline.on_pre_execute(rewrite)
    _dispatch(pipeline, execution=_exec(arguments=args))
    assert args["path"] == "corrected.txt"


def test_a_raising_pre_execute_listener_blocks_the_call():
    """Fail CLOSED. A gate that crashed cannot be read as consent — this is the one
    stage where degrading to 'carry on' would be a hole rather than an inconvenience."""
    ran = []

    async def run():
        ran.append(1)
        return ToolResult(value="raw")

    async def broken(execution, nxt):
        raise RuntimeError("policy bug")

    pipeline = ToolPipeline()
    pipeline.on_pre_execute(broken)
    out = _dispatch(pipeline, run)

    assert ran == []
    assert out.status == BLOCKED
    assert "policy bug" in out.value["reason"]


# -- execute --------------------------------------------------------------------


def test_execute_wraps_the_dispatch_and_can_replace_the_result():
    async def wrap(execution, nxt):
        inner = await nxt()
        return inner.with_value(f"[{inner.value}]")

    pipeline = ToolPipeline()
    pipeline.on_execute(wrap)
    assert _dispatch(pipeline).value == "[raw]"


def test_execute_can_short_circuit_without_running_the_tool():
    """The shape a deadline listener needs: answer without letting the body settle."""
    ran = []

    async def run():
        ran.append(1)
        return ToolResult(value="raw")

    async def short(execution, nxt):
        return ToolResult(value={"error": "timed out"}, status=ERROR)

    pipeline = ToolPipeline()
    pipeline.on_execute(short)
    out = _dispatch(pipeline, run)

    assert ran == []
    assert out.status == ERROR


def test_a_raising_execute_listener_becomes_an_error_result():
    async def broken(execution, nxt):
        raise RuntimeError("wrapper bug")

    pipeline = ToolPipeline()
    pipeline.on_execute(broken)
    out = _dispatch(pipeline)
    assert out.status == ERROR
    assert out.value["error_type"] == "RuntimeError"


# -- post_execute ---------------------------------------------------------------


def test_post_execute_can_transform_the_value():
    async def bound(execution, result, nxt):
        inner = await nxt()
        return inner.with_value(str(inner.value)[:2])

    pipeline = ToolPipeline()
    pipeline.on_post_execute(bound)
    assert _dispatch(pipeline).value == "ra"


def test_a_raising_post_execute_listener_keeps_the_real_result():
    """Fail OPEN. A broken formatter must not lose a result the tool actually
    produced — bounding and presentation are not permission decisions."""

    async def broken(execution, result, nxt):
        raise RuntimeError("formatter bug")

    pipeline = ToolPipeline()
    pipeline.on_post_execute(broken)
    out = _dispatch(pipeline)
    assert out.value == "raw" and out.status == OK


def test_a_post_execute_listener_returning_the_wrong_type_is_ignored():
    async def sloppy(execution, result, nxt):
        await nxt()
        return "just a string"

    pipeline = ToolPipeline()
    pipeline.on_post_execute(sloppy)
    assert _dispatch(pipeline).value == "raw"


# -- ordering and lifecycle -----------------------------------------------------


def test_the_first_listener_registered_is_the_outermost():
    """So a generic bounding policy registers FIRST in order to bound whatever the
    tool-specific listeners produced. Entry order is the wrapping order."""
    order = []

    def _mark(tag):
        async def listener(execution, result, nxt):
            order.append(f"{tag}-in")
            inner = await nxt()
            order.append(f"{tag}-out")
            return inner.with_value(f"{tag}({inner.value})")

        return listener

    pipeline = ToolPipeline()
    pipeline.on_post_execute(_mark("outer"))
    pipeline.on_post_execute(_mark("inner"))
    out = _dispatch(pipeline)

    assert order == ["outer-in", "inner-in", "inner-out", "outer-out"]
    # inner wrapped first, outer wrapped last
    assert out.value == "outer(inner(raw))"


def test_a_disposer_removes_the_listener_and_is_idempotent():
    async def bound(execution, result, nxt):
        return (await nxt()).with_value("changed")

    pipeline = ToolPipeline()
    dispose = pipeline.on_post_execute(bound)
    assert _dispatch(pipeline).value == "changed"

    dispose()
    assert _dispatch(pipeline).value == "raw"
    dispose()  # second call must not raise or disturb anything
    assert _dispatch(pipeline).value == "raw"


def test_registering_during_a_dispatch_does_not_change_that_dispatch():
    """The chain is snapshotted on entry; a listener that registers another must not
    make the walk it is already inside grow under it."""
    late_ran = []

    async def late(execution, result, nxt):
        late_ran.append(1)
        return await nxt()

    async def registers(execution, result, nxt):
        pipeline.on_post_execute(late)
        return await nxt()

    pipeline = ToolPipeline()
    pipeline.on_post_execute(registers)
    _dispatch(pipeline)
    assert late_ran == []
    # …but it is in the chain for the next call.
    _dispatch(pipeline)
    assert late_ran == [1]


# -- through the real engine ----------------------------------------------------


class _Scripted(ProviderClient):
    def __init__(self, turns):
        self._turns = list(turns)

    def complete(self, *, model, messages, tools=None, **settings):
        return self._turns.pop(0)

    def capabilities(self, model):
        return ModelCapabilities()


def _engine(tmp_path, tool):
    registry = ToolRegistry()
    registry.register(tool, metadata=ToolMetadata())
    turns = [
        AssistantTurn(
            tool_calls=[ToolCall(id="c1", name=tool.__name__, arguments={})],
            finish_reason="tool_calls",
        ),
        AssistantTurn(text="done", finish_reason="stop"),
    ]
    return TurnEngine(
        provider=_Scripted(turns),
        registry=registry,
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5",
    )


def _echo() -> dict:
    """Echo."""
    return {"ok": True}


def test_engine_dispatches_through_the_pipeline(tmp_path):
    engine = _engine(tmp_path, _echo)
    seen = []

    async def spy(execution, result, nxt):
        seen.append((execution.name, execution.call_id, result.status))
        return await nxt()

    engine.pipeline.on_post_execute(spy)
    asyncio.run(_drain(engine))
    assert seen == [("_echo", "c1", OK)]


def test_a_post_listener_changes_what_the_model_reads(tmp_path):
    engine = _engine(tmp_path, _echo)

    async def bound(execution, result, nxt):
        return (await nxt()).with_value({"bounded": True})

    engine.pipeline.on_post_execute(bound)
    asyncio.run(_drain(engine))

    tool_message = next(m for m in engine.messages if m.get("role") == "tool")
    assert '"bounded": true' in tool_message["content"]


def test_a_blocked_call_still_answers_the_model(tmp_path):
    """Every tool call must get a result message; an unanswered call orphans the
    history and hosted templates reject it."""
    engine = _engine(tmp_path, _echo)

    async def veto(execution, nxt):
        return Block("no")

    engine.pipeline.on_pre_execute(veto)
    events = asyncio.run(_drain(engine))

    finished = next(ev for ev in events if ev.type is EventType.TOOL_FINISHED)
    assert finished.data["status"] == BLOCKED
    assert any(m.get("role") == "tool" for m in engine.messages)


def test_an_engine_with_no_listeners_behaves_as_before(tmp_path):
    engine = _engine(tmp_path, _echo)
    events = asyncio.run(_drain(engine))
    finished = next(ev for ev in events if ev.type is EventType.TOOL_FINISHED)
    assert finished.data["status"] == OK


async def _drain(engine):
    return [ev async for ev in engine.run("go")]
