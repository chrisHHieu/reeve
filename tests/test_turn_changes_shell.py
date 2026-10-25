"""G4 — the receipt names the files a command changed, read from git.

A model that renames a field across nine files with `sed -i` names no path to any write tool,
so the receipt used to be empty: "Worked for 45s · 2 commands" and nothing else, on exactly the
turn a person most needs to see what changed (GUI rendering review, G4).
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable

import pytest

from harness.orchestration import worktree_state
from harness.session.changes import ChangeLog, change_listener
from harness.loop import TurnEngine
from harness.session.events import EventType
from harness.safety.permissions import Mode, PermissionEngine, write_paths
from harness.safety.provenance import resolve as resolve_path
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall
from harness.safety.risk import EXEC_TOOLS
from harness.tools import ToolRegistry

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


class _Scripted(ProviderClient):
    def __init__(self, turns: list[AssistantTurn]) -> None:
        self._turns = list(turns)

    def complete(self, *, model, messages, tools=None, **settings):
        if self._turns:
            return self._turns.pop(0)
        return AssistantTurn(text="done", finish_reason="stop")

    def capabilities(self, model):
        return ModelCapabilities()


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def _repo(root: Path, files: dict[str, str]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@example.invalid")
    _git(root, "config", "user.name", "t")
    _git(root, "config", "core.autocrlf", "false")
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    return root


def _engine(root: Path, commands: dict[str, Callable[[], None]], turns: list[AssistantTurn]):
    def run_shell(command: str) -> str:
        """Run a command.

        Args:
            command: what
        """
        output = commands[command]()
        return output if isinstance(output, str) else "exit 0"

    def write_file(path: str, content: str) -> str:
        """Write a file.

        Args:
            path: where
            content: what
        """
        target = Path(path) if Path(path).is_absolute() else root / path
        target.write_text(content, encoding="utf-8")
        return "written"

    registry = ToolRegistry()
    registry.register(run_shell)
    registry.register(write_file)
    engine = TurnEngine(
        provider=_Scripted(turns),
        registry=registry,
        permissions=PermissionEngine(workspace_root=root, mode=Mode.DANGER_FULL_ACCESS),
        model="m",
    )
    engine.changes = ChangeLog(
        locate=write_paths,
        describe=lambda name, arguments: name,
        consequential=lambda name: True,
        resolve=lambda raw: resolve_path(raw, root),
        root=str(root),
        opaque=lambda name: name in EXEC_TOOLS,
    )
    engine.pipeline.on_pre_execute(change_listener(engine.changes))
    return engine


def _turn(engine: TurnEngine, *calls: tuple[str, str, dict[str, Any]]) -> dict[str, Any]:
    engine.provider._turns[:0] = [
        AssistantTurn(
            tool_calls=[ToolCall(id=cid, name=name, arguments=args) for cid, name, args in calls],
            finish_reason="tool_calls",
        ),
        AssistantTurn(text="Renamed it everywhere.", finish_reason="stop"),
    ]

    async def go():
        return [event async for event in engine.run("go")]

    for event in asyncio.run(go()):
        if event.type in (EventType.TURN_END, EventType.INTERRUPTED, EventType.ERROR):
            return event.data.get("changes", {})
    return {}


def _stats(receipt: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {Path(s["path"]).name: s for s in receipt.get("stats", [])}


def _shell(cid: str, command: str) -> tuple[str, str, dict[str, Any]]:
    return (cid, "run_shell", {"command": command})


def test_files_a_command_changed_are_on_the_receipt(tmp_path):
    root = _repo(
        tmp_path / "repo",
        {"a.py": "qty = 1\nx = qty\n", "b.py": "y = qty\n", "gone.py": "one\ntwo\n", "kept.py": "k\n"},
    )

    def sed() -> None:
        for name in ("a.py", "b.py"):
            path = root / name
            path.write_text(path.read_text().replace("qty", "quantity"), encoding="utf-8")
        (root / "new.py").write_text("z = 1\n", encoding="utf-8")
        (root / "gone.py").unlink()

    receipt = _turn(_engine(root, {"sed": sed}, []), _shell("c1", "sed"))

    assert sorted(Path(p).name for p in receipt["overwritten"]) == ["a.py", "b.py"]
    assert [Path(p).name for p in receipt["created"]] == ["new.py"]
    assert [Path(p).name for p in receipt["deleted"]] == ["gone.py"]
    stats = _stats(receipt)
    assert (stats["a.py"]["added"], stats["a.py"]["removed"]) == (2, 2)
    assert (stats["b.py"]["added"], stats["b.py"]["removed"]) == (1, 1)
    assert (stats["new.py"]["added"], stats["new.py"]["removed"]) == (1, 0)
    assert stats["gone.py"]["removed"] == 2
    # Read from git, not from a call: no per-call before-image, so nothing to undo.
    assert all(s["source"] == "shell" and not s["undoable"] for s in stats.values())
    assert "kept.py" not in stats


def test_a_file_already_dirty_is_counted_from_where_the_turn_found_it(tmp_path):
    root = _repo(tmp_path / "repo", {"a.py": "1\n2\n3\n", "mine.py": "m\n"})
    # The person's own unsaved work, before the turn: neither file is the agent's doing yet.
    (root / "a.py").write_text("1\n2\n3\n4\n", encoding="utf-8")
    (root / "mine.py").write_text("m\nmore\n", encoding="utf-8")

    def append() -> None:
        with (root / "a.py").open("a", encoding="utf-8") as handle:
            handle.write("5\n")

    receipt = _turn(_engine(root, {"append": append}, []), _shell("c1", "append"))

    stats = _stats(receipt)
    assert (stats["a.py"]["added"], stats["a.py"]["removed"]) == (1, 0)  # not +2 against HEAD
    assert "mine.py" not in stats


def test_a_checkout_that_threw_work_away_is_on_the_receipt(tmp_path):
    root = _repo(tmp_path / "repo", {"a.py": "base\n"})
    (root / "a.py").write_text("base\nthe person's work\n", encoding="utf-8")

    receipt = _turn(
        _engine(root, {"checkout": lambda: _git(root, "checkout", "--", "a.py")}, []),
        _shell("c1", "checkout"),
    )

    stats = _stats(receipt)
    assert (stats["a.py"]["added"], stats["a.py"]["removed"]) == (0, 1)


def test_a_commit_changes_no_file(tmp_path):
    """A turn that only committed showed "19 files changed": every committed file went from dirty
    to clean, which read as a checkout throwing work away (owner's screenshot, 2026-10-15). Its
    content on disk never moved."""
    root = _repo(tmp_path / "repo", {"a.py": "base\n", "b.py": "b\n"})
    (root / "a.py").write_text("base\nwork\n", encoding="utf-8")
    (root / "c.py").write_text("new\n", encoding="utf-8")

    def commit() -> None:
        _git(root, "add", "a.py", "c.py")
        _git(root, "commit", "-q", "-m", "work")

    receipt = _turn(_engine(root, {"commit": commit}, []), _shell("c1", "commit"))

    assert _stats(receipt) == {}
    assert not receipt.get("overwritten") and not receipt.get("created") and not receipt.get("deleted")


def test_a_file_named_by_a_write_tool_is_listed_once(tmp_path):
    root = _repo(tmp_path / "repo", {"a.py": "1\n"})

    def touch() -> None:
        with (root / "a.py").open("a", encoding="utf-8") as handle:
            handle.write("3\n")

    receipt = _turn(
        _engine(root, {"touch": touch}, []),
        ("c1", "write_file", {"path": "a.py", "content": "1\n2\n"}),
        _shell("c2", "touch"),
    )

    assert [Path(p).name for p in receipt["overwritten"]] == ["a.py"]
    [stat] = receipt["stats"]
    assert "source" not in stat
    assert (stat["added"], stat["removed"]) == (2, 0)


def test_outside_git_a_command_leaves_no_guess(tmp_path):
    root = tmp_path / "plain"
    root.mkdir()
    (root / "a.py").write_text("1\n", encoding="utf-8")

    def edit() -> None:
        (root / "a.py").write_text("2\n", encoding="utf-8")

    receipt = _turn(_engine(root, {"edit": edit}, []), _shell("c1", "edit"))

    assert "overwritten" not in receipt and "stats" not in receipt


def test_a_turn_with_no_command_never_asks_git(tmp_path, monkeypatch):
    root = _repo(tmp_path / "repo", {"a.py": "1\n"})
    asked: list[Any] = []
    real = worktree_state.take
    monkeypatch.setattr(worktree_state, "take", lambda r: asked.append(r) or real(r))

    receipt = _turn(_engine(root, {}, []), ("c1", "write_file", {"path": "a.py", "content": "2\n"}))

    assert [Path(p).name for p in receipt["overwritten"]] == ["a.py"]
    assert asked == []


def test_a_repository_larger_than_the_workspace_reports_paths_inside_it(tmp_path):
    repo = _repo(tmp_path / "repo", {"app/a.py": "1\n", "other/b.py": "1\n"})
    root = repo / "app"

    def edit() -> None:
        (root / "a.py").write_text("2\n", encoding="utf-8")
        (repo / "other" / "b.py").write_text("2\n", encoding="utf-8")

    receipt = _turn(_engine(root, {"edit": edit}, []), _shell("c1", "edit"))

    assert [Path(p) for p in receipt["overwritten"]] == [root / "a.py"]


def test_reeve_s_own_folder_is_not_on_the_receipt(tmp_path):
    # Live run, 2026-09-25: the engine keeps its intent journal in `<workspace>/.reeve/`,
    # untracked in the person's repo. A command turn would have listed those files as
    # created by the command.
    root = _repo(tmp_path / "repo", {"a.py": "1\n"})

    def edit() -> None:
        (root / "a.py").write_text("2\n", encoding="utf-8")
        (root / ".reeve" / "intents").mkdir(parents=True)
        (root / ".reeve" / "intents" / "c1.json").write_text("{}", encoding="utf-8")

    receipt = _turn(_engine(root, {"edit": edit}, []), _shell("c1", "edit"))

    assert [Path(p).name for p in receipt["overwritten"]] == ["a.py"]
    assert "created" not in receipt


# -- G5: the check that proves the change rides on the receipt -------------------------


def _edit(root: Path) -> Callable[[], None]:
    def edit() -> None:
        (root / "a.py").write_text("2\n", encoding="utf-8")

    return edit


def test_the_turn_s_check_is_on_the_receipt(tmp_path):
    # GUI rendering review, G5: "10 tests pass" in the answer is the model's word. The
    # engine already reads the check's own exit and summary; the receipt now carries them.
    root = _repo(tmp_path / "repo", {"a.py": "1\n"})
    commands = {"edit": _edit(root), "pytest -q": lambda: "10 passed in 0.12s\n[exit 0]"}

    receipt = _turn(_engine(root, commands, []), _shell("c1", "edit"), _shell("c2", "pytest -q"))

    assert receipt["check"] == {
        "command": "pytest -q",
        "ok": True,
        "summary": "10 passed in 0.12s",
        "edits_since": 0,
        "timed_out": False,
    }


def test_a_red_check_is_red_on_the_receipt(tmp_path):
    root = _repo(tmp_path / "repo", {"a.py": "1\n"})
    commands = {"edit": _edit(root), "pytest -q": lambda: "1 failed, 9 passed in 0.2s\n[exit 1]"}

    receipt = _turn(_engine(root, commands, []), _shell("c1", "edit"), _shell("c2", "pytest -q"))

    assert receipt["check"]["ok"] is False
    assert receipt["check"]["summary"] == "1 failed, 9 passed in 0.2s"


def test_a_check_from_an_earlier_turn_is_not_this_turn_s_proof(tmp_path):
    root = _repo(tmp_path / "repo", {"a.py": "1\n", "b.py": "1\n"})

    def edit_b() -> None:
        (root / "b.py").write_text("2\n", encoding="utf-8")

    commands = {"edit": _edit(root), "edit b": edit_b, "pytest -q": lambda: "3 passed\n[exit 0]"}
    engine = _engine(root, commands, [])
    assert _turn(engine, _shell("c1", "edit"), _shell("c2", "pytest -q"))["check"]["ok"] is True

    second = _turn(engine, _shell("c3", "edit b"))

    assert [Path(p).name for p in second["overwritten"]] == ["b.py"]
    assert "check" not in second


def test_caches_a_command_leaves_behind_are_not_changes_and_binaries_have_no_line_counts(tmp_path):
    # Live run, 2026-09-26: `pytest` in a repo with no .gitignore left three
    # `__pycache__/*.pyc` files, and the receipt listed them as created "via shell" with
    # line counts read from bytes.
    root = _repo(tmp_path / "repo", {"a.py": "1\n"})

    def run() -> None:
        (root / "a.py").write_text("2\n", encoding="utf-8")
        (root / "__pycache__").mkdir()
        (root / "__pycache__" / "a.cpython-311.pyc").write_bytes(b"\x00\x01\x02\n\x03")
        (root / ".pytest_cache" / "v").mkdir(parents=True)
        (root / ".pytest_cache" / "v" / "lastfailed").write_text("{}", encoding="utf-8")
        (root / "out.bin").write_bytes(b"\x89PNG\x00\x00\n\x00")

    receipt = _turn(_engine(root, {"run": run}, []), _shell("c1", "run"))

    assert [Path(p).name for p in receipt["created"]] == ["out.bin"]
    assert sorted(_stats(receipt)) == ["a.py"]  # out.bin is listed, without numbers


def test_the_agent_s_own_notes_are_named_apart_on_the_receipt(tmp_path):
    """A turn that wrote its memory files listed them as "files changed" beside the person's
    code (owner, 2026-10-13). The receipt names them apart so a surface can count the person's
    files alone."""
    root = _repo(tmp_path / "repo", {"a.py": "1\n"})
    memory = tmp_path / "state" / "memory"
    memory.mkdir(parents=True)
    engine = _engine(root, {}, [])
    engine.permissions.memory_dirs = [str(memory)]

    receipt = _turn(
        engine,
        ("c1", "write_file", {"path": "a.py", "content": "1\n2\n"}),
        ("c2", "write_file", {"path": str(memory / "PROGRESS.md"), "content": "- done\n"}),
    )

    assert [Path(p).name for p in receipt["notes"]] == ["PROGRESS.md"]
    named = {Path(p).name for p in [*receipt.get("created", []), *receipt.get("overwritten", [])]}
    assert named == {"a.py", "PROGRESS.md"}
