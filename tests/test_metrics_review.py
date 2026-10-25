"""The Metrics page's self-review and its harness numbers (2026-10-14).

The review's own counting is `scripts/harness_review.py`'s (tested in test_harness_review.py); these
pin what the page gets: the shapes as shares of their bases, the claims' verdicts, the ship days,
a run that happens in a child process and never twice at once, a failed run that keeps the last
result, and the per-model numbers beside the tokens. No eval is on the page any more.
"""

from __future__ import annotations

import json
import sys
import threading

import pytest

from harness.interface.server.manager.metrics_by_model import harness_between, with_harness
from harness.interface.server.manager.metrics_review import SelfReview
from harness.interface.server.manager.metrics_store import MetricsStore
from harness.observability import self_review


def _log(tmp_path, events):
    conv = tmp_path / "conversations"
    conv.mkdir(parents=True, exist_ok=True)
    path = conv / "s1.events.jsonl"
    path.write_text("\n".join(json.dumps({"seq": i, **e}) for i, e in enumerate(events)), encoding="utf-8")
    return path


def test_the_review_gives_each_shape_as_a_share_of_what_it_is_counted_against(tmp_path):
    import time

    now = time.time()
    _log(tmp_path, [
        {"type": "turn/start", "t": now - 60, "turn": 1},
        {"type": "turn/end", "t": now - 50, "turn": 1, "reason": "completed_unverified"},
        {"type": "turn/start", "t": now - 40, "turn": 2},
        {"type": "turn/end", "t": now - 30, "turn": 2, "reason": "completed"},
    ])
    out = self_review.run(tmp_path, now=now)
    assert "error" not in out and out["days"] == self_review.REVIEW_DAYS
    (shape,) = [s for s in out["shapes"] if s["name"] == "unverified-ends"]
    assert shape == {"name": "unverified-ends", "owner": "engine/verify gate", "count": 1,
                     "per": "turn-ends", "of": 2, "share": 0.5}


def _with_ledger(monkeypatch, ledger):
    found = self_review.sources()
    monkeypatch.setattr(self_review, "sources", lambda: {**found, "ledger": ledger})


def test_every_claim_in_the_ledger_is_judged(tmp_path, monkeypatch):
    claim = {"claim": "fewer unverified ends", "evidence": "a test", "metric": "unverified-ends", "per": "turn-ends",
             "expect": "down", "baseline_since": "2026-09-01", "min_n": 1}
    ledger = tmp_path / "predictions.json"
    ledger.write_text(json.dumps([{**claim, "id": "a", "shipped": "2026-10-01"}, {**claim, "id": "b", "shipped": ""}]), encoding="utf-8")
    _with_ledger(monkeypatch, ledger)
    out = self_review.run(tmp_path)
    assert sum(out["claims"]["verdicts"].values()) == 2


def test_a_tree_without_the_ledger_still_counts_the_shapes(tmp_path, monkeypatch):
    import time

    now = time.time()
    _log(tmp_path, [
        {"type": "turn/start", "t": now - 60, "turn": 1},
        {"type": "turn/end", "t": now - 50, "turn": 1, "reason": "completed_unverified"},
    ])
    _with_ledger(monkeypatch, None)
    out = self_review.run(tmp_path, now=now)
    assert "error" not in out and [s["name"] for s in out["shapes"]] == ["unverified-ends"]
    assert out["claims"] == {"verdicts": {}, "refuted": []}


def test_the_ship_days_come_from_the_ledger(tmp_path):
    ledger = tmp_path / "predictions.json"
    ledger.write_text(json.dumps([
        {"id": "a", "adr": "0102", "shipped": "2026-10-04T10:00:00+07:00"},
        {"id": "b", "adr": "0097", "shipped": "2026-10-04T12:00:00+07:00"},
        {"id": "c", "adr": "0106", "shipped": "2026-10-05"},
        {"id": "d", "adr": "0184", "shipped": ""},
    ]), encoding="utf-8")
    assert self_review.ships(ledger) == [
        {"day": "2026-10-04", "adrs": ["0097", "0102"]},
        {"day": "2026-10-05", "adrs": ["0106"]},
    ]
    assert self_review.ships(None) == [] and self_review.ships(tmp_path / "missing.json") == []


def test_the_child_prints_one_object(tmp_path, capsys):
    assert self_review.main([str(tmp_path)]) == 0
    printed = capsys.readouterr().out.strip().splitlines()
    assert len(printed) == 1 and "shapes" in json.loads(printed[0])


