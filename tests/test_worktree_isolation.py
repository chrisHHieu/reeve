"""Per-child worktree isolation.

Opt-in, local, git-only: each `task` child gets its own worktree so parallel coding
children cannot fight over the same files — and a finished child's worktree is removed
ONLY on proof (clean tree, zero commits since its base). Pinned here: creation, the
fallbacks (default off, non-repo), the release proof (clean removed; dirty kept;
committed kept), and the manager path end to end.
"""

from __future__ import annotations

from pathlib import Path

from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient
from harness.safety.secrets import state_dir
from harness.interface.server.manager import SessionManager
from harness.session.sessions import SessionRecord
from harness.platform.spawn import run as spawn_run
from harness.orchestration.worktrees import create_child_worktree, remove_child_worktree, repo_root


class Provider(ProviderClient):
    def complete(self, *, model, messages, tools=None, **settings):
        return AssistantTurn(text="ok", finish_reason="stop")

    def capabilities(self, model):
        return ModelCapabilities()


def _git(repo: Path, *args: str) -> str:
    out = spawn_run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    assert out.returncode == 0, out.stderr or out.stdout
    return out.stdout


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "app.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "init")
    return repo


def _enable_isolation() -> None:
    path = state_dir() / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[delegation]\nworktree_isolation = true\n", encoding="utf-8")


def _parent(mgr: SessionManager, workspace: Path, sid: str) -> SessionRecord:
    record = SessionRecord(
        session_id=sid,
        workspace=str(workspace),
        model="gpt-5.5",
        mode="interactive",
        messages=[{"role": "user", "content": "the parent question"}],
        title="a parent",
        agent="reeve",
    )
    mgr.session_store.save(record)
    return record


# -- the worktree itself ----------------------------------------------------------------


def test_create_and_release_a_clean_child_worktree(tmp_path):
    repo = _repo(tmp_path)
    info = create_child_worktree(repo, "abc123")
    assert "error" not in info, info
    path = Path(info["path"])
    assert path.is_dir() and (path / "app.py").exists()
    assert info["branch"] == "reeve-sub/abc123"

    assert remove_child_worktree(info)["removed"] is True
    assert not path.exists()
    assert "reeve-sub/abc123" not in _git(repo, "branch", "--list")


def test_a_dirty_worktree_is_kept(tmp_path):
    repo = _repo(tmp_path)
    info = create_child_worktree(repo, "dirty1")
    Path(info["path"], "app.py").write_text("x = 2\n", encoding="utf-8")

    result = remove_child_worktree(info)

    assert result == {"removed": False, "reason": "worktree has uncommitted changes"}
    assert Path(info["path"]).is_dir()


def test_a_worktree_with_commits_is_kept(tmp_path):
    repo = _repo(tmp_path)
    info = create_child_worktree(repo, "commit1")
    worktree = Path(info["path"])
    (worktree / "new.py").write_text("y = 1\n", encoding="utf-8")
    _git(worktree, "add", ".")
    _git(worktree, "commit", "-m", "the child's work")

    result = remove_child_worktree(info)

    assert result["removed"] is False and "commits" in result["reason"]
    assert worktree.is_dir()


def test_a_non_repo_and_an_empty_record_answer_instead_of_raising(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert repo_root(plain) is None
    assert "error" in create_child_worktree(plain, "x")
    assert remove_child_worktree({"path": "", "base_head": ""})["removed"] is False


# -- the manager path --------------------------------------------------------------------


def test_the_child_works_in_its_worktree_and_it_is_released_on_idle(tmp_path):
    repo = _repo(tmp_path)
    _enable_isolation()
    mgr = SessionManager(workspace=repo, provider=Provider())
    parent = _parent(mgr, repo, "parent-iso")

    child_id = mgr.create_child_session(parent.session_id, "isolate me", "general", "gpt-5.5")

    child = mgr.session_store.load(child_id)
    assert child is not None
    worktree = Path(child.workspace)
    assert worktree != repo and worktree.is_dir()
    assert child_id in str(worktree)
    assert child_id in mgr._child_worktrees

    mgr.mark_idle(child_id)

    assert not worktree.exists()
    assert child_id not in mgr._child_worktrees


def test_the_default_and_a_non_repo_keep_the_parent_workspace(tmp_path):
    repo = _repo(tmp_path)
    off = SessionManager(workspace=repo, provider=Provider())
    parent = _parent(off, repo, "parent-off")
    child_id = off.create_child_session(parent.session_id, "d", "general", "m")
    assert off.session_store.load(child_id).workspace == str(repo)

    plain = tmp_path / "plain"
    plain.mkdir()
    _enable_isolation()
    on = SessionManager(workspace=plain, provider=Provider())
    parent2 = _parent(on, plain, "parent-plain")
    child2 = on.create_child_session(parent2.session_id, "d", "general", "m")
    assert on.session_store.load(child2).workspace == str(plain)


def test_a_kept_worktree_is_named_to_the_parent(tmp_path):
    """Orchestration audit O4: a worktree holding work was kept with only a log line, so the
    parent could not tell where the child's changes were."""
    import asyncio

    from harness.llm.providers import ToolCall

    class Writes(ProviderClient):
        def __init__(self):
            self.turns = [
                AssistantTurn(
                    tool_calls=[ToolCall(id="w1", name="write_file", arguments={"path": "new.py", "content": "y = 2\n"})],
                    finish_reason="tool_calls",
                ),
                AssistantTurn(text="wrote new.py", finish_reason="stop"),
            ]

        def complete(self, *, model, messages, tools=None, **settings):
            return self.turns.pop(0) if self.turns else AssistantTurn(text="ok", finish_reason="stop")

        def capabilities(self, model):
            return ModelCapabilities()

    repo = _repo(tmp_path)
    _enable_isolation()
    mgr = SessionManager(workspace=repo, provider=Writes())
    parent = _parent(mgr, repo, "parent-kept")

    out = asyncio.run(
        mgr.spawn_subagent(
            parent_id=parent.session_id, description="isolate me", prompt="write", agent_name="general"
        )
    )

    assert out["status"] == "completed"
    worktree = out["worktree"]
    assert Path(worktree["path"]).is_dir() and (Path(worktree["path"]) / "new.py").exists()
    assert worktree["branch"]
    assert [f["change"] for f in out["changed_files"]] == ["created"]
