"""What the harness keeps about a memory so the model does not have to, and the progress log.

The model writes the fact. When it was written, the files it names, whether it came after web
content, how often it is read and what it said before a rewrite are the harness's to record, and
to tell a later session on the index line.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from harness.context.memory import anchors
from harness.context.memory.admission import Admission, named_files, project_anchor
from harness.context.memory.auto_memory import INDEX_NAME, PROGRESS_NAME, PROJECT, AutoMemory, auto_memory_tools
from harness.context.memory.gate import PROGRESS_MAX_CHARS, judge_progress
from harness.context.memory.outside import read_outside_content
from harness.llm.providers import ModelCapabilities, ProviderClient
from harness.tools.memory_admission import memory_admission
from harness.tools.pipeline import BLOCKED, OK, ToolExecution, ToolPipeline, ToolResult


def note(kind: str, body: str, **meta: str) -> str:
    extra = "".join(f"  {key}: {value}\n" for key, value in meta.items())
    return f"---\nname: n\ndescription: {body[:40]}\nmetadata:\n  type: {kind}\n{extra}---\n{body}\n"


@pytest.fixture
def project(tmp_path):
    ws = tmp_path / "ws"
    (ws / "ops").mkdir(parents=True)
    (ws / "ops" / "deploy.py").write_text("def deploy():\n    return 'ci'\n", encoding="utf-8")
    memory = AutoMemory(tmp_path / "state" / "memory", PROJECT)
    memory.ensure()
    return ws, memory


def door_for(ws: Path, memory: AutoMemory, **kw) -> Admission:
    return Admission(memory.root, anchor=project_anchor([ws], ws), stamp=lambda: ("s1", 2), **kw)


def _locate(name, arguments):
    path = arguments.get("path")
    return ([str(path)], True) if name == "write_file" and path else ([], False)


def _dispatch(door: Admission, name: str, path: Path, text=None):
    pipeline = ToolPipeline()
    pipeline.on_execute(memory_admission([door], _locate, lambda raw: Path(raw)))

    async def run():
        if text is not None:
            path.write_text(text, encoding="utf-8")
        return ToolResult(status=OK, value="done")

    return asyncio.run(pipeline.dispatch(ToolExecution(call_id="c", name=name, arguments={"path": str(path)}), run))


def _record(memory: AutoMemory) -> dict:
    return json.loads((memory.root / anchors.SIDECAR_NAME).read_text(encoding="utf-8"))


# -- the files a note names ----------------------------------------------------------------------


def test_a_file_named_in_a_notes_text_is_watched_without_being_declared(project):
    ws, memory = project
    door = door_for(ws, memory)
    text = note("lesson", "The deploy in `ops/deploy.py` must go through CI; `pytest -q` and `nope/missing.py` name nothing here.")
    assert _dispatch(door, "write_file", memory.root / "deploys.md", text).status == OK
    (entry,) = _record(memory)["deploys.md"]["anchors"]
    assert entry["path"] == "ops/deploy.py" and entry["named"] is True and entry["digest"]

    memory.add_index_line("deploys.md", "Deploys", "through CI")
    assert "check before relying" not in memory.inject_block(via_files=True)
    (ws / "ops" / "deploy.py").write_text("def deploy():\n    return 'by hand'\n", encoding="utf-8")
    assert "[check before relying on it: ops/deploy.py changed since this was written]" in memory.inject_block(via_files=True)


def test_only_a_token_that_is_a_file_here_counts_and_a_line_number_is_not_part_of_it(project):
    ws, _ = project
    anchor = project_anchor([ws], ws)
    body = "See `ops/deploy.py:12`, the folders `ops` and `ops/`, the flag `--fast` and `README`."
    assert [raw for raw, _ in named_files(body, anchor)] == ["ops/deploy.py"]
    declared = [(ws / "ops" / "deploy.py").resolve()]
    assert named_files(body, anchor, skip=declared) == []


# -- what the index line says ----------------------------------------------------------------------


def test_ongoing_work_carries_its_date_and_other_types_do_not(project):
    ws, memory = project
    door = door_for(ws, memory)
    for name, kind in (("freeze.md", "project"), ("style.md", "feedback")):
        text = note(kind, f"A {kind} fact.")
        (memory.root / name).write_text(text, encoding="utf-8")
        door.record(name, text)
        memory.add_index_line(name, name, "hook")
    today = datetime.now(timezone.utc).date().isoformat()
    lines = {l.split("](")[1].split(")")[0]: l for l in memory.inject_block(via_files=True).splitlines() if "](" in l}
    assert lines["freeze.md"].endswith(f"(as of {today})") and "as of" not in lines["style.md"]


def test_a_note_written_after_web_or_connector_content_says_so(project):
    ws, memory = project
    text = note("reference", "The vendor's limits page: https://vendor.example/limits")
    (memory.root / "limits.md").write_text(text, encoding="utf-8")
    door_for(ws, memory, external=lambda: True).record("limits.md", text)
    (memory.root / "plain.md").write_text(text, encoding="utf-8")
    door_for(ws, memory).record("plain.md", text)
    memory.add_index_line("limits.md", "Limits", "vendor limits")
    memory.add_index_line("plain.md", "Plain", "no outside content")
    block = memory.inject_block(via_files=True)
    (marked,) = [l for l in block.splitlines() if "check it against the source" in l]
    assert "limits.md" in marked


def test_the_turn_is_what_decides_whether_outside_content_was_read():
    def call(name):
        return {"role": "assistant", "tool_calls": [{"function": {"name": name}}]}

    user = {"role": "user", "content": "go"}
    assert read_outside_content([user, call("web_fetch")]) is True
    assert read_outside_content([user, call("mcp__jira__get_issue")]) is True
    assert read_outside_content([user, call("read_file"), call("run_shell")]) is False
    # An earlier turn's page is not this turn's.
    assert read_outside_content([user, call("web_search"), user, call("read_file")]) is False


def test_a_note_nobody_read_for_a_month_says_it_can_go(project):
    ws, memory = project
    door = door_for(ws, memory)
    text = note("feedback", "Deploy through CI.")
    (memory.root / "ci.md").write_text(text, encoding="utf-8")
    door.record("ci.md", text)
    index = "- [CI](ci.md) — deploys"
    later = datetime.now(timezone.utc) + timedelta(days=31)
    assert "not read for over 30 days" not in anchors.annotate(index, memory.root)
    assert "not read for over 30 days" in anchors.annotate(index, memory.root, now=later)
    # A read resets it: an old note read last week is in use.
    door.read("ci.md")
    assert "not read" not in anchors.annotate(index, memory.root, now=datetime.now(timezone.utc) + timedelta(days=29))
    sidecar = memory.root / anchors.SIDECAR_NAME
    data = _record(memory)
    data["ci.md"]["written"] = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
    sidecar.write_text(json.dumps(data), encoding="utf-8")
    assert "not read" not in anchors.annotate(index, memory.root)
    # A note the harness has no record of gets no mark, however old.
    assert anchors.annotate("- [Old](old.md) — from before", memory.root, now=later) == "- [Old](old.md) — from before"


# -- reads and rewrites ----------------------------------------------------------------------------


def test_a_read_of_a_memory_is_counted_through_either_door(project):
    ws, memory = project
    door = door_for(ws, memory)
    text = note("feedback", "Deploy through CI.")
    assert _dispatch(door, "write_file", memory.root / "ci.md", text).status == OK
    assert "reads" not in _record(memory)["ci.md"]
    _dispatch(door, "read_file", memory.root / "ci.md")
    _dispatch(door, "read_file", memory.root / "ci.md")
    _dispatch(door, "read_file", memory.root / INDEX_NAME)  # the index is loaded anyway
    _dispatch(door, "grep", memory.root / "ci.md")  # a search is not a read of the note
    entry = _record(memory)["ci.md"]
    assert entry["reads"] == 2 and entry["read"] and INDEX_NAME not in _record(memory)

    memory.admit = door.admit
    (tool,) = auto_memory_tools(memory)
    tool("read", target="ci.md")
    assert _record(memory)["ci.md"]["reads"] == 3


def test_a_rewrite_keeps_what_the_note_said_and_when_it_was_first_written(project):
    ws, memory = project
    door = door_for(ws, memory)
    first = note("project", "Freeze until 2026-10-20.")
    assert _dispatch(door, "write_file", memory.root / "freeze.md", first).status == OK
    sidecar = memory.root / anchors.SIDECAR_NAME
    data = _record(memory)
    data["freeze.md"]["created"] = created = "2026-01-05T08:00:00+00:00"
    sidecar.write_text(json.dumps(data), encoding="utf-8")
    door.read("freeze.md")
    assert memory.versions("freeze.md") == []

    assert _dispatch(door, "write_file", memory.root / "freeze.md", note("project", "Freeze lifted on 2026-10-18.")).status == OK
    (kept,) = memory.versions("freeze.md")
    assert "Freeze until 2026-10-20." in (memory.root / ".history" / kept).read_text(encoding="utf-8")
    entry = _record(memory)["freeze.md"]
    assert entry["created"] == created and entry["reads"] == 1


# -- the progress log ------------------------------------------------------------------------------


def test_an_item_is_not_marked_done_while_the_turns_work_is_unchecked():
    before = "- [x] scaffold\n- [ ] parser\n- [ ] writer\n"
    after = "- [x] scaffold\n- [x] parser\n- [ ] writer\n"
    assert judge_progress(after, before=before, unsettled="") is None
    for state, said in (("unverified", "nothing has run since"), ("check_failed", "last check failed")):
        refused = judge_progress(after, before=before, unsettled=state)
        assert refused is not None and refused.rule == "memory-unverified" and said in refused.reason
    # Nothing newly done: rewording, adding items, or un-marking one is not a claim.
    assert judge_progress("- [x] scaffold\n- [ ] parser, then tests\n- [ ] docs\n", before=before, unsettled="unverified") is None
    assert judge_progress("- [ ] scaffold\n- [ ] parser\n", before=before, unsettled="check_failed") is None


def test_the_progress_log_is_held_to_what_a_session_loads_and_carries_no_secret():
    assert judge_progress("x" * (PROGRESS_MAX_CHARS + 1)).rule == "memory-progress"
    assert judge_progress("- [ ] rotate sk-" + "a1B2c3D4e5F6g7H8" * 3).rule == "memory-secret"
    assert judge_progress("- [ ] parser\nNext: writer\nBlocked: none\n") is None


def test_the_door_refuses_a_done_mark_through_either_tool_and_leaves_the_log_as_it_was(project):
    ws, memory = project
    state = {"unsettled": ""}
    door = door_for(ws, memory, unsettled=lambda: state["unsettled"])
    log = memory.root / PROGRESS_NAME
    start = "- [x] scaffold\n- [ ] parser\n"
    assert _dispatch(door, "write_file", log, start).status == OK

    state["unsettled"] = "unverified"
    refused = _dispatch(door, "write_file", log, "- [x] scaffold\n- [x] parser\n")
    assert refused.status == BLOCKED and refused.value["rule"] == "memory-unverified"
    assert log.read_text(encoding="utf-8") == start
    # What was done before is not a new claim: the log can still be added to mid-edit.
    grown = "- [x] scaffold\n- [ ] parser\n- [ ] writer\n"
    assert _dispatch(door, "write_file", log, grown).status == OK

    memory.admit = door.admit
    assert memory.write("progress", "- [x] scaffold\n- [x] parser\n- [ ] writer\n", mode="overwrite")["rule"] == "memory-unverified"
    assert memory.write("progress", grown + "Next: writer\n", mode="overwrite")["ok"] is True
    state["unsettled"] = ""
    assert memory.write("progress", "- [x] scaffold\n- [x] parser\n- [ ] writer\n", mode="overwrite")["ok"] is True
    assert _record(memory)[PROGRESS_NAME]["kind"] == "progress"
    assert "last written " in memory.progress_block()


# -- end to end: the engine's own word on whether the work is checked -------------------------------


class _Scripted(ProviderClient):
    def __init__(self, turns):
        self.turns = list(turns)

    def complete(self, model, messages, tools):
        return self.turns.pop(0)

    def capabilities(self, model):
        return ModelCapabilities()


def test_a_session_that_edited_code_cannot_call_the_item_done_before_anything_ran(tmp_path):
    from harness.compose import build_engine
    from harness.modes.agents import reeve_agent
    from harness.safety.permissions import Mode
    from harness.llm.providers import AssistantTurn, ToolCall

    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "parser.py").write_text("def parse(text):\n    return text\n", encoding="utf-8")
    memory = AutoMemory(tmp_path / "state" / "projects" / "p" / "memory")
    memory.ensure()
    log = memory.root / PROGRESS_NAME
    log.write_text("- [ ] parser\n", encoding="utf-8")

    def call(cid, path, content):
        return AssistantTurn(
            text="", finish_reason="tool_calls",
            tool_calls=[ToolCall(id=cid, name="write_file", arguments={"path": str(path), "content": content})],
        )

    turns = [
        AssistantTurn(text="", finish_reason="tool_calls",
                      tool_calls=[ToolCall(id="r1", name="read_file", arguments={"path": str(ws / "parser.py")})]),
        call("e1", ws / "parser.py", "def parse(text):\n    return text.strip()\n"),
        AssistantTurn(text="", finish_reason="tool_calls",
                      tool_calls=[ToolCall(id="r2", name="read_file", arguments={"path": str(log)})]),
        call("p1", log, "- [x] parser\n"),
        AssistantTurn(text="done", finish_reason="stop"),
    ]
    engine = build_engine(
        agent=reeve_agent(), workspace=ws, provider=_Scripted(turns), auto_memory=memory, model="m",
        mode=Mode.DANGER_FULL_ACCESS,
    )
    try:
        async def run():
            return [e async for e in engine.run("finish the parser")]

        asyncio.run(run())
        results = {m["tool_call_id"]: str(m.get("content")) for m in engine.messages if m.get("role") == "tool"}
        assert "memory-unverified" in results["p1"] and "nothing has run since" in results["p1"]
        assert log.read_text(encoding="utf-8") == "- [ ] parser\n"
    finally:
        engine.executor.close()