def test_a_frozen_build_starts_the_review_from_its_own_program(tmp_path, capsys):
    from harness.interface.server import run

    with pytest.raises(SystemExit) as done:
        run.main(["--self-review", str(tmp_path)])
    assert done.value.code == 0 and "claims" in json.loads(capsys.readouterr().out)


def _quick(monkeypatch, body):
    """A child that answers at once, in place of the minutes-long review."""
    from harness.interface.server.manager import metrics_review

    monkeypatch.setattr(metrics_review, "_command", lambda state: [sys.executable, "-c", body])


def test_a_stale_result_starts_one_run_in_a_child_and_tells_the_page(tmp_path, monkeypatch):
    monkeypatch.setenv("REEVE_SELF_REVIEW", "on")
    _quick(monkeypatch, "import json; print(json.dumps({'at': 1.0, 'shapes': [], 'claims': {}}))")
    told = threading.Event()
    review = SelfReview(tmp_path, on_done=told.set)

    first = review.payload()  # nothing yet: a run starts, and the page gets no result meanwhile
    assert first == {"running": True}
    assert review.start() is False  # never two at once
    assert told.wait(30)
    kept = review.read()
    assert kept["shapes"] == [] and kept["tried_at"] > 0 and "error" not in kept
    assert review.payload()["running"] is False  # fresh: no second run


def test_a_failed_run_keeps_the_last_result_and_waits_like_a_refresh(tmp_path, monkeypatch):
    monkeypatch.setenv("REEVE_SELF_REVIEW", "on")
    (tmp_path / "metrics-review.json").write_text(json.dumps({"at": 1.0, "shapes": [{"name": "x"}]}), encoding="utf-8")
    _quick(monkeypatch, "import sys; sys.exit(3)")
    told = threading.Event()
    review = SelfReview(tmp_path, on_done=told.set)
    assert review.start() is True
    assert told.wait(30)
    kept = review.read()
    assert kept["shapes"] == [{"name": "x"}] and kept["error"] == "exit 3"
    # The failure stamped `tried_at`, so the next request does not start another run.
    assert review.payload()["running"] is False


def test_runs_can_be_turned_off(tmp_path, monkeypatch):
    monkeypatch.setenv("REEVE_SELF_REVIEW", "off")
    review = SelfReview(tmp_path)
    assert review.start() is False and review.payload() == {"running": False}


def _session(store, sid, day, model, **numbers):
    row = {"day": day, "model": model, "turns_started": 0, "turns_completed": 0, "turns_failed": 0,
           "tool_calls": 0, "tool_errors": 0, "steps": 0, **numbers}
    store.upsert(sid, row=row, turns=[], log_mtime=1.0)


def test_each_model_gets_its_own_turns_and_tools_beside_its_tokens(tmp_path):
    store = MetricsStore(tmp_path / "metrics.db")
    try:
        _session(store, "a", "2026-10-10", "p:fast", turns_started=10, turns_completed=10, turns_failed=1,
                 tool_calls=40, tool_errors=2, steps=50, tokens_in=1000, cache_read=9000)
        _session(store, "b", "2026-10-11", "p:fast", turns_started=10, turns_completed=9, steps=30)
        _session(store, "c", "2026-10-02", "p:slow", turns_started=4, turns_completed=4, steps=8)
        rows = with_harness([{"name": "p:fast", "tokens": 5}, {"name": "p:other", "tokens": 1}], store._sessions(None))
        fast = rows[0]["harness"]
        assert fast["turns"] == 20 and fast["completion"] == 0.95 and fast["tool_success"] == 0.95
        assert fast["steps_per_turn"] == 4.0 and rows[0]["tokens"] == 5
        assert "harness" not in rows[1]  # a model no session ran on keeps its tokens only
        # The span before, for the tiles' changes: only the sessions of those days.
        before = harness_between(store._conn, store._lock, "2026-10-01", "2026-10-10")
        assert before["turns"] == 4 and before["steps_per_turn"] == 2.0
    finally:
        store.close()


def test_the_payload_has_no_evals_and_carries_the_review_and_the_ship_days(tmp_path):
    from harness.interface.server.manager import SessionManager

    manager = SessionManager(workspace=None, data_dir=tmp_path)
    payload = manager.ops_metrics("30d")
    assert "evals" not in payload and "versions" not in payload
    assert payload["review"] == {"running": False}  # runs are off in tests; no result yet
    assert isinstance(payload["ships"], list) and "harness" in payload["previous"]
    assert all(row["purpose"] != "eval" for row in payload["background"])
