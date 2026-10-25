"""How the sidebar lays out the sessions that are not pinned or waiting (owner ask 2026-10-12).

Grouped by their folder unless the person picked one list; the choice is the server's, so the
desktop window and a browser tab show the same sidebar. A plain chat's temporary folder is no
project: its row says so, and the sidebar puts it with the chats that have no folder.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from harness.interface.server.app import create_app
from harness.interface.server.manager import SessionManager
from harness.session.sessions import SessionRecord


def test_grouped_by_project_until_one_list_is_picked_and_the_pick_survives_a_restart(tmp_path):
    client = TestClient(create_app(SessionManager(data_dir=tmp_path / "data")))
    assert client.get("/v1/settings").json()["sidebar_organize"] == "project"

    assert client.post("/v1/settings/sidebar-organize", json={"sidebar_organize": "list"}).json() == {
        "ok": True,
        "sidebar_organize": "list",
    }
    restarted = TestClient(create_app(SessionManager(data_dir=tmp_path / "data")))
    assert restarted.get("/v1/settings").json()["sidebar_organize"] == "list"


def test_a_value_the_sidebar_does_not_know_is_refused_and_changes_nothing(tmp_path):
    client = TestClient(create_app(SessionManager(data_dir=tmp_path / "data")))
    refused = client.post("/v1/settings/sidebar-organize", json={"sidebar_organize": "by-agent"}).json()
    assert refused["ok"] is False
    assert client.get("/v1/settings").json()["sidebar_organize"] == "project"


def test_a_row_says_when_its_folder_is_a_chats_temporary_one(tmp_path, monkeypatch):
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    scratch = tmp_path / "scratch"
    monkeypatch.setenv("REEVE_SCRATCH_BASE", str(scratch))
    project = tmp_path / "wealthlog"
    project.mkdir()
    (scratch / "chat-1").mkdir(parents=True)
    mgr = SessionManager(workspace=tmp_path)
    for sid, ws in (("in-project", project), ("plain-chat", scratch / "chat-1")):
        mgr.session_store.save(
            SessionRecord(session_id=sid, workspace=str(ws), model="m", mode="interactive", messages=[], title=sid)
        )
    rows = {row["session_id"]: row for row in mgr.list_sessions()}
    assert rows["in-project"]["temp_workspace"] is False
    assert rows["plain-chat"]["temp_workspace"] is True
