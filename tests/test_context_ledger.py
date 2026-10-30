"""The model is shown what its context is made of.

Scripted providers only. Whether a model folds better with the ledger is measured by
`benchmarks.ablation --enable context_ledger`; these pin what the harness shows, when, and that
the request stays the previous one plus an append.
"""

from __future__ import annotations

from harness.context import ledger
from harness.loop.context_ledger import LEDGER_EVERY, LEDGER_TAG
from harness.session.invariants import _empty_index, _index_events, audit_messages
from harness.session.turns import ATTACHED_BY

from test_context_fold import DONE, _events, _step, _with_tools, collect, history, make_engine


def _ledgers(engine):
    return [m for m in engine.messages if m.get(ATTACHED_BY) == LEDGER_TAG]


def _rows(engine):
    return [e for e in _events(engine, "context/budget") if e.get("hint") == "ledger"]


def _engine(tmp_path, steps, **kwargs):
    engine, provider = make_engine(tmp_path, [*(_step(i) for i in range(steps)), DONE], messages=history(steps=16), window=12_000, **kwargs)
    _with_tools(engine)
    engine._stamp_fold_hint = lambda message: None  # the turn-start stamp is its own test
    return engine, provider


def test_the_heaviest_results_are_named_with_their_size_and_age():
    messages = history(steps=3, bulk=400)
    messages[5]["content"] = "x" * 8_000  # the second read is the big one
    items = ledger.heaviest(messages, 1)
    assert [item["age"] for item in items] == [1, 2, 0]
    assert items[0]["label"] == "read_file a.py" and items[0]["tokens"] > 1_900
    text = ledger.render(used=60_000, trigger=380_000, foldable=41_000, items=items[:1])
    assert "~60k of ~380k" in text and "~41k" in text and "read_file a.py ~2.0k, 1 step ago" in text


def test_off_unless_turned_on(tmp_path):
    engine, _ = _engine(tmp_path, 3)
    collect(engine)
    assert _ledgers(engine) == [] and _rows(engine) == []


def test_on_it_is_appended_spaced_and_logged_with_what_rebuilds_it(tmp_path):
    engine, provider = _engine(tmp_path, LEDGER_EVERY + 2)
    engine.context_ledger = True
    collect(engine)
    shown, rows = _ledgers(engine), _rows(engine)
    assert len(shown) == 2 and len(rows) == 2  # the first working step, then LEDGER_EVERY steps on
    assert [shown_text["content"] for shown_text in shown] == [engine.ledger_row(row) for row in rows]
    # an append: each request starts with the previous one, ledger included
    for before, after in zip(provider.requests, provider.requests[1:]):
        assert after[: len(before)] == before
    # and the invariant audit finds the ledger explained by its row
    index = _empty_index()
    _index_events(index, engine.log.read())
    assert not [v for v in audit_messages(engine.messages, index) if "ledger" in str(v)]


def test_nothing_is_shown_when_there_is_too_little_to_fold_or_no_fold_to_offer(tmp_path):
    small, _ = make_engine(tmp_path, [_step(1), DONE], messages=history(steps=2, bulk=200), window=200_000)
    _with_tools(small)
    small.context_ledger = True
    collect(small)
    no_fold, _ = _engine(tmp_path / "b", 2, fold=False)
    no_fold.context_ledger = True
    collect(no_fold)
    assert _ledgers(small) == [] and _ledgers(no_fold) == []
