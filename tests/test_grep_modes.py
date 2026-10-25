"""`grep` output modes and context lines — the cheap first move for exploration.

Search defaults to listing files, not lines; locating WHERE something lives
costs a path per file instead of up to 100 matched lines. Reeve's grep could only return
lines and had no context option, so "show me the surrounding code" was always a second
`read_file`. Pinned on both engines (ripgrep and the Python fallback):

- `files_with_matches`: paths only, newest first, capped with `truncated`;
- `count`: per-file counts and the total;
- `content` + `context`: the surrounding lines, marked apart from the matches;
- the default (no `output_mode`) is unchanged: matched lines.
"""

from __future__ import annotations

import os
import shutil
import time

import pytest

from harness.tools import search as search_mod
from harness.tools.search import _grep_render, search_tools

ENGINES = ["ripgrep", "python"]


@pytest.fixture(params=ENGINES)
def grep(request, tmp_path, monkeypatch):
    if request.param == "ripgrep" and shutil.which("rg") is None:
        pytest.skip("ripgrep not installed")
    if request.param == "python":
        monkeypatch.setattr(search_mod.shutil, "which", lambda _name: None)
    (tmp_path / "a.py").write_text("one\nneedle here\nthree\nfour\nneedle again\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("needle\n", encoding="utf-8")
    (tmp_path / "c.py").write_text("nothing\n", encoding="utf-8")
    later = time.time() + 60
    os.utime(tmp_path / "b.py", (later, later))
    tool = search_tools(str(tmp_path))[0]
    return tool


def test_files_with_matches_lists_paths_newest_first(grep):
    value = grep(pattern="needle", output_mode="files_with_matches")
    assert value["files"] == ["b.py", "a.py"]
    assert value["truncated"] is False
    text = _grep_render({"pattern": "needle", "output_mode": "files_with_matches"}, value)
    assert text.splitlines()[0] == "Found 2 files"
    assert "needle here" not in text  # paths only


def test_files_with_matches_is_capped_and_says_so(grep):
    value = grep(pattern="needle", output_mode="files_with_matches", max_results=1)
    assert len(value["files"]) == 1 and value["truncated"] is True
    assert "more" in _grep_render(
        {"pattern": "needle", "output_mode": "files_with_matches", "max_results": 1}, value
    )


def test_count_reports_per_file_and_total(grep):
    value = grep(pattern="needle", output_mode="count")
    counts = {row["file"]: row["count"] for row in value["counts"]}
    assert counts == {"a.py": 2, "b.py": 1}
    assert value["count"] == 3
    text = _grep_render({"pattern": "needle", "output_mode": "count"}, value)
    assert text.splitlines()[0] == "Found 3 matches in 2 files"
    assert "a.py: 2" in text


def test_context_lines_surround_each_match_and_are_marked(grep):
    value = grep(pattern="needle here", context=1)
    text = _grep_render({"pattern": "needle here", "context": 1}, value)
    assert value["count"] == 1  # context lines are not matches
    assert "  1- one" in text and "  2: needle here" in text and "  3- three" in text
    assert "four" not in text


def test_the_default_mode_is_unchanged(grep):
    value = grep(pattern="needle")
    assert value["count"] == 3
    assert {m["file"] for m in value["matches"]} == {"a.py", "b.py"}
    assert not any(m.get("context") for m in value["matches"])


def test_dotfiles_are_searched_and_the_git_dir_is_not(grep, tmp_path):
    # 2026-09-25 audit, W4: ripgrep skipped dotfiles, so `grep pytest` missed the CI
    # config that `list_files` had just listed. Both engines must agree.
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "ci.yml").write_text("run: pytest -q\n", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("pytest-in-git\n", encoding="utf-8")
    found = grep(pattern="pytest", output_mode="files_with_matches")
    files = [f.replace("\\", "/") for f in found["files"]]
    assert files == [".github/workflows/ci.yml"]


def test_a_glob_with_a_folder_matches_wherever_the_server_runs(tmp_path, monkeypatch):
    # 2026-09-25: ripgrep matched `tests/*.py` against the path it printed, relative only
    # under the process's own cwd — so any workspace that was not the server's cwd got
    # zero matches (65 greps in one 80-step eval run, most of them empty).
    if shutil.which("rg") is None:
        pytest.skip("ripgrep not installed")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text("auto_memory = 1\n", encoding="utf-8")
    (tmp_path / "pkg" / "sub").mkdir(parents=True)
    (tmp_path / "pkg" / "sub" / "m.py").write_text("auto_memory = 2\n", encoding="utf-8")
    elsewhere = tmp_path.parent / (tmp_path.name + "-cwd")
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    grep = search_tools(str(tmp_path))[0]
    for glob, want in (("tests/*.py", "tests/test_a.py"), ("pkg/**/*.py", "pkg/sub/m.py")):
        found = grep(pattern="auto_memory", glob=glob, output_mode="files_with_matches")
        assert [f.replace("\\", "/") for f in found["files"]] == [want], glob
        lines = grep(pattern="auto_memory", glob=glob)
        assert [m["file"].replace("\\", "/") for m in lines["matches"]] == [want], glob


@pytest.mark.parametrize("engine", ENGINES)
def test_a_folder_glob_reads_from_the_workspace_or_from_the_path(engine, tmp_path, monkeypatch):
    # The same eval run, after the fix above: `glob="harness/**/*.py", path="harness/context/memory"`
    # came back empty — ripgrep now ran FROM the path, so a workspace-relative glob no longer
    # matched. Models write both spellings, and the Python fallback matched a folder glob
    # against the bare file name, so it found nothing for either.
    if engine == "ripgrep" and shutil.which("rg") is None:
        pytest.skip("ripgrep not installed")
    if engine == "python":
        monkeypatch.setattr(search_mod.shutil, "which", lambda _name: None)
    (tmp_path / "pkg" / "sub").mkdir(parents=True)
    (tmp_path / "pkg" / "sub" / "m.py").write_text("auto_memory = 2\n", encoding="utf-8")
    (tmp_path / "pkg" / "n.txt").write_text("auto_memory = 3\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text("auto_memory = 1\n", encoding="utf-8")
    elsewhere = tmp_path.parent / (tmp_path.name + "-cwd")
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    grep = search_tools(str(tmp_path))[0]
    cases = [
        ({"glob": "pkg/**/*.py", "path": "pkg/sub"}, ["pkg/sub/m.py"]),  # workspace-relative
        ({"glob": "sub/*.py", "path": "pkg"}, ["pkg/sub/m.py"]),  # relative to the path
        ({"glob": "tests/*.py"}, ["tests/test_a.py"]),
        ({"glob": "*.py", "path": "pkg"}, ["pkg/sub/m.py"]),  # a bare name glob, as ever
    ]
    for args, want in cases:
        found = grep(pattern="auto_memory", output_mode="files_with_matches", **args)
        assert sorted(f.replace("\\", "/") for f in found["files"]) == want, args
