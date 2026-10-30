"""A refused whole-file write shows the file as it stands, and that is the read.

The store, 2026-09-25..10-30: 38 of 44 read-guard refusals were followed by a read of the same
file, and 5 by a second refusal. In session 90e83048-f57 a script the model ran changed a file it
had written, the rewrite was refused as stale, the model read 30 of its 181 lines and was refused
again as a partial read, and the whole file was written three times.
"""

from __future__ import annotations

import asyncio
import hashlib

import pytest
from aisuite.agents import ToolMetadata

from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall
from harness.loop import TurnEngine
from harness.loop.result_recording import lift_sidecars
from harness.safety.permissions import Mode, PermissionEngine
from harness.safety.provenance import resolve
from harness.session.session_log import SessionLog
from harness.tools import ToolRegistry
from harness.tools.files.window import MAX_TOTAL_CHARS
from harness.tools.observed import RULE_NEVER_READ, RULE_PARTIAL_VIEW, RULE_STALE, ObservedFiles
from harness.tools.observed_gate import SHOWN_CHARS, observation_gate, observation_recorder
from harness.tools.pipeline import BLOCKED, OK, ToolExecution, ToolPipeline, ToolResult

MAP = "".join(f"const line{i} = {i};\n" for i in range(1, 182))  # 181 lines, as in the session


def _locate(name, arguments):
    path = arguments.get("path")
    return ([str(path)], True) if name == "write_file" and path else ([], False)


@pytest.fixture
def observed(tmp_path):
    return ObservedFiles(resolve=lambda raw: resolve(raw, tmp_path))


def _write(pipeline, path):
    async def run():
        return ToolResult(status=OK, value="written")

    execution = ToolExecution(call_id="w", name="write_file", arguments={"path": path, "content": "new\n"})
    return asyncio.run(pipeline.dispatch(execution, run))


# -- the gate --------------------------------------------------------------------------------


def test_a_stale_whole_file_write_is_refused_with_the_file_as_it_stands(observed, tmp_path):
    target = tmp_path / "Map.tsx"
    target.write_text(MAP, encoding="utf-8")
    observed.observe("Map.tsx")  # the model wrote it
    target.write_text(MAP.replace("line2 = 2", "tiles = 512"), encoding="utf-8")  # its script edited it
    pipeline = ToolPipeline()
    pipeline.on_check(observation_gate(observed, _locate))

    blocked = _write(pipeline, "Map.tsx")

    assert blocked.status == BLOCKED and blocked.value["rule"] == RULE_STALE
    error = blocked.value["error"]
    assert "shown below as it stands now" in error and "Read the whole file" not in error
    assert "Map.tsx as it stands now (181 lines):" in error
    assert "     2\tconst tiles = 512;" in error and "   181\tconst line181 = 181;" in error
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    assert blocked.value["_shown"] == [["Map.tsx", digest]]


@pytest.mark.parametrize(
    "content",
    [
        "short\n" + "x" * 600 + "\n",  # a line the reader clips: rewritten whole it would lose its tail
        "binary\x00here\n",
        "".join(f"{i}\n" for i in range(2_001)),  # past the reader's window
    ],
    ids=["clipped-line", "nul-byte", "past-the-window"],
)
def test_a_file_the_refusal_cannot_show_whole_asks_for_all_of_it(observed, tmp_path, content):
    (tmp_path / "big.txt").write_text(content, encoding="utf-8")
    pipeline = ToolPipeline()
    pipeline.on_check(observation_gate(observed, _locate))

    blocked = _write(pipeline, "big.txt")

    assert blocked.status == BLOCKED and blocked.value["rule"] == RULE_NEVER_READ
    assert "Read the whole file first, with no line limit" in blocked.value["error"]
    assert "as it stands now" not in blocked.value["error"] and "_shown" not in blocked.value


def test_a_file_in_another_encoding_is_not_shown(observed, tmp_path):
    (tmp_path / "old.txt").write_bytes("caf\xe9\n".encode("latin-1"))
    pipeline = ToolPipeline()
    pipeline.on_check(observation_gate(observed, _locate))
    assert "_shown" not in _write(pipeline, "old.txt").value


