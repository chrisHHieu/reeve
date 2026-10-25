"""The freshness guard: a branch behind its upstream must fail LOUDLY, not silently.

Run against throwaway repositories (a bare "origin" plus two clones), so the test never
touches this checkout's own remotes.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from scripts.check_workspace_freshness import main


def _git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={
            **__import__("os").environ,
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.com",
        },
    )
    assert proc.returncode == 0, f"git {' '.join(args)}: {proc.stderr}"
    return (proc.stdout or "").strip()


@pytest.fixture
def clone(tmp_path, monkeypatch):
    """(work_clone, other_clone) sharing a bare origin; main is checked out in both."""
    origin = tmp_path / "origin.git"
    origin.mkdir()
    _git(origin, "init", "--bare", "--initial-branch=main")
    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init", "--initial-branch=main")
    (seed / "a.txt").write_text("one\n", encoding="utf-8")
    _git(seed, "add", ".")
    _git(seed, "commit", "-m", "first")
    _git(seed, "remote", "add", "origin", str(origin))
    _git(seed, "push", "-u", "origin", "main")

    work = tmp_path / "work"
    _git(tmp_path, "clone", str(origin), str(work))
    other = tmp_path / "other"
    _git(tmp_path, "clone", str(origin), str(other))
    monkeypatch.setattr("scripts.check_workspace_freshness.REPO", work)
    return work, other


def test_a_stale_checkout_fails(clone, capsys):
    work, other = clone
    (other / "b.txt").write_text("two\n", encoding="utf-8")
    _git(other, "add", ".")
    _git(other, "commit", "-m", "second")
    _git(other, "push")

    # Without fetching, the local clone cannot know yet: the check must not claim "fine".
    assert main(["--no-fetch"]) == 0

    _git(work, "fetch", "--prune")
    assert main(["--no-fetch"]) == 1
    assert "STALE" in capsys.readouterr().err


def test_a_fresh_checkout_passes(clone, capsys):
    work, _other = clone
    assert main([]) == 0
    assert "behind origin/main" in capsys.readouterr().out


def test_a_busy_branch_behind_main_warns_but_passes(clone, capsys):
    work, other = clone
    _git(work, "checkout", "-b", "feature")
    (work / "work.txt").write_text("mine\n", encoding="utf-8")
    _git(work, "add", ".")
    _git(work, "commit", "-m", "my work")
    _git(work, "push", "-u", "origin", "feature")
    for index in range(3):
        (other / f"c{index}.txt").write_text("x\n", encoding="utf-8")
        _git(other, "add", ".")
        _git(other, "commit", "-m", f"main {index}")
        _git(other, "push")

    # Its own upstream is current, and main is behind the limit -> warning, not an error:
    # a long-lived branch with work of its own is a legitimate state.
    assert main(["--max-behind-main", "2"]) == 0
    assert "warning" in capsys.readouterr().out
    assert main(["--max-behind-main", "1"]) == 0
    assert "warning" in capsys.readouterr().out


def test_a_branch_with_nothing_of_its_own_far_behind_main_fails(clone, capsys):
    work, other = clone
    _git(work, "checkout", "-b", "empty-branch", "origin/main")
    _git(work, "push", "-u", "origin", "empty-branch")
    for index in range(3):
        (other / f"m{index}.txt").write_text("x\n", encoding="utf-8")
        _git(other, "add", ".")
        _git(other, "commit", "-m", f"main {index}")
        _git(other, "push")
    _git(work, "fetch", "--prune")

    assert main(["--max-behind-main", "1"]) == 1
    assert "STALE" in capsys.readouterr().err


def test_a_repo_without_an_upstream_cannot_answer(tmp_path, monkeypatch):
    lonely = tmp_path / "lonely"
    lonely.mkdir()
    _git(lonely, "init", "--initial-branch=main")
    monkeypatch.setattr("scripts.check_workspace_freshness.REPO", lonely)
    assert main(["--no-fetch"]) == 2


def test_the_script_runs_as_a_module(clone):
    """The documented command is `python scripts/check_workspace_freshness.py`, so the
    module entry point has to work, not just the imported function."""
    repo = Path(__file__).resolve().parents[1]
    proc = subprocess.run(
        [sys.executable, "-m", "scripts.check_workspace_freshness", "--no-fetch"],
        cwd=str(repo),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.returncode in (0, 1, 2)
    assert "ahead" in proc.stdout or "upstream" in proc.stderr
