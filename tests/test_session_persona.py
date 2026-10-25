"""What a saved conversation keeps about itself (`session/conversations`): the agent it was started
with (the `agent` column, which `get_engine` always rebuilds from), whether it is pinned, and the
title a person gave it, which a later save never undoes.
"""

from __future__ import annotations

import pytest

from harness.session.conversations import ConversationStore
from harness.session.sessions import SessionRecord


@pytest.fixture
def store(tmp_path) -> ConversationStore:
    return ConversationStore(tmp_path)


def _saved(store, tmp_path, sid, **record):
    store.save(SessionRecord(session_id=sid, workspace=str(tmp_path), model="gpt-5.5", mode="interactive", agent="ops", **record))


def test_the_agent_is_kept_and_read_back_by_another_store(store, tmp_path):
    _saved(store, tmp_path, "s1")
    assert store.load("s1").agent == "ops"
    assert ConversationStore(tmp_path).load("s1").agent == "ops"


def test_pinning_and_renaming_are_kept(store, tmp_path):
    _saved(store, tmp_path, "s3")
    assert store.set_flags("s3", pinned=True) and store.load("s3").pinned is True
    assert store.rename("s3", "Release Captain") and store.load("s3").title == "Release Captain"


def test_save_replaces_a_stored_placeholder_but_never_a_rename(store, tmp_path):
    """The "New session" shown before there is a title is not one: a later save with a real first
    line replaces it (an old binding seed stored it as a title). A rename stays, even one to those
    very words."""

    def save():
        _saved(store, tmp_path, "s5", messages=[{"role": "user", "content": "Release Captain"}])

    save()
    with store._lock:
        store._conn.execute("UPDATE sessions SET title = 'New session' WHERE session_id = 's5'")
        store._conn.commit()
    save()
    assert store.load("s5").title == "Release Captain"
    assert store.rename("s5", "New session")
    save()
    assert store.load("s5").title == "New session"
