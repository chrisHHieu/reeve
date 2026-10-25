"""Auto memory (`harness/context/memory/auto_memory.py`).

An index plus topic files: the agent's own notes as machine-local markdown. These tests pin
the parts that make it safe and useful — the index is the only thing injected, it is
capped, topic files carry a type + modified stamp, and names cannot escape the directory.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from harness.context.memory.auto_memory import INDEX_MAX_LINES, INDEX_NAME, AutoMemory, auto_memory_tools
from harness.tools import ToolRegistry


@pytest.fixture()
def mem(tmp_path: Path) -> AutoMemory:
    return AutoMemory(tmp_path / "projects" / "proj" / "memory")


def test_empty_memory_injects_nothing(mem: AutoMemory):
    assert mem.inject_block() == ""
    assert mem.list_files() == []


def test_index_write_and_inject(mem: AutoMemory):
    assert mem.write("memory", "# Notes\n- likes tea", mode="overwrite")["ok"]
    assert mem.write("memory", "- timezone: CET")["ok"]  # append
    block = mem.inject_block()
    assert "likes tea" in block
    assert "- timezone: CET" in block
    assert INDEX_NAME in mem.list_files()[0]["name"]


def test_topic_write_adds_type_and_modified_frontmatter(mem: AutoMemory):
    res = mem.write("feedback_testing.md", "**Correction**: run pytest -q", mode="overwrite", note_type="feedback")
    assert res["ok"] and res["type"] == "feedback"
    content = mem.read("feedback_testing.md")["content"]
    assert content.startswith("---\n")
    assert "type: feedback" in content
    assert "modified:" in content
    assert "run pytest -q" in content


def test_topic_type_must_be_known(mem: AutoMemory):
    res = mem.write("x.md", "body", mode="overwrite", note_type="nonsense")
    assert res["ok"] is False and "type must be" in res["error"]


def test_only_the_index_is_injected(mem: AutoMemory):
    mem.write("memory", "# Index\n- topic: feedback_testing.md", mode="overwrite")
    mem.write("feedback_testing.md", "SECRET DETAIL", mode="overwrite", note_type="feedback")
    block = mem.inject_block()
    assert "feedback_testing.md" in block
    assert "SECRET DETAIL" not in block


def test_index_is_truncated_to_the_read_limit(mem: AutoMemory):
    mem.write("memory", "\n".join(f"- note {i}" for i in range(INDEX_MAX_LINES + 50)), mode="overwrite")
    block = mem.inject_block()
    assert "truncated at its read limit" in block
    assert f"- note {INDEX_MAX_LINES + 40}" not in block


def test_write_warns_when_the_index_is_over_limit(mem: AutoMemory):
    res = mem.write("memory", "\n".join(f"- note {i}" for i in range(INDEX_MAX_LINES + 5)), mode="overwrite")
    assert res["ok"] is True
    assert res["index_over_limit"] is True
    assert "rewrite" in res["warning"].lower()


def test_list_and_search(mem: AutoMemory):
    mem.write("memory", "# Index\n- uses PostgreSQL", mode="overwrite")
    mem.write("infra.md", "pgbouncer in front of postgres", mode="overwrite", note_type="project")
    names = [f["name"] for f in mem.list_files()]
    assert names[0] == INDEX_NAME  # index first
    hits = mem.search("postgres")
    assert {h["name"] for h in hits} == {INDEX_NAME, "infra.md"}


def test_edit_and_delete(mem: AutoMemory):
    mem.write("infra.md", "port 5432", mode="overwrite", note_type="project")
    assert mem.edit("infra.md", "5432", "5433")["ok"]
    assert "5433" in mem.read("infra.md")["content"]
    assert mem.delete("infra.md")["ok"]
    assert mem.read("infra.md")["exists"] is False


def test_names_cannot_escape_the_directory(mem: AutoMemory):
    with pytest.raises(ValueError):
        mem.read("../escape")
    with pytest.raises(ValueError):
        mem.write("../../etc/passwd", "nope")


def _reg(mem: AutoMemory, **kwargs) -> ToolRegistry:
    reg = ToolRegistry()
    reg.register_all(auto_memory_tools(mem, **kwargs))
    return reg


def test_tool_actions_round_trip(mem: AutoMemory):
    reg = _reg(mem)
    assert "auto_memory" in reg.names()
    assert reg.execute("auto_memory", {"action": "write", "target": "feedback_x.md", "content": "be terse", "type": "feedback"})["ok"]
    assert "be terse" in reg.execute("auto_memory", {"action": "read", "target": "feedback_x.md"})["content"]
    assert reg.execute("auto_memory", {"action": "list"})["files"]
    assert reg.execute("auto_memory", {"action": "search", "query": "terse"})["matches"]
    assert reg.execute("auto_memory", {"action": "delete", "target": "feedback_x.md"})["ok"]


def test_tool_writes_gated_reads_not(mem: AutoMemory):
    mem.write("memory", "saved", mode="overwrite")
    reg = _reg(mem, saving_enabled=lambda: False)
    assert reg.execute("auto_memory", {"action": "write", "target": "memory", "content": "nope"})["ok"] is False
    assert reg.execute("auto_memory", {"action": "read", "target": "memory"})["exists"] is True
    assert reg.execute("auto_memory", {"action": "list"})["files"]


def test_tool_rejects_unknown_action(mem: AutoMemory):
    assert "unknown action" in _reg(mem).execute("auto_memory", {"action": "nope"})["error"]


# -- parallel batch safety (owner-hit 2026-09-17) -------------------------------


def _widen_read_window(monkeypatch):
    """Sleep after each MEMORY.md read, so a parallel batch's read-modify-write calls
    genuinely overlap. Without it the threads finish in microseconds and the race only
    shows up on a loaded machine, which is a flaky test rather than a regression test."""

    real_read_text = Path.read_text

    def slow_read_text(self, *args, **kwargs):
        text = real_read_text(self, *args, **kwargs)
        if self.name == INDEX_NAME:
            time.sleep(0.05)
        return text

    monkeypatch.setattr(Path, "read_text", slow_read_text)


def _run_through_tool(reg: ToolRegistry, calls: list[dict]) -> list:
    """Fire every call from its own thread, released together, and collect the results."""
    start = threading.Barrier(len(calls))
    results: list = [None] * len(calls)

    def run(i: int) -> None:
        start.wait()
        results[i] = reg.execute("auto_memory", calls[i])

    threads = [threading.Thread(target=run, args=(i,)) for i in range(len(calls))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


def test_concurrent_edits_through_the_tool_do_not_lose_changes(mem: AutoMemory, monkeypatch):
    """The engine dispatches a batch's low-risk calls in parallel (`engine/helpers.py`),
    and `auto_memory` qualifies — six `edit` calls in one model message all touch
    MEMORY.md at once. Each call is a read-modify-write, so unsynchronized they all read
    the same text and the last write wins: the owner-hit where every call answered
    `{"ok": true}` and five of six deletions were gone. The per-path lock has to make
    every edit land, exactly once."""
    _widen_read_window(monkeypatch)
    mem.write("memory", "".join(f"- item {i}\n" for i in range(6)), mode="overwrite")
    reg = _reg(mem)

    results = _run_through_tool(
        reg,
        [
            {
                "action": "edit",
                "target": "memory",
                "old_string": f"- item {i}\n",
                "new_string": "",
            }
            for i in range(6)
        ],
    )

    assert all(r.get("ok") for r in results), results
    assert "item" not in mem.read("memory")["content"]


def test_concurrent_appends_through_the_tool_keep_every_line_once(
    mem: AutoMemory, monkeypatch
):
    """The append half of the same race: each call reads the index, appends its own line
    and writes the whole file back. A lost update here drops a memory's index line while
    the call still says `ok` — and, when the stale write carries another call's line
    along, duplicates it. Each line must be present exactly once."""
    _widen_read_window(monkeypatch)
    mem.write("memory", "# Index\n", mode="overwrite")
    reg = _reg(mem)

    results = _run_through_tool(
        reg,
        [
            {"action": "write", "target": "memory", "content": f"- item {i}", "mode": "append"}
            for i in range(6)
        ],
    )

    assert all(r.get("ok") for r in results), results
    final = mem.read("memory")["content"]
    for i in range(6):
        assert final.count(f"- item {i}") == 1, final


# -- engine wiring ------------------------------------------------------------


class _StubProvider:
    def complete(self, **kwargs):
        raise NotImplementedError

    def capabilities(self, model):
        raise NotImplementedError


def test_engine_keeps_memory_with_the_file_tools_like_claude_code(tmp_path):
    """The memory directory is a managed root and the file tools are the memory
    tools — no dedicated tool on the wire, the instruction naming the
    directory, then the index. Topic files are read on demand, never injected."""
    from harness.compose import build_engine
    from harness.modes.agents import reeve_agent

    mem = AutoMemory(tmp_path / "projects" / "p" / "memory")
    mem.ensure()
    mem.write("no-db-mocks.md", "SECRET DETAIL", mode="overwrite", note_type="correction")
    mem.write("memory", "# Index\n- [No DB mocks](no-db-mocks.md) — integration tests hit a real database", mode="overwrite")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    engine = build_engine(agent=reeve_agent(), workspace=workspace, provider=_StubProvider(), auto_memory=mem)
    try:
        assert "auto_memory" not in engine.registry.names()
        sys_prompt = engine.messages[0]["content"]
        assert "# Memory" in sys_prompt and str(mem.root.resolve()) in sys_prompt
        assert "integration tests hit a real database" in sys_prompt
        assert "SECRET DETAIL" not in sys_prompt  # topic files are not injected
        memory_root = [r for r in engine.roots if getattr(r, "managed", False)]
        assert [r.path for r in memory_root] == [mem.root.resolve()]
        # A write there is allowed without an ask; the workspace still asks.
        target = str(mem.root / "deploys.md")
        assert engine.permissions.evaluate("write_file", {"path": target, "content": "x"}).allowed
        elsewhere = str(workspace / "a.py")
        assert not engine.permissions.evaluate("write_file", {"path": elsewhere, "content": "x"}).allowed
        # ...and the file tool itself reaches it.
        write = engine.registry.execute("write_file", {"path": target, "content": "---\nname: x\n---\nfact"})
        assert (mem.root / "deploys.md").read_text(encoding="utf-8").endswith("fact")
        assert write
    finally:
        engine.executor.close()


def test_a_rootless_session_keeps_the_memory_tool(tmp_path):
    """No root to hang the memory directory off (it would become the primary): the
    dedicated tool stays, as it does for a remote world."""
    from harness.compose import build_engine
    from harness.modes.agents import get_agent

    mem = AutoMemory(tmp_path / "projects" / "p" / "memory")
    agent = get_agent("chat")
    engine = build_engine(agent=agent, workspace=None, provider=_StubProvider(), auto_memory=mem)
    assert not getattr(engine, "roots", None)
    assert "auto_memory" in engine.registry.names()


def test_engine_without_auto_memory_has_no_tool(tmp_path):
    from harness.compose import build_engine
    from harness.modes.agents import reeve_agent

    engine = build_engine(agent=reeve_agent(), workspace=tmp_path, provider=_StubProvider())
    try:
        assert "auto_memory" not in engine.registry.names()
    finally:
        engine.executor.close()




class _DreamProvider:
    def __init__(self, reply: str) -> None:
        self._reply = reply

    def complete(self, **kwargs):
        return type("T", (), {"text": self._reply})()


def test_a_near_limit_index_gets_a_shortening_reminder(tmp_path):
    """Memory v2 Phase 2: the nudge arrives while the index can still be tidied cheaply.
    Past the cap the same write carries the rewrite directive instead (test above)."""
    from harness.context.memory.auto_memory import INDEX_MAX_LINES

    am = AutoMemory(tmp_path / "am")
    near = "\n".join(f"- note {i}" for i in range(INDEX_MAX_LINES - 5))
    result = am.write("memory", near, mode="overwrite")

    assert result["ok"] is True
    assert "index_over_limit" not in result
    assert "close to its read limit" in result["warning"]


def test_the_progress_log_opens_each_new_session_whole_and_bounded(tmp_path):
    """Work that spans sessions resumes from the log instead of re-exploring."""
    from harness.compose import build_engine
    from harness.modes.agents import reeve_agent
    from harness.context.memory.auto_memory import PROGRESS_MAX_CHARS, PROGRESS_NAME

    am = AutoMemory(tmp_path / "am")
    am.write("progress", "- [x] scaffold\n- [ ] draft section 2\nNext: section 2, then check.", mode="overwrite")
    assert am.read(PROGRESS_NAME)["content"].startswith("- [x] scaffold")  # plain, no frontmatter
    ws = tmp_path / "ws"
    ws.mkdir()
    engine = build_engine(agent=reeve_agent(), workspace=ws, provider=object(), auto_memory=am, model="m")
    try:
        system = engine.messages[0]["content"]
        assert "draft section 2" in system and "check what it calls done" in system
    finally:
        engine.executor.close()

    (am.root / PROGRESS_NAME).write_text("x" * (PROGRESS_MAX_CHARS + 500), encoding="utf-8")
    assert "x" * 50 not in am.progress_block()


def test_a_long_progress_log_is_shown_as_its_headings_and_open_items(tmp_path):
    """The store's logs were diaries: about 60% of each the detail of finished work, carried on
    every request of every session in the project. A long one is shown as what a resume
    starts from, and the session is told where the rest is and how to read it."""
    from harness.context.memory.progress_log import INLINE_CHARS

    am = AutoMemory(tmp_path / "am")
    finished = "\n".join(f"- shipped part {i} with a long account of how it went" for i in range(60))
    log = (
        "# PROGRESS - report\n## Update 12/10\n" + finished
        + "\n## Next\n- [ ] draft section 2\n  - [ ] check the totals\n- [x] scaffold\n"
    )
    assert len(log) > INLINE_CHARS
    am.write("progress", log, mode="overwrite")
    block = am.progress_block(via_files=True)
    assert "## Update 12/10" in block and "## Next" in block
    assert "- [ ] draft section 2" in block and "  - [ ] check the totals" in block
    assert "shipped part" not in block and "- [x] scaffold" not in block
    assert f"{len(log.strip()):,} characters" in block and "`read_file`" in block
    assert "target `progress`" in am.progress_block()

    am.write("progress", "- [x] scaffold\n- [ ] draft section 2\nNext: section 2.", mode="overwrite")
    assert "Next: section 2." in am.progress_block()  # a short log is still shown whole


def test_promote_copies_into_the_repo_with_a_stamp(tmp_path):
    from pathlib import Path

    am = AutoMemory(tmp_path / "am")
    am.write(
        "feedback_ci.md", "Deploys go through CI.", mode="overwrite", note_type="feedback"
    )
    ws = tmp_path / "ws"
    ws.mkdir()

    result = am.promote("feedback_ci.md", workspace_root=ws)

    assert result["ok"] is True
    target = Path(result["target"])
    assert target == ws / ".agents" / "memory" / "feedback_ci.md"
    text = target.read_text(encoding="utf-8")
    assert "Deploys go through CI." in text
    assert "promoted_at:" in text
    assert "type: feedback" in text


def test_promote_refuses_the_index_and_missing_files(tmp_path):
    am = AutoMemory(tmp_path / "am")
    am.write("memory", "# Memory Index", mode="overwrite")
    ws = tmp_path / "ws"
    ws.mkdir()
    assert am.promote("memory", workspace_root=ws)["ok"] is False
    assert am.promote("nope.md", workspace_root=ws)["ok"] is False


# -- relation-line cleanup on delete (session 67ae406347b0's recorded gap) ---


def _memory_file(root, name: str, body: str) -> None:
    (root / name).write_text(body, encoding="utf-8")


def test_delete_removes_links_to_the_removed_file(tmp_path):
    from harness.context.memory.auto_memory import AutoMemory

    _memory_file(tmp_path, "a.md", "the removed one")
    _memory_file(
        tmp_path,
        "b.md",
        "b body\n\n    **Liên quan:** [A](a.md) · [C](c.md)\n",
    )
    _memory_file(tmp_path, "MEMORY.md", "# Memory Index\n\n- [A](a.md)\n- [B](b.md)\n")
    store = AutoMemory(str(tmp_path))

    result = store.delete("a.md")

    assert result["ok"] is True
    text = (tmp_path / "b.md").read_text(encoding="utf-8")
    assert "a.md" not in text
    assert "[C](c.md)" in text
    # The index is a file listing, not a relation: untouched.
    assert "- [A](a.md)" in (tmp_path / "MEMORY.md").read_text(encoding="utf-8")


def test_a_relation_line_with_nothing_left_is_dropped(tmp_path):
    from harness.context.memory.auto_memory import AutoMemory

    _memory_file(tmp_path, "a.md", "the removed one")
    _memory_file(tmp_path, "b.md", "b body\n\n**Liên quan:** [A](a.md)\n")
    store = AutoMemory(str(tmp_path))

    store.delete("a.md")

    assert "Liên quan" not in (tmp_path / "b.md").read_text(encoding="utf-8")


def test_other_relation_lines_are_untouched(tmp_path):
    from harness.context.memory.auto_memory import AutoMemory

    _memory_file(tmp_path, "a.md", "x")
    _memory_file(tmp_path, "b.md", "**Liên quan:** [A](a.md) · [D](d.md)\n")
    store = AutoMemory(str(tmp_path))

    assert store.clean_links_to(["missing.md"]) == 0
    assert (tmp_path / "b.md").read_text(encoding="utf-8") == (
        "**Liên quan:** [A](a.md) · [D](d.md)\n"
    )


def test_a_credential_shaped_write_is_refused(tmp_path):
    from harness.context.memory.auto_memory import AutoMemory

    store = AutoMemory(str(tmp_path))

    result = store.write("notes.md", "the deploy key is ghp_" + "0123456789abcdefghijklmnopqrstuv")

    assert result["ok"] is False
    assert "credential" in result["error"]
    assert not (tmp_path / "notes.md").exists()


def test_a_clean_write_still_lands(tmp_path):
    from harness.context.memory.auto_memory import AutoMemory

    store = AutoMemory(str(tmp_path))

    result = store.write("notes.md", "the deploy key lives in the vault under ops/deploy")

    assert result["ok"] is True
    assert "vault" in (tmp_path / "notes.md").read_text(encoding="utf-8")


def test_a_duplicate_write_is_dropped_with_a_note(tmp_path):
    from harness.context.memory.auto_memory import AutoMemory

    store = AutoMemory(str(tmp_path))
    first = store.write("notes.md", "Deploys go through CI, never by hand.")
    second = store.write("notes.md", "Deploys go through CI, never by hand.")

    assert first["ok"] is True and "action" not in first
    assert second["ok"] is True and second["action"] == "duplicate"
    from harness.context.memory import memory_format

    _meta, body = memory_format.split((tmp_path / "notes.md").read_text(encoding="utf-8"))
    assert body.count("Deploys go through CI") == 1


def test_a_near_copy_is_also_dropped(tmp_path):
    from harness.context.memory.auto_memory import AutoMemory

    store = AutoMemory(str(tmp_path))
    store.write("notes.md", "The release branch is cut every Thursday afternoon by the ops team.")
    second = store.write(
        "notes.md", "The release branch is cut every Thursday afternoon by the ops team!"
    )

    assert second.get("action") == "duplicate"


def test_a_different_fact_still_appends(tmp_path):
    from harness.context.memory.auto_memory import AutoMemory

    store = AutoMemory(str(tmp_path))
    store.write("notes.md", "Deploys go through CI.")
    second = store.write("notes.md", "The staging key lives in the vault.")

    assert second["ok"] is True and second.get("action") != "duplicate"


# -- versioning (the rollback guardrail from the Anthropic memory talk) -----------------


def test_a_write_keeps_the_previous_version(tmp_path):
    from harness.context.memory.auto_memory import AutoMemory

    store = AutoMemory(str(tmp_path))
    store.write("notes.md", "first fact")
    store.write("notes.md", "second fact")

    assert store.versions("notes.md")
    restored = store.restore("notes.md")

    assert restored["ok"] is True
    assert "first fact" in (tmp_path / "notes.md").read_text(encoding="utf-8")


def test_a_delete_is_recoverable(tmp_path):
    from harness.context.memory.auto_memory import AutoMemory

    store = AutoMemory(str(tmp_path))
    store.write("notes.md", "keep me")
    store.delete("notes.md")

    assert store.restore("notes.md")["ok"] is True
    assert "keep me" in (tmp_path / "notes.md").read_text(encoding="utf-8")


def test_restoring_a_missing_version_is_an_error(tmp_path):
    from harness.context.memory.auto_memory import AutoMemory

    store = AutoMemory(str(tmp_path))
    store.write("notes.md", "only fact")

    assert store.restore("notes.md", version="notes.md.19990101T000000000000.md")["ok"] is False


# -- cross-process concurrency (the talk's hashing guardrail, our shape) ----------------


def test_the_process_lock_is_taken_and_released(tmp_path):
    from harness.context.memory.auto_memory import _process_lock

    target = tmp_path / "notes.md"
    lock = tmp_path / ".notes.md.lock"

    with _process_lock(target):
        assert lock.is_dir()

    assert not lock.exists()


def test_a_stale_lock_is_stolen(tmp_path):
    import os
    import time

    from harness.context.memory.auto_memory import AutoMemory

    store = AutoMemory(str(tmp_path))
    lock = tmp_path / ".notes.md.lock"
    lock.mkdir()
    old = time.time() - 3600
    os.utime(lock, (old, old))

    result = store.write("notes.md", "a fact after a crashed writer")

    assert result["ok"] is True
    assert not lock.exists()


def test_a_full_file_refusal_carries_entries_and_an_instruction(tmp_path, monkeypatch):
    from harness.context.memory import auto_memory

    monkeypatch.setattr(auto_memory, "MAX_FILE_CHARS", 400)
    store = auto_memory.AutoMemory(str(tmp_path))
    store.write("notes.md", "first fact " * 10)

    result = store.write("notes.md", "second fact " * 30)

    assert result["ok"] is False
    assert "full" in result["error"]
    assert result["entries"] and "first fact" in result["entries"][0]
    assert "Consolidate now" in result["instruction"]


def test_three_refusals_skip_the_save_for_the_turn(tmp_path, monkeypatch):
    from harness.context.memory import auto_memory

    monkeypatch.setattr(auto_memory, "MAX_FILE_CHARS", 400)
    store = auto_memory.AutoMemory(str(tmp_path))
    tool = auto_memory.auto_memory_tools(store)[0]
    tool("write", "notes.md", "first fact " * 10)

    outcomes = [tool("write", "notes.md", "second fact " * 30) for _ in range(3)]

    assert outcomes[0]["entries"] is not None
    assert outcomes[1]["entries"] is not None
    assert "save skipped this turn" in outcomes[2]["error"]


def test_an_outside_change_is_refused_not_overwritten(tmp_path, monkeypatch):
    from harness.context.memory import auto_memory

    store = auto_memory.AutoMemory(str(tmp_path))
    store.write("notes.md", "first")

    calls = {"n": 0}

    def fake_digest(path):
        calls["n"] += 1
        return "same" if calls["n"] == 1 else "changed"

    monkeypatch.setattr(auto_memory, "_digest", fake_digest)
    result = store.write("notes.md", "second")

    assert result["ok"] is False
    assert "changed while" in result["error"]
