"""Titles the model writes. When a turn of the person's ends, the manager makes one call,
on the conversation's own provider and model and without waiting for it, for a title of four or
five words. It is kept as `auto_title` and never as `title`, so a rename always wins. An answer of
"small-talk" earns one more try after the next message, with both openers, and any failure is
dropped without a word.
"""

from __future__ import annotations

import asyncio
import time

from harness.interface.server.manager import SessionManager
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient


class TitleAwareProvider(ProviderClient):
    """Answers the conversation from one queue and the title calls (known by the titling prompt)
    from another, so a title call made on the side never takes a scripted turn. A queued title can
    be text, a whole turn, or an exception to raise."""

    def __init__(self, chat_turns, titles):
        self._chat, self._titles = list(chat_turns), list(titles)
        self.title_calls: list[list[dict]] = []

    def complete(self, *, model, messages, tools=None, **settings):
        if not (messages and "title chat sessions" in str(messages[0].get("content", ""))):
            return self._chat.pop(0)
        self.title_calls.append([dict(m) for m in messages])
        title = self._titles.pop(0)
        if isinstance(title, Exception):
            raise title
        return title if isinstance(title, AssistantTurn) else AssistantTurn(text=title, finish_reason="stop")

    def capabilities(self, model):
        return ModelCapabilities()


def _text(text):
    return AssistantTurn(text=text, finish_reason="stop")


async def _turn(mgr: SessionManager, sid: str, text: str) -> None:
    """One turn as the server runs it (run, save, mark idle, which is where the title is asked
    for), then the wait for the title call to settle."""
    engine = mgr.get_engine(sid, agent="chat")
    mgr.mark_running(sid)
    async for _ in engine.run(text):
        pass
    mgr.save(sid, engine)
    mgr.mark_idle(sid)
    settled_by = time.time() + 5.0
    while sid in mgr._autotitle_inflight and time.time() < settled_by:
        await asyncio.sleep(0.005)


def _mgr(tmp_path, chat_turns, titles):
    provider = TitleAwareProvider(chat_turns, titles)
    return SessionManager(workspace=tmp_path, provider=provider), provider


def _title(mgr, sid):
    return mgr.session_store.load(sid).title


def _auto_title(mgr, sid):
    return mgr.session_store.title_state(sid)["auto_title"]


async def test_title_set_after_first_turn(tmp_path):
    mgr, provider = _mgr(tmp_path, [_text("sure")], ["Japan Trip Planning Help"])
    await _turn(mgr, "s1", "help me plan a trip to japan")
    [asked] = provider.title_calls
    assert asked[-1]["content"] == "help me plan a trip to japan"
    assert _auto_title(mgr, "s1") == "Japan Trip Planning Help"
    # shown in place of the title cut from the first message, everywhere
    assert _title(mgr, "s1") == "Japan Trip Planning Help"
    assert next(s for s in mgr.list_sessions() if s["session_id"] == "s1")["title"] == "Japan Trip Planning Help"


async def test_small_talk_sentinel_then_retry_with_both_openers(tmp_path):
    mgr, provider = _mgr(tmp_path, [_text("hey!"), _text("on it")], ["small-talk", "Quarterly Report Draft"])
    await _turn(mgr, "s2", "hey")
    assert _auto_title(mgr, "s2") is None and _title(mgr, "s2") == "hey"  # the first message stands in
    await _turn(mgr, "s2", "help me draft the quarterly report")
    assert len(provider.title_calls) == 2
    retried_with = provider.title_calls[1][-1]["content"]
    assert "hey" in retried_with and "quarterly report" in retried_with
    assert _title(mgr, "s2") == "Quarterly Report Draft"


async def test_gives_up_after_second_sentinel(tmp_path):
    mgr, provider = _mgr(tmp_path, [_text("hi"), _text("hello"), _text("yo")], ["small-talk", "small-talk", "Should Never Be Asked"])
    for said in ("hey", "how are you", "good morning"):
        await _turn(mgr, "s3", said)
    assert len(provider.title_calls) == 2  # the first try and the one retry
    assert _auto_title(mgr, "s3") is None and _title(mgr, "s3") == "hey"


async def test_manual_rename_always_wins(tmp_path):
    mgr, _ = _mgr(tmp_path, [_text("ok")], ["Login Bug Investigation"])
    await _turn(mgr, "s4", "the login page 500s")
    assert _title(mgr, "s4") == "Login Bug Investigation"
    mgr.rename_session("s4", "Prod incident 42")  # after the model's title landed
    assert _title(mgr, "s4") == "Prod incident 42"
    assert mgr.session_store.set_auto_title("s4", "sneaky") is False  # and a late one cannot displace it
    assert _title(mgr, "s4") == "Prod incident 42"


async def test_rename_before_generation_blocks_it(tmp_path):
    mgr, provider = _mgr(tmp_path, [_text("hi"), _text("ok")], ["small-talk", "Should Never Be Asked"])
    await _turn(mgr, "s5", "hey")
    mgr.rename_session("s5", "My Thread")
    await _turn(mgr, "s5", "help me plan the offsite")
    assert len(provider.title_calls) == 1 and _title(mgr, "s5") == "My Thread"  # no retry was made


