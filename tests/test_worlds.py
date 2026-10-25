"""The execution world seam (worlds.py, D5): ONE object per session owns the fs tools,
the shell executor and the terminals, and the catalog composes whatever world it is
given — so a remote/container backend slots in by implementing the Protocol, with no
tool or catalog edit.

LocalWorld is delegation, not rewrite: it composes the implementations that have always
existed (aisuite's version-guarded file toolkit, LocalExecutor, TerminalSessions), so
behavior is byte-identical. These tests pin three things: the delegation is faithful,
the one-world invariant holds (executor/terminals come from the same object), and a
fake world swapped in at the catalog runs the write path WITHOUT touching the disk —
the property that makes a second backend possible at all.
"""

from __future__ import annotations

from pathlib import Path

import aisuite as ai
from aisuite.agents import ToolMetadata, tool

from harness.modes.agents.base import AgentContext
from harness.modes.catalog import expand
from harness.tools.shell import Executor
from harness.orchestration.worlds import LocalWorld, World

_REPLACED = {"search_files", "read_file", "read_file_lines"}


def _names(tools) -> set[str]:
    return {getattr(t, "__name__", "?") for t in tools}


# -- delegation is faithful ---------------------------------------------------------


def test_fs_tools_delegate_unchanged(tmp_path):
    """LocalWorld returns exactly what the catalog used to inline: the toolkit over the
    same root, the same kwargs — the FULL set, unfiltered (the replaced-name filter is
    the catalog's composition concern, and it keeps doing it)."""
    world = LocalWorld(root=tmp_path)
    direct = ai.toolkits.files(root=str(tmp_path), allow_write=True)
    assert _names(world.fs_tools()) == _names(direct)


def test_fs_tools_pass_roots_through(tmp_path):
    rw = tmp_path / "rw"
    rw.mkdir()
    world = LocalWorld(root=tmp_path)
    via_world = _names(world.fs_tools(roots=[{"path": rw, "writable": True}]))
    direct = _names(ai.toolkits.files(roots=[{"path": rw, "writable": True}]))
    assert via_world == direct
    assert "write_file" in via_world


def test_local_world_is_a_world(tmp_path):
    assert isinstance(LocalWorld(root=tmp_path), World)


# -- the one-world invariant --------------------------------------------------------


class _FakeExecutor(Executor):
    def run(self, command: str, timeout=None) -> dict:
        return {"output": ""}


def test_injected_executor_and_terminals_are_kept(tmp_path):
    """Whatever the builder hands the world is what the world answers with — the shell
    and the terminals cannot silently become two different worlds."""
    executor = _FakeExecutor()

    class _Terminals:
        pass

    terminals = _Terminals()
    world = LocalWorld(root=tmp_path, executor=executor, terminals=terminals)
    assert world.executor is executor
    assert world.terminals is terminals


def test_lazily_built_executor_runs_in_the_worlds_root(tmp_path):
    world = LocalWorld(root=tmp_path)
    executor = world.executor
    assert Path(executor.cwd) == tmp_path.resolve()
    executor.close()  # a real LocalExecutor: it spawned a shell to hold


# -- the swap: a second world runs the write path without the disk ------------------


class FakeWorld:
    """A second execution world: files live in a dict, never on disk. Deliberately NOT
    a subclass of LocalWorld — the Protocol is the only contract."""

    def __init__(self):
        self.root = Path("/virtual")
        self.files: dict[str, str] = {}

    def fs_tools(self, roots=None) -> list:
        def write_file(path: str, content: str, overwrite: bool = True) -> str:
            if path in self.files and not overwrite:
                raise FileExistsError(path)
            self.files[path] = content
            return path

        def list_files(path: str = "", pattern: str = "", recursive: bool = True,
                       max_results: int = 100) -> list[str]:
            return sorted(self.files)

        return [
            tool(
                write_file,
                metadata=ToolMetadata(
                    category="filesystem", risk_level="medium", requires_approval=True
                ),
            ),
            tool(
                list_files,
                metadata=ToolMetadata(
                    category="filesystem", risk_level="low", requires_approval=False
                ),
            ),
        ]

    executor = None
    terminals = None


def test_a_swapped_world_runs_the_write_path_off_disk(tmp_path):
    """The D5 proof in one frame: the SAME catalog composition, a different world. The
    write_file the capability hands out writes into the fake's memory and the workspace
    directory stays untouched."""
    world = FakeWorld()
    context = AgentContext(workspace=tmp_path, world=world)
    tools = expand(["files"], context)

    write = next(t for t in tools if getattr(t, "__name__", "") == "write_file")
    assert write(path="report.md", content="# hello") == "report.md"
    assert world.files["report.md"] == "# hello"
    assert list(tmp_path.iterdir()) == [], "the real disk was touched"

    # The non-world tools of the same capability still anchor at the workspace: the
    # swap replaced the world, not the composition.
    names = _names(tools)
    assert {"write_file", "list_files", "read_file"} <= names and "read_image" not in names
