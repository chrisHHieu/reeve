"""`grep` and `list_files` say when they stopped short — whatever the model passed.

Found by the 2026-09-24 harness audit: `grep` announced its cap only when the MODEL had
passed `max_results` (the render compared against the argument), so the ordinary call —
default cap, 100 — cut silently. `list_files` (aisuite's) returned the first 100 paths in
`rglob` order, sorted them by name, and never said there were more; it also walked
`node_modules` and ignored `.gitignore`. Both let the model conclude "that is all of
them" — the call site it did not edit, the file it did not look at. a glob and
grep append a truncation line whenever the limit is reached.
"""

from __future__ import annotations

import os
import shutil
import time

import pytest

from harness.tools import search as search_mod
from harness.tools.files.renders import _listfiles_render
from harness.tools.listing import list_files_tools
from harness.tools.search import _grep_render, search_tools

needs_rg = pytest.mark.skipif(shutil.which("rg") is None, reason="ripgrep not installed")


def _grep(tmp_path):
    return search_tools(str(tmp_path))[0]


def _list(tmp_path):
    return list_files_tools(str(tmp_path))[0]


def _seed_matches(tmp_path, files: int, per_file: int) -> None:
    for i in range(files):
        (tmp_path / f"m{i:03d}.txt").write_text("needle\n" * per_file, encoding="utf-8")


# -- grep ------------------------------------------------------------------------


@needs_rg
def test_grep_default_cap_is_announced_without_max_results(tmp_path):
    _seed_matches(tmp_path, files=30, per_file=5)  # 150 matches, default cap 100
    value = _grep(tmp_path)(pattern="needle")
    assert value["count"] == 100 and value["truncated"] is True
    text = _grep_render({"pattern": "needle"}, value)
    assert "more matches exist" in text.splitlines()[0]


@needs_rg
def test_grep_exactly_at_the_cap_is_not_called_truncated(tmp_path):
    _seed_matches(tmp_path, files=1, per_file=5)
    value = _grep(tmp_path)(pattern="needle", max_results=5)
    assert value["count"] == 5 and value["truncated"] is False
    assert "more matches exist" not in _grep_render({"pattern": "needle", "max_results": 5}, value)


def test_grep_python_fallback_reports_truncation_too(tmp_path, monkeypatch):
    monkeypatch.setattr(search_mod.shutil, "which", lambda _name: None)
    _seed_matches(tmp_path, files=3, per_file=50)
    value = _grep(tmp_path)(pattern="needle")
    assert value["engine"] == "python"
    assert value["count"] == 100 and value["truncated"] is True
    exact = _grep(tmp_path)(pattern="needle", max_results=150)
    assert exact["count"] == 150 and exact["truncated"] is False


# -- list_files --------------------------------------------------------------------


def _seed_files(tmp_path, count: int) -> None:
    for i in range(count):
        (tmp_path / f"f{i:03d}.py").write_text("x\n", encoding="utf-8")


def test_list_files_default_cap_is_announced(tmp_path):
    _seed_files(tmp_path, 150)
    value = _list(tmp_path)()
    assert len(value["files"]) == 100
    assert value["total"] == 150 and value["truncated"] is True
    text = _listfiles_render({}, value)
    assert "showing 100 of 150" in text


def test_list_files_lists_newest_first(tmp_path):
    _seed_files(tmp_path, 5)
    newest = tmp_path / "f002.py"
    later = time.time() + 60
    os.utime(newest, (later, later))
    value = _list(tmp_path)()
    assert value["files"][0] == "f002.py"
    assert value["truncated"] is False
    assert "showing" not in _listfiles_render({}, value)


@needs_rg
def test_list_files_skips_gitignored_and_dependency_dirs(tmp_path):
    (tmp_path / ".gitignore").write_text("generated/\n", encoding="utf-8")
    (tmp_path / "generated").mkdir()
    (tmp_path / "generated" / "out.py").write_text("x\n", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "dep.js").write_text("x\n", encoding="utf-8")
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github" / "ci.yml").write_text("x\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("x\n", encoding="utf-8")
    files = set(_list(tmp_path)()["files"])
    assert os.path.join("src", "app.py") in files
    # A hidden CONFIG directory is still listed; ignored and dependency dirs are not.
    assert os.path.join(".github", "ci.yml") in files
    assert not any(f.startswith(("generated", "node_modules")) for f in files)


def test_list_files_pattern_recursion_and_errors(tmp_path):
    (tmp_path / "a.py").write_text("x\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("x\n", encoding="utf-8")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "c.py").write_text("x\n", encoding="utf-8")
    tool = _list(tmp_path)
    assert set(tool(pattern="*.py")["files"]) == {"a.py", os.path.join("pkg", "c.py")}
    assert set(tool(recursive=False)["files"]) == {"a.py", "b.txt"}
    assert tool(path="pkg")["files"] == [os.path.join("pkg", "c.py")]
    assert tool(path="missing") == {"error": "folder does not exist: missing"}
    # A guessed folder name gets the siblings it was probably meant to be.
    assert tool(path="pkgs") == {"error": "folder does not exist: pkgs — did you mean: pkg"}
    assert "nearest existing" in tool(path="nope/deeper")["error"]
    assert "outside the session's folders" in tool(path="..")["error"]


def test_remote_list_files_asks_one_past_the_cap_and_says_so():
    from harness.testing.fake_ssh import FakeRemoteHost, FakeSsh
    from harness.orchestration.worlds import remote_world

    workspace = "/home/dev/project"
    host = FakeRemoteHost(directories={workspace})
    for i in range(4):
        host.write(f"{workspace}/f{i}.py", "x\n")
    world = remote_world(
        {"id": "p", "name": "box", "host": "box.example", "user": "dev"},
        workspace,
        runner=FakeSsh(host=host),
        session_id="s",
        verify=False,
    )
    tool = list_files_tools(workspace, backend=world.read_backend(None))[0]
    capped = tool(max_results=3)
    assert len(capped["files"]) == 3 and capped["truncated"] is True
    whole = tool(max_results=4)
    assert len(whole["files"]) == 4 and whole["truncated"] is False


@pytest.mark.parametrize("engine", ["ripgrep", "python"])
def test_list_files_folder_pattern_reads_from_the_workspace_or_from_the_path(
    engine, tmp_path, monkeypatch
):
    # Same rule as grep's glob (tests/test_grep_modes.py): `pattern="harness/context/memory/*.py"`
    # found nothing in an eval run, and after listing FROM the path a workspace-relative
    # pattern with `path` set found nothing either; the walk matched folder patterns
    # against the bare file name.
    from harness.tools import listing as listing_mod

    if engine == "ripgrep" and shutil.which("rg") is None:
        pytest.skip("ripgrep not installed")
    if engine == "python":
        monkeypatch.setattr(listing_mod.shutil, "which", lambda _name: None)
    (tmp_path / "pkg" / "sub").mkdir(parents=True)
    (tmp_path / "pkg" / "sub" / "m.py").write_text("x\n", encoding="utf-8")
    (tmp_path / "pkg" / "n.txt").write_text("x\n", encoding="utf-8")
    elsewhere = tmp_path.parent / (tmp_path.name + "-cwd")
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    list_files = list_files_tools(str(tmp_path))[0]
    for args in (
        {"pattern": "pkg/**/*.py"},
        {"pattern": "pkg/**/*.py", "path": "pkg/sub"},
        {"pattern": "sub/*.py", "path": "pkg"},
    ):
        found = list_files(**args)
        assert [f.replace("\\", "/") for f in found["files"]] == ["pkg/sub/m.py"], args