async def test_provider_failure_is_swallowed(tmp_path):
    mgr, _ = _mgr(tmp_path, [_text("ok")], [RuntimeError("model down")])
    await _turn(mgr, "s6", "summarize this doc")
    assert _auto_title(mgr, "s6") is None and _title(mgr, "s6") == "summarize this doc"


async def test_sanitizes_and_rejects_absurd_output(tmp_path):
    quoted, _ = _mgr(tmp_path, [_text("ok")], ['"Fix   Login Bug"'])
    await _turn(quoted, "s7", "the login page 500s")
    assert _title(quoted, "s7") == "Fix Login Bug"  # quotes off, spaces closed up
    rambling, _ = _mgr(tmp_path / "b", [_text("ok")], ["x" * 90])
    await _turn(rambling, "s8", "the login page 500s")
    assert _auto_title(rambling, "s8") is None  # past 80 characters it is a failure, not a title


async def test_cap_cut_completion_is_rejected_and_retried(tmp_path):
    # The Gemini failure the owner saw: thought tokens eat the token cap, the answer is
    # ONE truncated word ("Giới" for "Giới thiệu…") with finish "length". Storing it
    # would both mislead and close the turn-2 retry window.
    cut = AssistantTurn(text="Giới", finish_reason="length")
    mgr, provider = _mgr(
        tmp_path, [_text("sure"), _text("ok")], [cut, "Financial Analysis Deep Dive"]
    )
    await _turn(mgr, "s9", "phân tích tài chính của tôi")
    assert mgr.session_store.title_state("s9")["auto_title"] is None
    assert mgr.session_store.load("s9").title == "phân tích tài chính của tôi"

    await _turn(mgr, "s9", "và gave me a plan")
    assert len(provider.title_calls) == 2  # the rejection left the retry alive
    assert mgr.session_store.load("s9").title == "Financial Analysis Deep Dive"


async def test_one_word_answer_is_rejected(tmp_path):
    # The backstop for providers that don't report finish honestly: one word is below
    # anything the prompt asks for and reads as a truncation survivor — the first-line
    # fallback keeps the seat. A terse-but-complete two-word answer passes; the gate
    # catches truncation, it does not second-guess style.
    mgr, _ = _mgr(tmp_path, [_text("ok")], ["Giới"])
    await _turn(mgr, "s10", "tôi cần phân tích tài chính")
    assert mgr.session_store.title_state("s10")["auto_title"] is None
    assert mgr.session_store.load("s10").title == "tôi cần phân tích tài chính"


async def test_a_bound_session_gets_the_same_first_line_title_as_a_local_one(tmp_path):
    """A remote binding seeds a bare session row before the first turn (the welcome
    screen's host pick). That seed must not persist the "New session" placeholder —
    with the upsert's COALESCE it would outrank every later snapshot and the session
    would read as new forever. The titler LLM fails here on purpose: only the
    first-line fallback can answer, exactly as it does for a local session."""
    from harness.orchestration.remote_environments import RemoteEnvironmentStore
    from harness.testing.fake_ssh import FakeRemoteHost, FakeSsh

    provider = TitleAwareProvider(
        [_text("ok"), _text("ok")],
        [RuntimeError("titler offline"), RuntimeError("titler offline")],
    )
    mgr = SessionManager(workspace=tmp_path, provider=provider)
    store = RemoteEnvironmentStore(
        tmp_path / "remote-environments.json",
        runner=FakeSsh(host=FakeRemoteHost(directories={"/home/dev/project"})),
    )
    mgr.remote_environments = store
    profile = store.create(
        {"name": "Build server", "host": "dev.example.com", "user": "dev"}
    )
    assert mgr.set_session_environment(
        "remote-1", profile["id"], "/home/dev/project"
    )["ok"]
    # The binding pre-created the row: no placeholder title may be persisted.
    seeded = mgr.session_store.load("remote-1")
    assert seeded.title in (None, "")

    await _turn(mgr, "remote-1", "fix the remote build please")
    record = mgr.session_store.load("remote-1")
    assert record.environment_id == profile["id"]
    assert record.workspace == "/home/dev/project"
    assert record.title == "fix the remote build please"
    row = next(s for s in mgr.list_sessions() if s["session_id"] == "remote-1")
    assert row["title"] == "fix the remote build please"

    # Local sessions keep the exact same trigger and wording rules.
    await _turn(mgr, "local-1", "tidy the local repo")
    local = mgr.session_store.load("local-1")
    assert local.title == "tidy the local repo"
    assert local.environment_id is None


async def test_a_title_call_lands_in_the_usage_rollup(tmp_path):
    """Titles (like the memory passes) call the model outside any turn, so no `turn/end`
    counts them — and before 2026-09-24 nothing did. They are metered under a sentinel."""
    from harness.llm.providers.base import TokenUsage

    titled = AssistantTurn(
        text="Japan Trip Planning Help",
        finish_reason="stop",
        usage=TokenUsage(input=40, output=8),
    )
    mgr, _ = _mgr(tmp_path, [_text("sure")], [titled])
    await _turn(mgr, "s1", "help me plan a trip to japan")

    rows = mgr.metrics_store._conn.execute(
        "SELECT calls, tokens_in, tokens_out FROM model_usage WHERE session_id = '__title__'"
    ).fetchall()
    assert [tuple(r) for r in rows] == [(1, 40, 8)]
