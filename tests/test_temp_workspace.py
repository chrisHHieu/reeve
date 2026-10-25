"""Starting in a temporary folder: the folder is made when the first message is sent
(`POST /v1/workspaces/temp`, a git repository for code work), the `ready` event marks it with
`temp_workspace`, and "Save as project…" moves it somewhere real later
(`POST /v1/sessions/{id}/save-as-project`).
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from harness.interface.server import create_app
from harness.interface.server.manager import SessionManager
from harness.llm.providers import ModelCapabilities, ProviderClient
from harness.session.sessions import SessionRecord


class _NoTurns(ProviderClient):
    def complete(self, *, model, messages, tools=None, **settings):  # pragma: no cover
        raise AssertionError("no turn is run")

    def capabilities(self, model):
        return ModelCapabilities()


@pytest.fixture
def served(tmp_path, monkeypatch):
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    manager = SessionManager(workspace=tmp_path, provider=_NoTurns())
    manager._prefs["scratch_base"] = str(tmp_path / "scratch")
    return manager, TestClient(create_app(manager))


def _temp(client, sid, **options):
    return client.post("/v1/workspaces/temp", json={"session_id": sid, **options}).json()


def _saved(manager, sid, workspace, **record):
    manager.session_store.save(SessionRecord(session_id=sid, workspace=str(workspace), model="m", mode="interactive", agent="reeve", **record))


def _save_as(client, sid, path):
    return client.post(f"/v1/sessions/{sid}/save-as-project", json={"path": str(path)}).json()


def test_a_temporary_folder_is_made_once_as_a_repository_and_kept_out_of_recents(served, tmp_path):
    manager, client = served
    made = _temp(client, "abc123", git=True)
    folder = Path(made["path"])
    assert made["ok"] is True and folder.is_dir() and folder.parent == (tmp_path / "scratch").resolve()
    assert made["git"] is True and (folder / ".git").is_dir()
    again = _temp(client, "abc123")  # sent again: the same folder
    assert again["ok"] is True and again["path"] == made["path"]
    assert manager.is_temp_workspace(made["path"]) is True
    manager.session_store.touch_workspace(made["path"])
    assert made["path"] not in [w["path"] for w in manager.recent_workspaces()]


@pytest.mark.parametrize("sid", ["", "../evil", "a/b", ".."])
def test_a_session_id_that_is_not_a_plain_name_is_refused(served, sid):
    assert _temp(served[1], sid)["ok"] is False


def test_saving_it_as_a_project_moves_the_files_and_the_session_with_them(served, tmp_path):
    manager, client = served
    source = Path(_temp(client, "sess1")["path"])
    (source / "notes.txt").write_text("hello", encoding="utf-8")
    _saved(manager, "sess1", source, messages=[{"role": "user", "content": "hi"}])
    # A live session whose shell stands in the folder: on Windows the move is refused (WinError 32)
    # unless the shell is closed first, which is the case this covers.
    engine = manager.get_engine("sess1", agent="reeve", workspace=str(source))
    run = getattr(getattr(engine, "executor", None), "run", None)
    if callable(run):
        run("echo hello")
    destination = tmp_path / "projects" / "myproj"
    saved = _save_as(client, "sess1", destination)
    assert saved["ok"] is True
    moved = Path(saved["path"])
    assert moved == destination.resolve() and (moved / "notes.txt").read_text(encoding="utf-8") == "hello"
    assert not source.exists()
    assert manager.session_store.load("sess1").workspace == str(moved)
    assert manager.is_temp_workspace(str(moved)) is False


def test_a_real_folder_or_a_destination_with_files_in_it_is_refused(served, tmp_path):
    manager, client = served
    real = tmp_path / "realproj"
    real.mkdir()
    _saved(manager, "s2", real)
    assert _save_as(client, "s2", tmp_path / "x")["ok"] is False  # not temporary
    source = Path(_temp(client, "s3")["path"])
    _saved(manager, "s3", source)
    occupied = tmp_path / "full"
    occupied.mkdir()
    (occupied / "occupied.txt").write_text("x", encoding="utf-8")
    assert _save_as(client, "s3", occupied)["ok"] is False and source.is_dir()  # and the folder is left where it was
