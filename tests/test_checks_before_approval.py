"""A call a pipeline check refuses is refused before anyone is asked to approve it.

Found live 2026-09-26: in workspace-write mode a person was shown the approval card for an
overwrite of a file the model had never read, pressed Allow, and the read-before-edit gate
then refused the call anyway — a click that could not change the outcome, and a model told
nothing until after a person had been interrupted. The check now answers first; what it
reads can still change while a card waits, so dispatch asks it again.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from harness.loop import ApprovalOutcome, TurnEngine
from harness.session.events import EventType
from harness.safety.permissions import Mode, PermissionEngine, write_paths
from harness.safety.provenance import resolve
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall
from harness.tools import ToolRegistry
from harness.tools.observed import ObservedFiles
from harness.tools.observed_gate import observation_gate, observation_recorder
from harness.tools.pipeline import BLOCKED, OK, Block, ToolExecution, ToolPipeline, ToolResult


@dataclass
class _Meta:
    risk_level: str = "medium"
    category: str = "file"
    requires_approval: bool = False


class _Scripted(ProviderClient):
    def __init__(self, turns):
        self._turns = list(turns)

    def complete(self, *, model, messages, tools=None, **settings):
        if not self._turns:
            return AssistantTurn(text="done", finish_reason="stop")
        return self._turns.pop(0)

    def capabilities(self, model):
        return ModelCapabilities()


def _calls(*calls: tuple[str, str, dict]) -> list[AssistantTurn]:
    return [
        AssistantTurn(tool_calls=[ToolCall(id=cid, name=name, arguments=args)], finish_reason="tool_calls")
        for cid, name, args in calls
    ]


def _engine(root: Path, turns: list[AssistantTurn], while_asked=None):
    def write_file(path: str, content: str) -> str:
        """Write a file.

        Args:
            path: where
            content: what
        """
        (root / path).write_text(content, encoding="utf-8")
        return "written"

    def read_file(path: str) -> str:
        """Read a file.

        Args:
            path: which
        """
        return (root / path).read_text(encoding="utf-8")

    registry = ToolRegistry()
    registry.register(write_file, metadata=_Meta())
    registry.register(read_file, metadata=_Meta(risk_level="low"))
    asked: list = []

    async def approver(request):
        asked.append(request.tool_name)
        if while_asked is not None:
            while_asked()
        return ApprovalOutcome.ONCE

    engine = TurnEngine(
        provider=_Scripted(turns),
        registry=registry,
        permissions=PermissionEngine(workspace_root=root, mode=Mode.WORKSPACE_WRITE),
        model="m",
        approver=approver,
    )
    observed = ObservedFiles(resolve=lambda raw: resolve(raw, root))
    engine.pipeline.on_check(observation_gate(observed, write_paths))
    engine.pipeline.on_post_execute(observation_recorder(observed, write_paths))
    return engine, asked


def _run(engine: TurnEngine) -> list:
    async def go():
        return [event async for event in engine.run("go")]

    return asyncio.run(go())


def _result(engine: TurnEngine, call_id: str) -> str:
    return str(
        next(
            m.get("content")
            for m in engine.messages
            if m.get("role") == "tool" and m.get("tool_call_id") == call_id
        )
    )


def test_an_overwrite_of_an_unread_file_is_refused_without_asking(tmp_path):
    (tmp_path / "config.py").write_text("PORT = 8080\n", encoding="utf-8")
    engine, asked = _engine(
        tmp_path, _calls(("c1", "write_file", {"path": "config.py", "content": "PORT = 9\n"}))
    )
    events = _run(engine)

    assert asked == []  # nobody was asked to allow what the gate refuses
    assert not any(e.type is EventType.PERMISSION_REQUIRED for e in events)
    finished = [e.data for e in events if e.type is EventType.TOOL_FINISHED]
    assert [f["status"] for f in finished] == [BLOCKED]
    assert "read-before-edit" in _result(engine, "c1")
    assert (tmp_path / "config.py").read_text(encoding="utf-8") == "PORT = 8080\n"


def test_after_a_read_the_same_overwrite_is_asked_and_runs(tmp_path):
    # The control: the check stands aside for a file the model has seen, so a person is
    # still asked — the early answer never replaces the approval, only a hopeless one.
    (tmp_path / "config.py").write_text("PORT = 8080\n", encoding="utf-8")
    engine, asked = _engine(
        tmp_path,
        _calls(
            ("c1", "read_file", {"path": "config.py"}),
            ("c2", "write_file", {"path": "config.py", "content": "PORT = 9\n"}),
        ),
    )
    _run(engine)

    assert asked == ["write_file"]
    assert (tmp_path / "config.py").read_text(encoding="utf-8") == "PORT = 9\n"


def test_a_file_that_changes_while_the_card_waits_is_still_refused(tmp_path):
    # Why dispatch asks again: the person approved the file as the model read it, and
    # something else rewrote it in the meantime.
    target = tmp_path / "config.py"
    target.write_text("PORT = 8080\n", encoding="utf-8")
    engine, asked = _engine(
        tmp_path,
        _calls(
            ("c1", "read_file", {"path": "config.py"}),
            ("c2", "write_file", {"path": "config.py", "content": "PORT = 9\n"}),
        ),
        while_asked=lambda: target.write_text("PORT = 7070\n", encoding="utf-8"),
    )
    _run(engine)

    assert asked == ["write_file"]
    assert "stale-file" in _result(engine, "c2")
    assert target.read_text(encoding="utf-8") == "PORT = 7070\n"


# -- the stage itself --------------------------------------------------------------------


def _execution() -> ToolExecution:
    return ToolExecution(name="write_file", arguments={"path": "x"}, call_id="c1")


def _dispatch(pipeline: ToolPipeline) -> ToolResult:
    async def run():
        return ToolResult(value="ran", status=OK)

    return asyncio.run(pipeline.dispatch(_execution(), run))


def test_the_early_answer_is_the_result_dispatch_would_give():
    pipeline = ToolPipeline()
    pipeline.on_check(lambda execution: Block("no", rule="some-rule"))
    assert pipeline.refusal(_execution()) == _dispatch(pipeline)
    assert _dispatch(pipeline).status == BLOCKED


def test_a_check_that_crashes_is_asked_again_at_dispatch_where_it_blocks():
    def broken(execution):
        raise RuntimeError("boom")

    pipeline = ToolPipeline()
    pipeline.on_check(broken)
    # Early: no verdict — the approval proceeds as it would have.
    assert pipeline.refusal(_execution()) is None
    # At dispatch: fail closed, as a crashed pre-execute gate always has.
    assert _dispatch(pipeline).status == BLOCKED


def test_a_check_with_no_objection_lets_the_call_run():
    pipeline = ToolPipeline()
    pipeline.on_check(lambda execution: None)
    assert pipeline.refusal(_execution()) is None
    assert _dispatch(pipeline).value == "ran"


def test_a_program_s_sub_call_is_refused_without_asking_too(tmp_path):
    # `run_code` brokers each call its program makes through the same approval, so the
    # same early answer applies: the program is asked for, its hopeless write is not.
    from harness.tools.code_mode import run_code_tool

    (tmp_path / "config.py").write_text("PORT = 8080\n", encoding="utf-8")
    code = "print(tools.write_file(path='config.py', content='PORT = 9'))"
    engine, asked = _engine(tmp_path, _calls(("c1", "run_code", {"code": code})))
    engine.registry.register(run_code_tool())
    events = _run(engine)

    assert asked == ["run_code"]
    subs = [e.data for e in events if e.type is EventType.TOOL_FINISHED and e.data.get("in_program")]
    assert [(s["name"], s["status"]) for s in subs] == [("write_file", BLOCKED)]
    assert (tmp_path / "config.py").read_text(encoding="utf-8") == "PORT = 8080\n"
