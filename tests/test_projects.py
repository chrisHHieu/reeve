"""What project a folder is (`session/projects`): its key (a repository and all its worktrees are
one), its label, the names people give projects, and the key a session's memory is kept under.
"""

import sqlite3
import subprocess
import threading
from pathlib import Path

import pytest

from harness.context.memory.auto_memory import AutoMemory
from harness.session.projects import ProjectNames, project_key, project_label, project_presence, resolve_memory_key


def _git(folder: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=folder, check=True, capture_output=True)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """A repository with one commit."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    (root / "a.txt").write_text("x")
    _git(root, "add", "a.txt")
    _git(root, "commit", "-qm", "init")
    return root


def _names(tmp_path) -> ProjectNames:
    connection = sqlite3.connect(tmp_path / "t.db", check_same_thread=False)
    connection.row_factory = sqlite3.Row
    return ProjectNames(connection, threading.RLock())


# -- the key -------------------------------------------------------------------------------------


def test_a_plain_folder_is_its_own_path_whether_or_not_it_exists(tmp_path):
    there = tmp_path / "plain"
    there.mkdir()
    for folder in (there, tmp_path / "nope"):
        assert project_key(folder) == str(folder.resolve())


def test_anywhere_in_a_repository_or_its_worktree_is_the_repository(repo, tmp_path):
    deep = repo / "src" / "deep"
    deep.mkdir(parents=True)
    worktree = tmp_path / "wt"
    _git(repo, "worktree", "add", "-q", str(worktree))
    for folder in (repo, deep, worktree):
        assert project_key(folder) == str(repo.resolve())


def test_the_label_says_git_or_folder_and_shortens_home(repo, tmp_path):
    git = project_label(str(repo.resolve()))
    assert (git["kind"], git["label"]) == ("git", "repo")
    plain = tmp_path / "plain"
    plain.mkdir()
    assert project_label(str(plain.resolve()))["kind"] == "folder"
    assert project_label(str(tmp_path / "x" / "y"), home=str(tmp_path))["label"] == "~/x/y"


# -- names ---------------------------------------------------------------------------------------


def test_a_name_points_at_a_key_and_naming_again_moves_it(tmp_path):
    names = _names(tmp_path)
    names.name_current("memory", "reeve", "/k1")
    assert names.resolve("memory", "reeve") == "/k1"
    names.name_current("memory", "ops", "/k1")
    names.name_current("memory", "ops", "/k2")
    assert names.resolve("memory", "ops") == "/k2"


def test_names_are_listed_most_recently_used_first_up_to_the_limit(tmp_path):
    names = _names(tmp_path)
    for n, name in enumerate(["a", "b", "c"]):
        names.name_current("memory", name, f"/k{n}")
    names._conn.execute("UPDATE project_names SET last_used_at = datetime('now', '-1 hour') WHERE name != 'a'")
    assert [row["name"] for row in names.list("memory", limit=2)] == ["a", "b"]


@pytest.mark.parametrize(("kind", "name"), [("memory", "  "), ("nope", "x"), ("board", "x")])
def test_a_blank_name_or_any_kind_but_memory_is_refused(tmp_path, kind, name):
    # boards went with teams (2026-10-12): memory is the one kind a name can point at
    with pytest.raises(ValueError):
        _names(tmp_path).name_current(kind, name, "/k")


def test_a_forgotten_name_points_nowhere(tmp_path):
    names = _names(tmp_path)
    names.name_current("memory", "x", "/k")
    assert names.forget("memory", "x") is True and names.resolve("memory", "x") is None


# -- where memory is kept ------------------------------------------------------------------------


def test_a_binding_wins_over_the_folder_and_a_subfolder_is_its_repository(repo, tmp_path):
    names = _names(tmp_path)
    names.name_current("memory", "reeve", "/the/real/project")
    assert resolve_memory_key(str(tmp_path), binding="reeve", names=names) == "/the/real/project"
    (repo / "src").mkdir()
    assert resolve_memory_key(str(repo / "src")) == str(repo.resolve())


def test_presence_counts_what_the_project_has(tmp_path):
    memory = AutoMemory(tmp_path / "mem")
    memory.save_memory(title="F", description="f", note_type="project", body="f")
    assert project_presence("/p", memory=memory) == {"memories": 1}
