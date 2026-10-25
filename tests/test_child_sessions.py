"""Child sessions — a `task`-spawned subagent is a real session row, hidden from roots.

What must hold: the child's `kind="subagent"` marker (with its
`parent_session`, title, agent and model) survives a reload and the per-turn save; every
session listing hides it unless asked for children explicitly; `children_of` returns
exactly a parent's children; and deleting a parent prunes the subtree. A FORK is pinned
as unchanged: it uses `parent_session` too, but it is a visible root.
"""

from __future__ import annotations

from harness.session.conversations import ConversationStore
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient
from harness.interface.server.manager import SessionManager
from harness.session.sessions import SessionRecord


class Provider(ProviderClient):
    def complete(self, *, model, messages, tools=None, **settings):
        return AssistantTurn(text="ok", finish_reason="stop")

    def capabilities(self, model):
        return ModelCapabilities()


def _manager(tmp_path, monkeypatch) -> SessionManager:
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    return SessionManager(workspace=tmp_path, provider=Provider())


def _parent(mgr: SessionManager, sid: str = "parent-1") -> SessionRecord:
    record = SessionRecord(
        session_id=sid,
        workspace=str(mgr.default_workspace or "."),
        model="gpt-5.5",
        mode="interactive",
        messages=[
            {"role": "user", "content": "first question"},
            {"role": "assistant", "content": "first answer"},
        ],
        title="a long conversation",
        agent="reeve",
    )
    mgr.session_store.save(record)
    return record


# -- the marker is real and durable -------------------------------------------------


def test_create_child_session_persists_the_marker_and_survives_reload(tmp_path, monkeypatch):
    mgr = _manager(tmp_path, monkeypatch)
    parent = _parent(mgr)

    child_id = mgr.create_child_session(
        parent.session_id, "Investigate the cache", "explorer", "gpt-5.5-mini"
    )

    child = mgr.session_store.load(child_id)
    assert child is not None
    assert child.kind == "subagent"
    assert child.parent_session == parent.session_id
    assert child.title == "Investigate the cache (@explorer subagent)"
    assert child.agent == "explorer"
    assert child.model == "gpt-5.5-mini"
    assert child.workspace == parent.workspace
    assert child.mode == parent.mode

    # The per-turn save rebuilds the record WITHOUT kind (manager.save); the upsert's
    # INSERT-only rule must keep the child a child.
    child.messages = child.messages + [{"role": "user", "content": "go"}]
    mgr.session_store.save(child)
    again = mgr.session_store.load(child_id)
    assert again.kind == "subagent" and again.parent_session == parent.session_id

    # ...and a fresh store over the same db (the restart path) still reads it.
    reopened = ConversationStore(mgr.session_store.base)
    assert reopened.load(child_id).kind == "subagent"
    # An ordinary row written without the field defaults to a root.
    assert reopened.load(parent.session_id).kind == "root"


# -- every listing path is roots-only unless asked ----------------------------------


