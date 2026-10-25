"""Nested AGENTS.md discovery — the parent chain at build, and per-directory files
injected on demand at the next turn's start (the CLAUDE.md hierarchy)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from harness.context.compaction import CompactionState
from harness.loop import TurnEngine
from harness.safety.permissions import PermissionEngine
from harness.session.project import load_agents_md, nested_agents_md
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient
from harness.tools import ToolRegistry
from harness.tools.observed import ObservedFiles
from harness.llm.wire import wire_message


class _Provider(ProviderClient):
    def complete(self, **_kw):
        return AssistantTurn(text="ok", finish_reason="stop")

    def capabilities(self, model):
        return ModelCapabilities()


def _engine(tmp_path) -> TurnEngine:
    return TurnEngine(
        provider=_Provider(),
        registry=ToolRegistry(),
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5",
        messages=[{"role": "system", "content": "sys"}],
    )


def _turn(engine: TurnEngine, text: str) -> dict:
    """Open a turn the way the loop does: the message joins the thread, then it is stamped."""
    message = {"role": "user", "content": text}
    engine.messages.append(message)
    engine._stamp_nested_agents(message)
    return message


def _read(engine: TurnEngine, path: Path, call_id: str) -> None:
    """A successful `read_file` of `path`, as the thread records it."""
    engine.messages.append(
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": "read_file", "arguments": json.dumps({"path": str(path)})},
                }
            ],
        }
    )
    engine.messages.append({"role": "tool", "tool_call_id": call_id, "content": "x = 1"})
    engine.observed.observe(str(path))


def _workspace(tmp_path) -> tuple[Path, TurnEngine]:
    ws = tmp_path / "ws"
    (ws / "pkg").mkdir(parents=True)
    (ws / "pkg" / "AGENTS.md").write_text("PKG RULE", encoding="utf-8")
    (ws / "pkg" / "mod.py").write_text("x = 1\n", encoding="utf-8")
    engine = _engine(tmp_path)
    engine.workspace = str(ws)
    engine.observed = ObservedFiles(resolve=lambda raw: str(Path(raw)))
    return ws, engine


def _cut(engine: TurnEngine, boundary: int) -> None:
    engine.compaction_state = CompactionState(
        boundary_index=boundary, summary_text="earlier work", working_state=""
    )


def test_a_stamp_a_cut_swallowed_is_stated_again(tmp_path):
    """The conventions rode a user message that a compaction (or a fold) has since replaced
    with a block. They are no longer in the request, so "stated once per session" would
    leave the agent working in that directory without them for the rest of the session.
    The same rule the live-context block already follows: swallowed means restate."""
    ws, engine = _workspace(tmp_path)
    _read(engine, ws / "pkg" / "mod.py", "c1")
    first = _turn(engine, "continue")
    assert "PKG RULE" in first["api_content"]
    stamped_at = engine.messages.index(first)

    _read(engine, ws / "pkg" / "mod.py", "c2")  # still working there, after the stamp
    _cut(engine, stamped_at + 1)  # the block now stands for the message that carried it

    again = _turn(engine, "next")
    assert "PKG RULE" in again.get("api_content", "")
    # Stated again once, not on every turn after the cut.
    assert "api_content" not in _turn(engine, "and then")


def test_a_stamp_still_in_the_kept_tail_is_not_repeated(tmp_path):
    ws, engine = _workspace(tmp_path)
    _read(engine, ws / "pkg" / "mod.py", "c1")
    first = _turn(engine, "continue")
    _cut(engine, engine.messages.index(first))  # the cut stops just before the stamp

    assert "api_content" not in _turn(engine, "next")


def test_a_directory_that_left_the_view_gets_its_conventions_back_when_work_returns(tmp_path):
    """A cut is supposed to free what is no longer in use. A directory none of whose files
    is in the kept tail is not restated at once; it is when the agent reads there again."""
    ws, engine = _workspace(tmp_path)
    _read(engine, ws / "pkg" / "mod.py", "c1")
    first = _turn(engine, "continue")
    engine.messages.append({"role": "assistant", "content": "done with pkg"})
    _cut(engine, engine.messages.index(first) + 2)  # nothing of pkg is in the tail

    assert "api_content" not in _turn(engine, "something else")

    _read(engine, ws / "pkg" / "mod.py", "c2")
    assert "PKG RULE" in _turn(engine, "back to pkg").get("api_content", "")


def test_a_reopened_session_knows_what_its_thread_already_states(tmp_path):
    """The record of what was stated rides the message, so an engine rebuilt over the same
    thread does not state it a second time."""
    ws, engine = _workspace(tmp_path)
    _read(engine, ws / "pkg" / "mod.py", "c1")
    _turn(engine, "continue")

    reopened = _engine(tmp_path)
    reopened.messages = list(engine.messages)
    reopened.workspace = str(ws)
    reopened.observed = ObservedFiles(resolve=lambda raw: str(Path(raw)))
    reopened.observed.observe(str(ws / "pkg" / "mod.py"))

    assert "api_content" not in _turn(reopened, "next")


def test_a_staged_undo_that_hides_the_stamp_restates_it(tmp_path):
    ws, engine = _workspace(tmp_path)
    _read(engine, ws / "pkg" / "mod.py", "c1")
    first = _turn(engine, "continue")
    at = engine.messages.index(first)
    engine.messages.append({"role": "assistant", "content": "ok"})
    engine.revert_state = {"boundary": at, "staged_at": len(engine.messages)}

    assert "PKG RULE" in _turn(engine, "try again").get("api_content", "")


def test_a_path_scoped_rule_follows_the_same_rule(tmp_path):
    ws, engine = _workspace(tmp_path)
    rules = ws / ".agents" / "rules"
    rules.mkdir(parents=True)
    (rules / "py.md").write_text("---\npaths:\n  - pkg/**\n---\n\nPY RULE", encoding="utf-8")
    _read(engine, ws / "pkg" / "mod.py", "c1")
    first = _turn(engine, "continue")
    assert "PY RULE" in first["api_content"]
    assert "api_content" not in _turn(engine, "next")

    _read(engine, ws / "pkg" / "mod.py", "c2")
    _cut(engine, engine.messages.index(first) + 1)

    assert "PY RULE" in _turn(engine, "after the cut").get("api_content", "")


def test_the_record_of_what_was_stated_is_never_sent(tmp_path):
    ws, engine = _workspace(tmp_path)
    _read(engine, ws / "pkg" / "mod.py", "c1")
    first = _turn(engine, "continue")

    sent = wire_message(first)
    assert set(sent) == {"role", "content"}
    assert "PKG RULE" in sent["content"]


def test_the_parent_chain_reads_outermost_first(tmp_path):
    root = tmp_path / "repo"
    ws = root / "packages" / "app"
    ws.mkdir(parents=True)
    (root / "AGENTS.md").write_text("REPO RULE", encoding="utf-8")
    (ws / "AGENTS.md").write_text("APP RULE", encoding="utf-8")
    glob = tmp_path / "global-AGENTS.md"
    glob.write_text("GLOBAL RULE", encoding="utf-8")

    block = load_agents_md(ws, managed_path=glob)

    # The most specific file reads last and wins by reading order.
    assert block.index("GLOBAL RULE") < block.index("REPO RULE") < block.index("APP RULE")
    assert 'path="' in block  # a parent file names the directory it scopes


def test_nested_files_are_discovered_root_most_first_and_skipped_once(tmp_path):
    ws = tmp_path / "ws"
    (ws / "pkg" / "deep").mkdir(parents=True)
    (ws / "AGENTS.md").write_text("ROOT RULE", encoding="utf-8")
    (ws / "pkg" / "AGENTS.md").write_text("PKG RULE", encoding="utf-8")
    (ws / "pkg" / "deep" / "AGENTS.md").write_text("DEEP RULE", encoding="utf-8")
    (ws / "pkg" / "mod.py").write_text("x = 1\n", encoding="utf-8")
    (ws / "pkg" / "deep" / "mod.py").write_text("x = 2\n", encoding="utf-8")

    found = nested_agents_md(ws, [str(ws / "pkg" / "deep" / "mod.py")])

    assert [rel for rel, _ in found] == ["pkg", "pkg/deep"]
    assert "PKG RULE" in found[0][1] and "DEEP RULE" in found[1][1]
    # The workspace root's own file is already in the system prompt: never a candidate.
    assert all(rel != "." for rel, _ in found)
    # Already-injected files are not restated.
    assert nested_agents_md(ws, [str(ws / "pkg" / "mod.py")], skip=["pkg"]) == []
    # A path outside the workspace is not this rule's business.
    outside = tmp_path / "other" / "x.py"
    outside.parent.mkdir()
    outside.write_text("y = 1\n", encoding="utf-8")
    assert nested_agents_md(ws, [str(outside)]) == []


def test_one_pass_is_capped(tmp_path):
    ws = tmp_path / "ws"
    for index in range(8):
        directory = ws / f"p{index}"
        directory.mkdir(parents=True)
        (directory / "AGENTS.md").write_text(f"RULE {index}", encoding="utf-8")
        (directory / "m.py").write_text("x = 1\n", encoding="utf-8")

    found = nested_agents_md(ws, [str(ws / f"p{i}" / "m.py") for i in range(8)])

    assert len(found) == 6  # each file is prompt weight; the next turn continues


def test_the_engine_stamps_nested_agents_once_at_the_next_turn(tmp_path):
    ws = tmp_path / "ws"
    (ws / "pkg").mkdir(parents=True)
    (ws / "pkg" / "AGENTS.md").write_text("PKG RULE", encoding="utf-8")
    (ws / "pkg" / "mod.py").write_text("x = 1\n", encoding="utf-8")

    engine = _engine(tmp_path)
    engine.workspace = str(ws)
    engine.observed = ObservedFiles(resolve=lambda raw: str(Path(raw)))
    engine.observed.observe(str(ws / "pkg" / "mod.py"))

    first = _turn(engine, "continue")
    assert "PKG RULE" in first["api_content"]
    assert 'path="pkg"' in first["api_content"]

    second = _turn(engine, "next")
    assert "api_content" not in second  # stated once while the first is still in the view


def test_inject_agents_off_and_remote_worlds_skip_the_pass(tmp_path):
    ws = tmp_path / "ws"
    (ws / "pkg").mkdir(parents=True)
    (ws / "pkg" / "AGENTS.md").write_text("PKG RULE", encoding="utf-8")
    (ws / "pkg" / "mod.py").write_text("x = 1\n", encoding="utf-8")

    engine = _engine(tmp_path)
    engine.workspace = str(ws)
    engine.observed = ObservedFiles(resolve=lambda raw: str(Path(raw)))
    engine.observed.observe(str(ws / "pkg" / "mod.py"))

    engine.inject_agents = False  # a subagent definition opted out
    message = {"role": "user", "content": "continue"}
    engine._stamp_nested_agents(message)
    assert "api_content" not in message

    engine.inject_agents = True
    engine.world = SimpleNamespace(is_remote=True)  # a POSIX path on another host
    remote = {"role": "user", "content": "continue"}
    engine._stamp_nested_agents(remote)
    assert "api_content" not in remote


def test_a_repo_written_for_claude_code_is_read_through_its_claude_md(tmp_path):
    # 2026-09-25 audit, W7: four of the owner's repos keep their conventions only in
    # CLAUDE.md, and the agent never saw them. AGENTS.md still wins when both exist.
    ws = tmp_path / "ws"
    (ws / "pkg").mkdir(parents=True)
    (ws / "CLAUDE.md").write_text("CLAUDE ROOT RULE", encoding="utf-8")
    (ws / "pkg" / ".claude").mkdir()
    (ws / "pkg" / ".claude" / "CLAUDE.md").write_text("PKG CLAUDE RULE", encoding="utf-8")
    (ws / "pkg" / "mod.py").write_text("x = 1\n", encoding="utf-8")

    block = load_agents_md(ws, managed_path=tmp_path / "none.md")
    assert "CLAUDE ROOT RULE" in block and "<project CLAUDE.md>" in block
    assert nested_agents_md(ws, [str(ws / "pkg" / "mod.py")]) == [("pkg", "PKG CLAUDE RULE")]

    (ws / "AGENTS.md").write_text("AGENTS ROOT RULE", encoding="utf-8")
    block = load_agents_md(ws, managed_path=tmp_path / "none.md")
    assert "AGENTS ROOT RULE" in block and "CLAUDE ROOT RULE" not in block


def test_a_nested_claude_md_is_injected_under_its_own_name(tmp_path):
    ws = tmp_path / "ws"
    (ws / "pkg").mkdir(parents=True)
    (ws / "pkg" / "CLAUDE.md").write_text("PKG RULE", encoding="utf-8")
    (ws / "pkg" / "mod.py").write_text("x = 1\n", encoding="utf-8")
    engine = _engine(ws)
    engine.workspace = ws
    engine.inject_agents = True
    engine.observed = ObservedFiles(resolve=lambda raw: str(Path(raw)))
    engine.observed.observe(str(ws / "pkg" / "mod.py"))
    message = {"role": "user", "content": "go"}
    engine._stamp_nested_agents(message)
    assert '<nested CLAUDE.md path="pkg">' in message["api_content"]
