"""A child's own worktree, asked for per delegation and handed back by a person.

On a temporary git repository, never the product's own. Pinned: the three verbs
(what the copy holds, apply all of it or none, discard), the copy found again after a
restart, isolation asked for on one call / by one agent definition while the global
switch stays off, the caller told when a copy it asked for could not be set up, and the
parent's model told what the person decided.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient
from harness.interface.server.manager import SessionManager
from harness.interface.server.manager.child_worktrees import handback_context
from harness.session.sessions import SessionRecord
from harness.platform.spawn import run as spawn_run
from harness.tools.task import changes_of, task_tools
from harness.orchestration.worktree_handback import apply_child_worktree, discard_child_worktree, find_child_worktree, worktree_changes
from harness.orchestration.worktrees import create_child_worktree


class Provider(ProviderClient):
    def complete(self, *, model, messages, tools=None, **settings):
        return AssistantTurn(text="ok", finish_reason="stop")

    def capabilities(self, model):
        return ModelCapabilities()


def _git(repo: Path, *args: str) -> str:
    out = spawn_run(
        ["git", "-C", str(repo), *args], capture_output=True, encoding="utf-8", errors="replace", timeout=60
    )
    assert out.returncode == 0, out.stderr or out.stdout
    return out.stdout


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "app.py").write_text("x = 1\ny = 2\n", encoding="utf-8")
    (repo / "old.py").write_text("gone = True\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "init")
    return repo


def _work(info: dict) -> Path:
    """What a child did in its copy: an edit, a new file (untracked), a deletion."""
    copy = Path(info["path"])
    (copy / "app.py").write_text("x = 1\ny = 3\nz = 4\n", encoding="utf-8")
    (copy / "new.py").write_text("fresh = 1\n", encoding="utf-8")
    (copy / "old.py").unlink()
    return copy


# -- the three verbs ------------------------------------------------------------------------


def test_the_copy_is_read_as_the_files_it_changed_tracked_or_new(tmp_path):
    repo = _repo(tmp_path)
    info = create_child_worktree(repo, "c1")
    _work(info)
    files = {f["path"]: f for f in worktree_changes(info)["files"]}
    assert files == {
        "app.py": {"path": "app.py", "change": "modified", "added": 2, "removed": 1},
        "new.py": {"path": "new.py", "change": "created", "added": 1, "removed": 0},
        "old.py": {"path": "old.py", "change": "deleted", "added": 0, "removed": 1},
    }


def test_apply_puts_all_of_it_in_the_parents_folder_and_removes_the_copy(tmp_path):
    repo = _repo(tmp_path)
    info = create_child_worktree(repo, "c2")
    copy = _work(info)
    # Part of the work was committed in the copy: it comes over all the same.
    _git(copy, "add", "new.py")
    _git(copy, "commit", "-m", "the child's commit")

    result = apply_child_worktree(info)

    assert result["applied"] is True and len(result["files"]) == 3
    assert (repo / "app.py").read_text(encoding="utf-8") == "x = 1\ny = 3\nz = 4\n"
    assert (repo / "new.py").read_text(encoding="utf-8") == "fresh = 1\n"
    assert not (repo / "old.py").exists()
    # Uncommitted in the parent — where Review shows it — and nothing of the copy is left.
    assert _git(repo, "log", "--oneline").count("\n") == 1
    assert not copy.exists() and "reeve-sub/c2" not in _git(repo, "branch", "--list")


def test_work_that_no_longer_fits_is_refused_whole_and_the_copy_is_kept(tmp_path):
    repo = _repo(tmp_path)
    info = create_child_worktree(repo, "c3")
    copy = _work(info)
    # The parent's folder moved on under the same lines.
    (repo / "app.py").write_text("x = 1\ny = 99\n", encoding="utf-8")

    result = apply_child_worktree(info)

    assert result["applied"] is False and "do not fit" in result["reason"]
    # None of it: the file the patch COULD have created is not there either.
    assert (repo / "app.py").read_text(encoding="utf-8") == "x = 1\ny = 99\n"
    assert not (repo / "new.py").exists() and (repo / "old.py").exists()
    assert copy.is_dir() and (copy / "new.py").exists()


def test_discard_removes_the_copy_and_its_work(tmp_path):
    repo = _repo(tmp_path)
    info = create_child_worktree(repo, "c4")
    copy = _work(info)
    assert discard_child_worktree(info) == {"removed": True}
    assert not copy.exists() and "reeve-sub/c4" not in _git(repo, "branch", "--list")
    assert (repo / "app.py").read_text(encoding="utf-8") == "x = 1\ny = 2\n"


def test_an_empty_copy_has_nothing_to_apply_and_bad_records_answer(tmp_path):
    repo = _repo(tmp_path)
    info = create_child_worktree(repo, "c5")
    assert worktree_changes(info) == {"files": []}
    assert apply_child_worktree(info) == {"applied": False, "reason": "the copy holds no changes"}
    assert discard_child_worktree({}) == {"removed": False, "reason": "no worktree recorded"}
    assert "error" in worktree_changes({"path": str(tmp_path / "nowhere"), "base_head": "abc"})


def test_the_copy_is_found_again_from_disk_with_the_commit_it_started_at(tmp_path):
    repo = _repo(tmp_path)
    info = create_child_worktree(repo, "c6")
    _work(info)
    # The parent commits something else meanwhile: the base is still where the copy began.
    (repo / "other.py").write_text("o = 1\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "later")
    found = find_child_worktree(repo, "c6")
    assert found == {**info, "path": str(Path(info["path"]))}
    assert find_child_worktree(repo, "nobody") is None
    assert find_child_worktree(tmp_path, "c6") is None  # not a repository


# -- asked for per delegation ----------------------------------------------------------------


def _manager(workspace: Path) -> SessionManager:
    mgr = SessionManager(workspace=workspace, provider=Provider())
    mgr.session_store.save(
        SessionRecord(
            session_id="parent", workspace=str(workspace), model="gpt-5.5", mode="interactive",
            messages=[], title="a parent", agent="reeve",
        )
    )
    return mgr


def test_one_call_can_ask_for_a_copy_while_the_global_switch_is_off(tmp_path):
    repo = _repo(tmp_path)
    mgr = _manager(repo)
    plain = mgr.create_child_session("parent", "shared", "general", "gpt-5.5")
    asked = mgr.create_child_session("parent", "isolated", "general", "gpt-5.5", isolation="worktree")
    assert Path(mgr.session_store.load(plain).workspace) == repo
    copy = Path(mgr.session_store.load(asked).workspace)
    assert copy != repo and copy.name == f"subagent-{asked}" and (copy / "app.py").exists()


def test_an_agent_definition_can_ask_for_every_one_of_its_runs(tmp_path):
    from harness.tools.task_agents import AgentInfo, resolve_agents

    class Config:
        agents = {"builder": {"description": "builds", "isolation": "Worktree"}, "odd": {"isolation": "vm"}}

    agents = resolve_agents(Config())
    assert agents["builder"].isolation == "worktree" and agents["odd"].isolation == ""
    repo = _repo(tmp_path)
    mgr = _manager(repo)
    child = mgr.create_child_session(
        "parent", "build", "builder", "gpt-5.5", agent_info=AgentInfo(name="builder", isolation="worktree")
    )
    assert Path(mgr.session_store.load(child).workspace).name == f"subagent-{child}"


def test_a_copy_that_was_asked_for_and_could_not_be_made_is_said(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    mgr = _manager(plain)
    out = asyncio.run(
        mgr.spawn_subagent(
            parent_id="parent", description="isolate", prompt="do it", agent_name="general",
            isolation="worktree",
        )
    )
    assert out["status"] == "completed" and out["unisolated"] == "not a git repository"
    block = changes_of(out)
    assert "could not be set up (not a git repository)" in block and "worked in your own folder" in block
    # Not asked: a non-repo is the ordinary silent fallback.
    quiet = asyncio.run(
        mgr.spawn_subagent(parent_id="parent", description="plain", prompt="do it", agent_name="general")
    )
    assert "unisolated" not in quiet
    # Nor is the global switch an ask: it isolates what it can and says nothing where it
    # cannot, or every delegation in a plain folder would carry the warning.
    from harness.safety.secrets import state_dir

    config = state_dir() / "config.toml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text("[delegation]\nworktree_isolation = true\n", encoding="utf-8")
    switched = _manager(plain)
    assert switched.wants_worktree(switched.session_store.load("parent")) is True
    silent = asyncio.run(
        switched.spawn_subagent(parent_id="parent", description="plain", prompt="do it", agent_name="general")
    )
    assert silent["status"] == "completed" and "unisolated" not in silent


def test_the_task_tool_checks_the_option_and_passes_it_only_when_asked(tmp_path):
    seen: list[dict] = []

    def spawner(**kwargs):
        seen.append(kwargs)
        return {"task_id": "t1", "status": "completed", "report": "done", "tokens": 1}

    tools = task_tools(workspace=tmp_path, provider=Provider(), model="m", spawner=spawner, session_id="s1")
    task = next(t for t in tools if getattr(t, "__name__", "") == "task")
    assert task.__tool_schema__["function"]["parameters"]["properties"]["isolation"]["enum"] == ["worktree"]

    task(description="d", prompt="p", subagent_type="general")
    task(description="d", prompt="p", subagent_type="general", isolation="WorkTree")
    assert "isolation" not in seen[0] and seen[1]["isolation"] == "worktree"
    assert 'must be "worktree"' in task(description="d", prompt="p", subagent_type="general", isolation="vm")["error"]
    assert "only applies to a new task" in task(
        description="d", prompt="p", subagent_type="general", isolation="worktree", task_id="t1"
    )["error"]
    assert len(seen) == 2


# -- handed back by a person -------------------------------------------------------------------


def test_the_manager_shows_applies_and_tells_the_parent_model(tmp_path):
    repo = _repo(tmp_path)
    mgr = _manager(repo)
    child = mgr.create_child_session("parent", "isolated", "general", "gpt-5.5", isolation="worktree")
    _work({"path": mgr.session_store.load(child).workspace})

    view = mgr.child_worktree_view(child)["worktree"]
    assert view["branch"] == f"reeve-sub/{child}" and view["busy"] is False
    assert sorted(f["path"] for f in view["files"]) == ["app.py", "new.py", "old.py"]
    assert mgr.children_view("parent")[0]["worktree"] is True
    assert handback_context(mgr, "parent") == ""

    # After a restart the manager's own map is empty: the copy is found on disk.
    mgr._child_worktrees.clear()
    applied = mgr.apply_child_worktree(child)
    assert applied["ok"] is True and len(applied["files"]) == 3
    assert (repo / "new.py").exists()
    assert mgr.child_worktree_view(child) == {"worktree": None}
    assert "worktree" not in mgr.children_view("parent")[0]
    told = handback_context(mgr, "parent")
    assert child in told and "APPLIED" in told and "3 file(s)" in told
    assert mgr.apply_child_worktree(child) == {
        "ok": False, "error": "this subagent has no separate copy to hand back",
    }


def test_discard_is_told_too_and_a_working_child_is_left_alone(tmp_path):
    repo = _repo(tmp_path)
    mgr = _manager(repo)
    child = mgr.create_child_session("parent", "isolated", "general", "gpt-5.5", isolation="worktree")
    copy = _work({"path": mgr.session_store.load(child).workspace})

    assert mgr.try_mark_running(child)
    refused = mgr.discard_child_worktree(child)
    assert refused["ok"] is False and "still working" in refused["error"] and copy.is_dir()
    mgr.mark_idle(child)

    assert mgr.discard_child_worktree(child) == {"ok": True}
    assert not copy.exists()
    assert "DISCARDED" in handback_context(mgr, "parent")


def test_the_three_routes_answer_for_a_child(tmp_path):
    from fastapi.testclient import TestClient

    from harness.interface.server.app import create_app

    repo = _repo(tmp_path)
    mgr = _manager(repo)
    child = mgr.create_child_session("parent", "isolated", "general", "gpt-5.5", isolation="worktree")
    _work({"path": mgr.session_store.load(child).workspace})
    client = TestClient(create_app(mgr))

    seen = client.get(f"/v1/sessions/{child}/worktree").json()["worktree"]
    assert len(seen["files"]) == 3 and seen["busy"] is False
    assert client.get("/v1/sessions/parent/children").json()["children"][0]["worktree"] is True
    assert client.post(f"/v1/sessions/{child}/worktree/apply").json()["ok"] is True
    assert (repo / "new.py").exists()
    assert client.get(f"/v1/sessions/{child}/worktree").json() == {"worktree": None}
    assert client.post(f"/v1/sessions/{child}/worktree/discard").json()["ok"] is False
