"""Item 2.5 — reach what compaction pushed out of the model's view.

The gap is not that anything was lost. `apply_to_outbound` is a projection: the canonical
messages are untouched and the `.jsonl` still holds every word. The gap is that the model
had no way to ASK, so a compacted decision became unanswerable even though it was sitting
on disk three inches away.

The load-bearing test is therefore not "the search finds a word". It is that the search
finds a passage the model can no longer see.
"""

from __future__ import annotations

from harness.context.compaction import CompactionState, apply_to_outbound
from harness.session.conversations import ConversationStore
from harness.session.sessions import SessionRecord
from harness.tools.history import HistorySearch


def _store(tmp_path) -> ConversationStore:
    return ConversationStore(tmp_path)


def _save(store, sid, workspace, messages, title=None):
    store.save(
        SessionRecord(
            session_id=sid,
            workspace=workspace,
            model="m",
            mode="interactive",
            messages=messages,
            title=title,
        )
    )


def _say(role, text):
    return {"role": role, "content": text}


PRICE_TALK = [
    _say("user", "what should we charge for the team plan?"),
    _say("assistant", "Given the support load, 240 per seat per year holds up."),
    _say("user", "ok, we settled on 240 per seat — write that down"),
    _say("assistant", "Noted: 240 per seat per year for the team plan."),
]


# -- the thing this exists for -----------------------------------------------------


def test_it_finds_what_compaction_took_out_of_the_model_view(tmp_path):
    """The whole point, and the only test that proves it. The passage is compacted OUT
    of the outbound messages — the model genuinely cannot see it — and the search still
    returns it verbatim from the canonical thread."""
    store = _store(tmp_path)
    ws = str(tmp_path / "proj")
    later = PRICE_TALK + [_say("user", "now draft the launch note")]
    _save(store, "s1", ws, later, title="Pricing")

    # What the model can still see: compaction replaces the early span with a summary.
    state = CompactionState(
        boundary_index=len(PRICE_TALK),
        summary_text="Earlier: the team discussed pricing.",
        working_state="drafting the launch note",
    )
    outbound = apply_to_outbound(later, state)
    visible = " ".join(str(m.get("content", "")) for m in outbound)
    assert "240 per seat" not in visible, "the premise is gone — rewrite this test"

    hits = HistorySearch(store, session_id="s1", workspace=ws).search("240 per seat")["hits"]
    assert hits, "compaction hid it and nothing could reach it"
    assert "240 per seat" in hits[0]["said"]


def test_a_new_session_reaches_last_weeks_decision(tmp_path):
    """"What did we settle on?" is asked in a NEW conversation more often than in the old
    one. Same project, so the sessions are the ones the sidebar already groups together."""
    store = _store(tmp_path)
    ws = str(tmp_path / "proj")
    _save(store, "old", ws, PRICE_TALK, title="Pricing")
    _save(store, "new", ws, [_say("user", "remind me what we charge")], title="Launch")

    result = HistorySearch(store, session_id="new", workspace=ws).search("settled 240")
    assert [h["session"] for h in result["hits"]] == ["Pricing"]
    assert "240 per seat" in result["hits"][0]["said"]


def test_another_project_is_not_searched(tmp_path):
    """The scope boundary. A project is a line the user already sees; reading across it
    would be one project's agent reading another's conversations."""
    store = _store(tmp_path)
    mine, theirs = str(tmp_path / "mine"), str(tmp_path / "theirs")
    _save(store, "a", mine, [_say("user", "our budget is 240 per seat")], title="Mine")
    _save(store, "b", theirs, [_say("user", "their budget is 240 per seat")], title="Theirs")

    result = HistorySearch(store, session_id="a", workspace=mine).search("240 per seat")
    assert [h["session"] for h in result["hits"]] == ["Mine"]


# -- what it returns ---------------------------------------------------------------


