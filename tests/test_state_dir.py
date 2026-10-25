"""Where Reeve keeps its state."""

from __future__ import annotations

import sys

import pytest

from harness.safety.secrets import locations as secrets


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A home with no override: the default location is computed from APPDATA / ~."""
    monkeypatch.delenv("REEVE_STATE_DIR", raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData"))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    base = tmp_path / "AppData" if sys.platform == "win32" else tmp_path / ".config"
    base.mkdir(parents=True, exist_ok=True)
    return base


def test_override_wins_and_is_not_touched(tmp_path, monkeypatch):
    target = tmp_path / "elsewhere"
    monkeypatch.setenv("REEVE_STATE_DIR", str(target))
    assert secrets.state_dir() == target
    assert not target.exists()


def test_default_is_named_reeve_and_asking_creates_nothing(home):
    assert secrets.state_dir() == home / "Reeve"
    assert not (home / "Reeve").exists()


def test_the_session_database_is_reeve_db_and_the_guard_covers_it(tmp_path):
    assert secrets.database_path(tmp_path) == tmp_path / "reeve.db"
    assert secrets.protected_database_names() == ("reeve.db",)