def test_one_refusal_shows_no_more_than_one_read_would(observed, tmp_path, monkeypatch):
    for name in ("a.py", "b.py"):
        (tmp_path / name).write_text("x = 1\n" * 20, encoding="utf-8")
    monkeypatch.setattr("harness.tools.observed_gate.SHOWN_CHARS", 300)  # one file is 259

    def locate(name, arguments):
        return (["a.py", "b.py"], True)

    pipeline = ToolPipeline()
    pipeline.on_check(observation_gate(observed, locate))
    blocked = _write(pipeline, "a.py")

    assert [path for path, _ in blocked.value["_shown"]] == ["a.py"]
    assert "The same applies to: b.py" in blocked.value["error"]
    assert SHOWN_CHARS == MAX_TOTAL_CHARS  # the budget is the reader's own


def test_the_shown_version_is_what_is_recorded(observed, tmp_path):
    """A file that changes after the refusal showed it is stale again: what the model has is the
    version shown, not whatever is on disk when the record is made."""
    target = tmp_path / "a.py"
    target.write_text("x = 1\n", encoding="utf-8")
    shown = hashlib.sha256(target.read_bytes()).hexdigest()
    target.write_text("x = 2\n", encoding="utf-8")

    observed.observe("a.py", digest=shown)

    assert observed.blame("a.py")[0] == RULE_STALE


def test_the_record_of_what_was_shown_never_reaches_the_model_or_a_program():
    clean, *_ = lift_sidecars({"error": "blocked", "_shown": [["a.py", "abc"]]})
    assert clean == {"error": "blocked"}


# -- the engine: the refusal is the read -----------------------------------------------------


class Scripted(ProviderClient):
    """Plays its turns in order; a callable in the list runs (between steps) instead of answering."""

    def __init__(self, turns):
        self.turns = list(turns)

    def complete(self, model, messages, tools):
        while callable(self.turns[0]):
            self.turns.pop(0)()
        return self.turns.pop(0)

    def capabilities(self, model):
        return ModelCapabilities()


def _turn(*calls):
    return AssistantTurn(text="", finish_reason="tool_calls", tool_calls=list(calls))


DONE = AssistantTurn(text="done", finish_reason="stop")


def _engine(tmp_path, turns):
    engine = TurnEngine(
        provider=Scripted(turns),
        registry=ToolRegistry(),
        permissions=PermissionEngine(workspace_root=tmp_path, mode=Mode.DANGER_FULL_ACCESS),
        model="gpt-5.5",
        messages=[{"role": "system", "content": "be helpful"}],
    )
    engine.log = SessionLog(tmp_path / "events.jsonl")
    engine.observed = ObservedFiles(resolve=lambda raw: resolve(raw, tmp_path))
    written: list[str] = []

    def write_file(path: str, content: str) -> str:
        """Write a file."""
        (tmp_path / path).write_text(content, encoding="utf-8")
        written.append(path)
        return "written"

    def read_file(path: str, start_line: int = 1, max_lines: int = 2000) -> dict:
        """Read a file."""
        lines = (tmp_path / path).read_text(encoding="utf-8").splitlines()
        window = lines[start_line - 1 : start_line - 1 + max_lines]
        end = start_line + len(window) - 1
        return {"path": path, "start_line": start_line, "end_line": end, "total_lines": len(lines),
                "content": "\n".join(window)}

    engine.registry.register(write_file, metadata=ToolMetadata())
    engine.registry.register(read_file, metadata=ToolMetadata())

    def locate(name, arguments):
        path = arguments.get("path")
        return ([str(path)], True) if name == "write_file" and path else ([], False)

    engine.pipeline.on_check(observation_gate(engine.observed, locate))
    engine.pipeline.on_post_execute(observation_recorder(engine.observed, locate))
    return engine, written


def _results(engine):
    return {m["tool_call_id"]: str(m.get("content")) for m in engine.messages if m.get("role") == "tool"}


def _run(engine):
    async def go():
        return [e async for e in engine.run("rewrite the map")]

    asyncio.run(go())


