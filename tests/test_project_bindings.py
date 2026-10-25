"""Which project a conversation's memory belongs to, through the manager and the API: the
project it is in by default, naming one, binding a conversation to a named one and back, and
worktrees of one repository counting as one project.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from harness.interface.server import SessionManager, create_app
from harness.llm.providers import ModelCapabilities, ProviderClient
from harness.session.projects import project_key
from harness.session.sessions import SessionRecord


class _NoTurns(ProviderClient):
    def complete(self, **request):  # pragma: no cover
        raise AssertionError("no turn is run")

    def capabilities(self, model):
        return ModelCapabilities()


def _served(tmp_path):
    manager = SessionManager(workspace=tmp_path, provider=_NoTurns())
    return TestClient(create_app(manager)), manager


def _conversation(manager, sid: str, workspace: Path, **record) -> None:
    manager.session_store.save(SessionRecord(session_id=sid, workspace=str(workspace), model="m", mode="auto", messages=[{"role": "user", "content": "hi"}], **record))


def _folder(tmp_path, name):
    folder = tmp_path / name
    folder.mkdir()
    return folder


def _memory_key(manager, sid):
    record = manager.session_store.load(sid)
    return manager._memory_key_for(record, record.workspace)


def _bind(client, **binding):
    return client.put("/v1/sessions/s1/bindings", json={"kind": "memory", **binding}).json()


def _git(folder, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=folder, check=True, capture_output=True)


def test_the_menu_shows_the_folders_project_and_then_the_name_given_to_it(tmp_path):
    client, manager = _served(tmp_path)
    folder = _folder(tmp_path, "proj")
    _conversation(manager, "s1", folder)

    def menu():
        return client.get("/v1/sessions/s1/project-menu", params={"kind": "memory"}).json()

    first = menu()
    assert first["bound"] is None and first["named"] == []
    assert (first["derived"]["key"], first["derived"]["kind"]) == (str(folder.resolve()), "folder")
    named = client.post("/v1/sessions/s1/project-name", json={"kind": "memory", "name": "myproj"}).json()
    assert named["ok"] and named["key"] == str(folder.resolve())
    assert [n["name"] for n in menu()["named"]] == ["myproj"]


def test_binding_to_a_named_project_moves_the_memory_and_unbinding_moves_it_back(tmp_path):
    client, manager = _served(tmp_path)
    scratch, real = _folder(tmp_path, "scratch"), _folder(tmp_path, "real")
    _conversation(manager, "s1", scratch)
    manager.session_store.names().name_current("memory", "reeve", str(real.resolve()))
    manager._auto_memory_for(str(real.resolve())).save_memory(title="Real", description="the real fact", note_type="project", body="the real fact")
    bound = _bind(client, name="reeve")
    assert bound["ok"] and bound["bindings"] == {"memory": "reeve"}
    assert _memory_key(manager, "s1") == str(real.resolve())
    unbound = _bind(client)
    assert unbound["ok"] and unbound["bindings"] == {}
    assert _memory_key(manager, "s1") == str(scratch.resolve())  # the folder's own again


def test_a_name_nobody_gave_or_a_kind_that_does_not_exist_is_refused(tmp_path):
    client, manager = _served(tmp_path)
    _conversation(manager, "s1", tmp_path)
    assert not _bind(client, name="ghost")["ok"]
    assert not client.put("/v1/sessions/s1/bindings", json={"kind": "nope", "name": "x"}).json()["ok"]


def test_saving_a_turn_keeps_the_binding(tmp_path):
    client, manager = _served(tmp_path)
    _conversation(manager, "s1", _folder(tmp_path, "p"))
    manager.session_store.names().name_current("memory", "m1", "/somewhere")
    assert _bind(client, name="m1")["ok"]
    saved = manager.session_store.load("s1")
    # a turn's save writes the record again, without the bindings
    manager.session_store.save(SessionRecord(session_id="s1", workspace=saved.workspace, model=saved.model, mode=saved.mode, messages=saved.messages))
    assert manager.session_store.load("s1").bindings == {"memory": "m1"}


def test_a_worktree_shares_its_repositorys_memory(tmp_path):
    _, manager = _served(tmp_path)
    repo = _folder(tmp_path, "repo")
    _git(repo, "init", "-q")
    (repo / "f").write_text("x")
    _git(repo, "add", "f")
    _git(repo, "commit", "-qm", "i")
    worktree = tmp_path / "wt"
    _git(repo, "worktree", "add", "-q", str(worktree))
    _conversation(manager, "a", repo)
    _conversation(manager, "b", worktree)
    assert _memory_key(manager, "a") == _memory_key(manager, "b") == str(repo.resolve())


def test_reaching_a_folder_that_has_memory_says_how_much_and_one_without_says_nothing(tmp_path):
    _, manager = _served(tmp_path)
    known = _folder(tmp_path, "known")
    manager._auto_memory_for(project_key(str(known))).save_memory(title="Fact", description="fact", note_type="project", body="fact")
    notice = manager._project_notice(str(known))
    assert notice is not None and "project memory (1 entries)" in notice
    assert manager._project_notice(str(_folder(tmp_path, "fresh"))) is None