def test_an_existing_row_backfills_to_root(tmp_path):
    """The migration is `ADD COLUMN kind TEXT DEFAULT 'root'`: a DB written before the
    column existed keeps every conversation, and each reads as the root it always was."""
    import sqlite3

    conn = sqlite3.connect(tmp_path / "reeve.db")
    conn.executescript(
        """
        CREATE TABLE sessions (
            session_id TEXT PRIMARY KEY, workspace TEXT, model TEXT, mode TEXT,
            title TEXT, agent TEXT DEFAULT 'reeve', n_msgs INTEGER DEFAULT 0,
            messages TEXT, extra_roots TEXT, pinned INTEGER DEFAULT 0,
            archived INTEGER DEFAULT 0, origin TEXT, origin_label TEXT,
            auto_title TEXT, renamed INTEGER DEFAULT 0, thinking TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    conn.execute(
        "INSERT INTO sessions (session_id, workspace, model, mode, title) "
        "VALUES (?, ?, ?, ?, ?)",
        ("legacy", str(tmp_path), "m", "interactive", "old chat"),
    )
    conn.commit()
    conn.close()

    store = ConversationStore(tmp_path)
    assert store.load("legacy").kind == "root"
    assert [r.session_id for r in store.list()] == ["legacy"]


def test_the_sidebar_list_hides_children_unless_asked(tmp_path, monkeypatch):
    mgr = _manager(tmp_path, monkeypatch)
    parent = _parent(mgr)
    child_id = mgr.create_child_session(parent.session_id, "Research", "explorer", "m")

    listed = [s["session_id"] for s in mgr.list_sessions()]
    assert parent.session_id in listed
    assert child_id not in listed

    with_children = [s["session_id"] for s in mgr.list_sessions(include_children=True)]
    assert child_id in with_children

    # The store, which backs recall and search too, has the same default.
    assert child_id not in [r.session_id for r in mgr.session_store.list()]
    assert child_id in [
        r.session_id for r in mgr.session_store.list(include_children=True)
    ]


def test_the_route_hides_children_unless_asked(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from harness.interface.server import create_app

    mgr = _manager(tmp_path, monkeypatch)
    parent = _parent(mgr, "parent-route")
    child_id = mgr.create_child_session(parent.session_id, "Research", "explorer", "m")
    client = TestClient(create_app(mgr))

    ids = [s["session_id"] for s in client.get("/v1/sessions").json()["sessions"]]
    assert parent.session_id in ids and child_id not in ids

    ids = [
        s["session_id"]
        for s in client.get("/v1/sessions?include_children=true").json()["sessions"]
    ]
    assert child_id in ids


def test_search_history_ignores_a_childs_transcript(tmp_path, monkeypatch):
    from harness.tools.history import HistorySearch

    mgr = _manager(tmp_path, monkeypatch)
    parent = _parent(mgr, "parent-search")
    child_id = mgr.create_child_session(parent.session_id, "Research", "explorer", "m")
    hidden = mgr.session_store.load(child_id)
    hidden.messages = [
        {"role": "user", "content": "the passphrase is ZEBRA"},
        {"role": "assistant", "content": "noted"},
    ]
    mgr.session_store.save(hidden)

    search = HistorySearch(
        mgr.session_store,
        session_id=parent.session_id,
        workspace=str(mgr.default_workspace or "."),
    )
    assert search.search("ZEBRA")["hits"] == []


# -- children_of is exact -----------------------------------------------------------


def test_children_of_returns_exactly_the_children(tmp_path, monkeypatch):
    mgr = _manager(tmp_path, monkeypatch)
    parent = _parent(mgr)
    first = mgr.create_child_session(parent.session_id, "one", "explorer", "m")
    second = mgr.create_child_session(parent.session_id, "two", "explorer", "m")
    stranger = mgr.create_child_session("some-other-parent", "three", "explorer", "m")

    assert mgr.children_of(parent.session_id) == [first, second]
    assert mgr.children_of(first) == []
    assert stranger not in mgr.children_of(parent.session_id)


# -- a fork is lineage, not a child -------------------------------------------------


def test_a_fork_is_still_a_visible_root(tmp_path, monkeypatch):
    mgr = _manager(tmp_path, monkeypatch)
    parent = _parent(mgr)
    fork_id = mgr.fork_session(parent.session_id)["session_id"]

    fork = mgr.session_store.load(fork_id)
    assert fork.parent_session == parent.session_id  # lineage stays in the column
    assert fork.kind == "root"  # ...but it is not a subagent

    assert mgr.children_of(parent.session_id) == []
    assert fork_id in [s["session_id"] for s in mgr.list_sessions()]
    assert fork_id in [r.session_id for r in mgr.session_store.list()]


# -- the task chain: one trace over a root and everything it spawns -------------------


def test_a_session_log_carries_the_chain_trace_of_its_root(tmp_path, monkeypatch):
    """The `trace` id, one level above `sessionId`: the root and every subagent it
    spawns (any depth) write the SAME trace, so a reader can group the chain's rows
    without joining on parent columns."""
    mgr = _manager(tmp_path, monkeypatch)
    parent = _parent(mgr, "trace-root")
    child = mgr.create_child_session(parent.session_id, "one", "explorer", "m")
    grandchild = mgr.create_child_session(child, "two", "explorer", "m")

    for session_id in (parent.session_id, child, grandchild):
        assert mgr.trace_of(session_id) == parent.session_id, session_id

    mgr.session_log(child).append("turn/start", turn=1)
    assert mgr.session_log(child).read()[0]["trace"] == parent.session_id


def test_a_fork_starts_its_own_chain(tmp_path, monkeypatch):
    """A fork branches the conversation at a boundary — a new task chain, with the parent
    session as lineage only. Its own subagents inherit the FORK's trace."""
    mgr = _manager(tmp_path, monkeypatch)
    parent = _parent(mgr, "trace-fork-root")
    fork_id = mgr.fork_session(parent.session_id)["session_id"]
    child = mgr.create_child_session(fork_id, "one", "explorer", "m")

    assert mgr.trace_of(parent.session_id) == parent.session_id
    assert mgr.trace_of(fork_id) == fork_id
    assert mgr.trace_of(child) == fork_id


def test_a_parentless_row_traces_to_itself(tmp_path, monkeypatch):
    """An orphan (a parent that was deleted, a row written by a newer process) is its own
    chain: the walk stops rather than raising, because an unreadable trace is worse than
    a short one."""
    mgr = _manager(tmp_path, monkeypatch)
    orphan = _parent(mgr, "trace-orphan")
    orphan.parent_session = "gone-parent"
    orphan.kind = "subagent"
    mgr.session_store.save(orphan)
    # The INSERT-only lineage keeps the row as written; the walk finds no parent row.
    assert mgr.trace_of("trace-orphan") == "trace-orphan"


# -- deleting a parent prunes the subtree -------------------------------------------


def test_deleting_a_parent_removes_its_children(tmp_path, monkeypatch):
    mgr = _manager(tmp_path, monkeypatch)
    parent = _parent(mgr, "parent-delete")
    child = mgr.create_child_session(parent.session_id, "one", "explorer", "m")
    grandchild = mgr.create_child_session(child, "two", "explorer", "m")
    bystander = _parent(mgr, "bystander")

    out = mgr.delete_session(parent.session_id)

    assert out["ok"] is True
    assert mgr.session_store.load(parent.session_id) is None
    assert mgr.session_store.load(child) is None
    assert mgr.session_store.load(grandchild) is None
    assert mgr.session_store.load(bystander.session_id) is not None


def test_deleting_one_child_leaves_the_parent_and_siblings(tmp_path, monkeypatch):
    mgr = _manager(tmp_path, monkeypatch)
    parent = _parent(mgr, "parent-partial")
    first = mgr.create_child_session(parent.session_id, "one", "explorer", "m")
    second = mgr.create_child_session(parent.session_id, "two", "explorer", "m")

    assert mgr.delete_session(first)["ok"] is True

    assert mgr.session_store.load(first) is None
    assert mgr.session_store.load(second) is not None
    assert mgr.session_store.load(parent.session_id) is not None