def _rewrite(call_id):
    return ToolCall(id=call_id, name="write_file", arguments={"path": "Map.tsx", "content": MAP + "// v2\n"})


def test_after_a_refusal_that_showed_the_file_the_rewrite_goes_through_with_no_read(tmp_path):
    """Session 90e83048-f57's shape: written by the model, edited by its own script, rewritten."""
    target = tmp_path / "Map.tsx"

    def script_edit():
        target.write_text(MAP.replace("line2 = 2", "tiles = 512"), encoding="utf-8")

    engine, written = _engine(tmp_path, [
        _turn(ToolCall(id="w0", name="write_file", arguments={"path": "Map.tsx", "content": MAP})),
        script_edit,
        _turn(_rewrite("w1")),
        _turn(_rewrite("w2")),
        DONE,
    ])

    _run(engine)

    results = _results(engine)
    assert RULE_STALE in results["w1"] and "     2\tconst tiles = 512;" in results["w1"]
    assert "_shown" not in results["w1"]
    assert results["w2"] == "written" and written == ["Map.tsx", "Map.tsx"]


def test_a_partial_read_then_a_rewrite_is_refused_once_not_twice(tmp_path):
    """The second refusal of that session: 30 lines read of 181, then the whole file written."""
    (tmp_path / "Map.tsx").write_text(MAP, encoding="utf-8")
    engine, written = _engine(tmp_path, [
        _turn(ToolCall(id="r1", name="read_file", arguments={"path": "Map.tsx", "max_lines": 30})),
        _turn(_rewrite("w1")),
        _turn(_rewrite("w2")),
        DONE,
    ])

    _run(engine)

    results = _results(engine)
    assert RULE_PARTIAL_VIEW in results["w1"] and "   181\tconst line181 = 181;" in results["w1"]
    assert results["w2"] == "written" and written == ["Map.tsx"]


def test_a_file_changed_after_the_refusal_showed_it_is_refused_again(tmp_path):
    target = tmp_path / "Map.tsx"
    target.write_text(MAP, encoding="utf-8")

    def someone_else():
        target.write_text(MAP + "// the person's line\n", encoding="utf-8")

    engine, written = _engine(tmp_path, [_turn(_rewrite("w1")), someone_else, _turn(_rewrite("w2")), DONE])

    _run(engine)

    results = _results(engine)
    assert RULE_NEVER_READ in results["w1"]
    assert RULE_STALE in results["w2"] and "the person's line" in results["w2"] and written == []


def test_a_refusal_the_step_bound_cut_is_not_a_read(tmp_path):
    """The model saw only a head of the file, so the file was not read whole."""
    (tmp_path / "Map.tsx").write_text(MAP, encoding="utf-8")
    engine, written = _engine(tmp_path, [_turn(_rewrite("w1")), _turn(_rewrite("w2")), DONE])
    engine._enforce_turn_budget = lambda tool_call, rendered, result, facts=None: (
        (rendered[:200] + "\n[cut]") if isinstance(rendered, str) else rendered,
        result,
    )

    _run(engine)

    results = _results(engine)
    assert RULE_NEVER_READ in results["w1"] and RULE_NEVER_READ in results["w2"] and written == []


def test_a_call_answered_without_running_is_logged_with_its_arguments(tmp_path):
    """A refused call's arguments stay in every later request (the assistant message keeps
    them), and `tool/call` used to be written only for a call that started."""
    import json

    (tmp_path / "Map.tsx").write_text(MAP, encoding="utf-8")
    engine, _ = _engine(tmp_path, [
        _turn(_rewrite("w1"), ToolCall(id="u1", name="no_such_tool", arguments={"x": 1})),
        DONE,
    ])

    _run(engine)

    rows = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    order = [(row["type"], row.get("call_id")) for row in rows if row.get("type") in ("tool/call", "tool/result")]
    assert order == [("tool/call", "w1"), ("tool/result", "w1"), ("tool/call", "u1"), ("tool/result", "u1")]
    logged = {row["call_id"]: json.loads(row["arguments"]) for row in rows if row.get("type") == "tool/call"}
    assert logged["w1"]["path"] == "Map.tsx" and logged["u1"] == {"x": 1}
