"""The inbox (`integrations/inbox`): approvals, questions and notifications that wait for the
person. The first answer from any surface is the one that stands; what is answered and what still
waits is told back when a session resumes; an approval asked through the inbox comes back as the
engine's outcome; and the session list says how many wait and for what.
"""

from __future__ import annotations

import asyncio

from harness.integrations.inbox import KIND_APPROVAL, KIND_NOTIFICATION, STATE_RESOLVED, InboxStore, args_preview, inbox_approver
from harness.loop import ApprovalOutcome, PermissionRequest


def _store(tmp_path):
    return InboxStore(tmp_path / "inbox.json")


def test_items_are_kept_per_session_and_a_notification_is_its_own_kind(tmp_path):
    store = _store(tmp_path)
    store.add_approval("s1", "Run shell?")
    store.add_question("s1", "Which env?")
    store.add_notification("s2", "Report ready")
    assert len(store.list(session_id="s1")) == 2 and len(store.pending("s1")) == 2
    assert store.list(session_id="s2")[0].kind == KIND_NOTIFICATION


def test_the_first_answer_stands_and_a_later_one_changes_nothing(tmp_path):
    store = _store(tmp_path)
    item = store.add_approval("s1", "Run shell?")
    assert store.resolve(item.id, "allow") is True
    assert store.resolve(item.id, "deny") is False  # from whichever surface
    answered = store.get(item.id)
    assert (answered.state, answered.resolution) == (STATE_RESOLVED, "allow")
    assert store.resolve("nope", "allow") is False


def test_an_answer_survives_a_reload(tmp_path):
    item = _store(tmp_path).add_approval("s1", "Run shell?")
    _store(tmp_path).resolve(item.id, "allow")
    assert _store(tmp_path).get(item.id).resolution == "allow"


def test_a_resume_is_told_what_still_waits_and_what_was_answered_meanwhile(tmp_path):
    store = _store(tmp_path)
    answered = store.add_approval("s1", "Ship the release?")
    store.resolve(answered.id, "allow")
    store.add_question("s1", "Which changelog format?")
    store.add_approval("other", "Another session's question")
    told = store.reconcile_on_resume("s1")
    assert [i["title"] for i in told["pending"]] == ["Which changelog format?"]
    assert [i["title"] for i in told["recap"]] == ["Ship the release?"]


def _asked_through_the_inbox(tmp_path, tool, reason, answer):
    """The outcome of one approval asked through the inbox and answered there, and the store."""
    store = _store(tmp_path)

    async def answer_once_it_is_there():
        for _ in range(200):
            waiting = store.pending("s1")
            if waiting:
                store.resolve(waiting[0].id, answer)
                return
            await asyncio.sleep(0.001)

    async def run():
        outcome, _ = await asyncio.gather(inbox_approver(store, "s1")(PermissionRequest(tool, {}, None, reason)), answer_once_it_is_there())
        return outcome

    return asyncio.run(run()), store


def test_an_approval_asked_through_the_inbox_comes_back_as_the_answer_given(tmp_path):
    allowed, store = _asked_through_the_inbox(tmp_path, "run_shell", "needs approval", "allow")
    assert allowed is ApprovalOutcome.ONCE
    assert store.list(session_id="s1")[0].kind == KIND_APPROVAL
    denied, _ = _asked_through_the_inbox(tmp_path / "b", "rm", "danger", "deny")
    assert denied is ApprovalOutcome.DENY


def test_the_arguments_are_previewed_on_one_short_line():
    assert args_preview({"path": "g.txt", "content": "buy milk"}) == "path: g.txt · content: buy milk"
    assert args_preview(None) == "" and args_preview({}) == ""
    assert "\n" not in args_preview({"x": "a\nb\nc"})
    assert args_preview({"content": "z" * 300}).endswith("…")


def test_an_approval_shows_what_the_call_does_after_its_reason():
    from harness.interface.server.manager.parked import approval_body

    body = approval_body(PermissionRequest("write_file", {"path": "groceries.txt", "content": "buy milk"}, None, ""))
    assert "groceries.txt" in body and "buy milk" in body
    assert approval_body(PermissionRequest("rm", {"path": "/x"}, None, "destructive")).startswith("destructive")


def test_a_session_row_says_what_it_waits_on_the_person_for(tmp_path, monkeypatch):
    """The session list carries how many prompts wait and the kind of the oldest, so a surface can
    list the session under what needs the person, and say for what, without a second call."""
    from harness.interface.server.manager import SessionManager
    from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient
    from harness.session.sessions import SessionRecord

    class _Ok(ProviderClient):
        def complete(self, *, model, messages, tools=None, **settings):
            return AssistantTurn(text="ok")

        def capabilities(self, model):
            return ModelCapabilities()

    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    workspace = tmp_path / "ws"
    workspace.mkdir()
    manager = SessionManager(workspace=str(workspace), provider=_Ok())
    for sid in ("quiet", "asked"):
        manager.session_store.save(SessionRecord(session_id=sid, workspace=str(workspace), model="gpt-5.5", mode="interactive", agent="reeve", title=sid))

    def rows():
        return {row["session_id"]: (row["attention"], row["attention_kind"]) for row in manager.list_sessions()}

    assert rows()["quiet"] == (0, None)
    manager.inbox.add_question("asked", "Which one?", options=["a", "b"])
    manager.inbox.add_approval("asked", "Approve: run_shell")
    assert rows()["asked"] == (2, "question") and rows()["quiet"] == (0, None)  # the oldest one's kind
    manager.inbox.resolve(manager.inbox.pending(session_id="asked")[0].id, "a")
    assert rows()["asked"] == (1, "approval")
