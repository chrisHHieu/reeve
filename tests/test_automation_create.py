"""Making an automation from the app (`create_automation`, behind "New automation" and the templates):
what it refuses and why, and that one it accepts is saved with a scratch folder of its own. No
network and no model.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from harness.interface.server.manager import SessionManager

DAILY = "0 8 * * *"


def _fresh_manager(tmp_path, monkeypatch) -> SessionManager:
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    return SessionManager(data_dir=tmp_path / "data")


def test_a_valid_automation_is_saved_with_its_own_scratch_folder(tmp_path, monkeypatch):
    manager = _fresh_manager(tmp_path, monkeypatch)
    made = manager.create_automation({"title": "Morning news briefing", "instructions": "Search the web and write a 5-bullet briefing.", "cron": DAILY})
    assert made["ok"] is True
    task = made["task"]
    assert (task["title"], task["schedule"]) == ("Morning news briefing", "Every day at ~8:00 AM")
    saved = manager.task_store.get(task["id"])  # in the store, with a new scratch folder
    assert saved is not None and saved.agent == "reeve" and Path(saved.workspace).is_dir()


@pytest.mark.parametrize(
    ("asked", "said"),
    [
        ({"title": "Bad", "instructions": "do something", "cron": "not-a-cron"}, "invalid cron"),
        ({"title": "No instructions", "instructions": "  ", "cron": DAILY}, "instructions"),
        ({"title": "No schedule", "instructions": "do something"}, ""),
    ],
)
def test_a_bad_schedule_no_instructions_or_no_schedule_saves_nothing(tmp_path, monkeypatch, asked, said):
    manager = _fresh_manager(tmp_path, monkeypatch)
    refused = manager.create_automation(asked)
    assert refused["ok"] is False and said in refused.get("error", "")
    assert manager.task_store.list() == []  # nothing saved


def test_create_automation_editor_control_row(tmp_path, monkeypatch):
    """The editor's control row rides through: a project folder, a pinned model, and one
    of the chat composer's permission modes land on the task record."""
    manager = _fresh_manager(tmp_path, monkeypatch)
    ws = tmp_path / "proj"
    ws.mkdir()
    out = manager.create_automation(
        {
            "title": "PR watch",
            "instructions": "review the merged PRs",
            "cron": "0 8 * * *",
            "workspace": str(ws),
            "model": "vertex:gemini/gemini-3.7-flash",
            "mode": "auto",
        }
    )
    assert out["ok"] is True
    task = out["task"]
    assert task["workspace"] == str(ws)
    assert task["model"] == "vertex:gemini/gemini-3.7-flash"
    assert task["mode"] == "auto"
    saved = manager.task_store.get(task["id"])
    assert saved.workspace == str(ws)  # the project folder, NOT a provisioned scratch


def test_create_automation_rejects_an_unknown_mode(tmp_path, monkeypatch):
    manager = _fresh_manager(tmp_path, monkeypatch)
    out = manager.create_automation(
        {"title": "T", "instructions": "I", "cron": "0 8 * * *", "mode": "yolo"}
    )
    assert out["ok"] is False
    assert "invalid mode" in out["error"]
    assert manager.task_store.list() == []


def test_create_automation_defaults_keep_scratch_and_server_model(tmp_path, monkeypatch):
    """No control-row input → the long-standing defaults: managed scratch folder, server
    model at run time, the interactive mode with the inbox approver."""
    manager = _fresh_manager(tmp_path, monkeypatch)
    out = manager.create_automation(
        {"title": "T", "instructions": "I", "cron": "0 8 * * *"}
    )
    saved = manager.task_store.get(out["task"]["id"])
    assert Path(saved.workspace).is_dir()
    assert saved.model is None
    assert saved.mode is None


def test_update_automation_control_fields(tmp_path, monkeypatch):
    """The detail page's edit mode can move the task to a project folder, pin a model,
    and change the mode — and "" clears back to scratch / server default."""
    manager = _fresh_manager(tmp_path, monkeypatch)
    tid = manager.create_automation(
        {"title": "T", "instructions": "I", "cron": "0 8 * * *"}
    )["task"]["id"]
    ws = tmp_path / "proj2"
    ws.mkdir()
    res = manager.update_automation(
        tid, {"workspace": str(ws), "model": "m1", "mode": "discuss"}
    )
    assert res["ok"] is True
    task = manager.task_store.get(tid)
    assert task.workspace == str(ws)
    assert task.model == "m1"
    assert task.mode == "discuss"

    res = manager.update_automation(tid, {"model": "", "workspace": ""})
    assert res["ok"] is True
    task = manager.task_store.get(tid)
    assert task.model is None
    assert task.mode == "discuss"  # untouched by this patch
    assert Path(task.workspace).is_dir() and task.workspace != str(ws)


def test_manual_run_seeds_the_session_with_the_task_config(tmp_path, monkeypatch):
    """▶ Run now opens a LIVE session — a brand-new session would build its engine from
    the SERVER defaults, so a task pinned to full access would start out asking for
    approval. prepare_manual_run seeds the session record from the task first: mode,
    model, folder, and title all ride from the task, with legacy spellings normalized
    ("auto" → danger-full-access)."""
    manager = _fresh_manager(tmp_path, monkeypatch)
    out = manager.create_automation(
        {
            "title": "Morning new",
            "instructions": "brief me",
            "cron": "0 9 * * *",
            "mode": "auto",
            "model": "openrouter:ling/ling-3.0-flash",
        }
    )
    tid = out["task"]["id"]
    prepped = manager.prepare_manual_run(tid)
    assert prepped["ok"] is True
    record = manager.session_store.load(prepped["session_id"])
    assert record is not None
    assert record.mode == "danger-full-access"
    assert record.model == "openrouter:ling/ling-3.0-flash"
    assert record.title == "Morning new"
    assert record.workspace == out["task"]["workspace"]