def test_it_returns_the_words_not_a_summary(tmp_path):
    store = _store(tmp_path)
    ws = str(tmp_path / "proj")
    _save(store, "s1", ws, PRICE_TALK, title="Pricing")

    hit = HistorySearch(store, session_id="s1", workspace=ws).search("settled")["hits"][0]
    assert "we settled on 240 per seat" in hit["said"]
    assert hit["who"] == "the user"
    assert hit["this_session"] is True


def test_the_newest_saying_comes_first(tmp_path):
    """For "what did we decide", the last time it was said is the answer; the earlier
    times are how it got there."""
    store = _store(tmp_path)
    ws = str(tmp_path / "proj")
    _save(
        store,
        "s1",
        ws,
        [
            _say("user", "maybe the rate is 200"),
            _say("user", "actually the rate is 240"),
        ],
        title="Rates",
    )
    hits = HistorySearch(store, session_id="s1", workspace=ws).search("rate is")["hits"]
    assert "240" in hits[0]["said"] and "200" in hits[1]["said"]


def test_every_word_must_appear(tmp_path):
    """AND, not OR. "what did we chốt about price" against an OR search returns every
    message containing "about"."""
    store = _store(tmp_path)
    ws = str(tmp_path / "proj")
    _save(store, "s1", ws, PRICE_TALK, title="Pricing")
    search = HistorySearch(store, session_id="s1", workspace=ws)

    assert search.search("settled 240")["hits"]
    assert search.search("settled 999")["hits"] == []


def test_tool_output_is_not_searched(tmp_path):
    """Machine output dominates a transcript by volume and is not what anyone means by
    "what we said" — a match there would bury the four human sentences around it."""
    store = _store(tmp_path)
    ws = str(tmp_path / "proj")
    _save(
        store,
        "s1",
        ws,
        [
            {"role": "tool", "tool_call_id": "c1", "content": "seat price 240 per seat"},
            _say("user", "unrelated"),
        ],
        title="Noisy",
    )
    assert HistorySearch(store, session_id="s1", workspace=ws).search("240 per seat")["hits"] == []


def test_multipart_content_is_read(tmp_path):
    """An attachment turn stores content as parts, not a string."""
    store = _store(tmp_path)
    ws = str(tmp_path / "proj")
    _save(
        store,
        "s1",
        ws,
        [{"role": "user", "content": [{"type": "text", "text": "we settled on 240 per seat"}]}],
        title="Parts",
    )
    assert HistorySearch(store, session_id="s1", workspace=ws).search("240")["hits"]


# -- bounds and the empty answer ---------------------------------------------------


def test_an_empty_query_says_what_to_do_instead(tmp_path):
    result = HistorySearch(_store(tmp_path), session_id="s1").search("   ")
    assert "distinctive words" in result["error"]


def test_no_match_says_why_rather_than_going_quiet(tmp_path):
    store = _store(tmp_path)
    ws = str(tmp_path / "proj")
    _save(store, "s1", ws, PRICE_TALK, title="Pricing")
    result = HistorySearch(store, session_id="s1", workspace=ws).search("quarterly rebate")
    assert result["hits"] == []
    assert "Every word in the query has to appear" in result["note"]


def test_the_limit_holds(tmp_path):
    store = _store(tmp_path)
    ws = str(tmp_path / "proj")
    _save(store, "s1", ws, [_say("user", f"line {i} about pricing") for i in range(30)])
    hits = HistorySearch(store, session_id="s1", workspace=ws).search("pricing", limit=3)["hits"]
    assert len(hits) == 3


def test_a_missing_session_does_not_break_the_search(tmp_path):
    """The store is live, so a session can be deleted between listing and loading."""
    store = _store(tmp_path)
    ws = str(tmp_path / "proj")
    _save(store, "gone", ws, PRICE_TALK, title="Pricing")
    search = HistorySearch(store, session_id="missing-id", workspace=ws)
    assert search.search("240 per seat")["hits"]  # the real one is still found
