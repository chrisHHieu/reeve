"""`scripts/harness_predictions.py`: a change's claim, judged by counts on each side of its ship date."""

from __future__ import annotations

import importlib.util
import json
from collections import Counter
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


predictions = _load("harness_predictions")
harness_review = _load("harness_review")


# -- the verdict -------------------------------------------------------------------------


def test_a_claim_of_never_is_refuted_by_one_and_confirmed_only_with_enough_sessions():
    assert predictions.verdict("zero", (9, 82), (1, 3), min_n=30) == "refuted"
    assert predictions.verdict("zero", (9, 82), (0, 29), min_n=30) == "too early"
    assert predictions.verdict("zero", (9, 82), (0, 30), min_n=30) == "confirmed"


def test_a_claim_that_something_starts_happening():
    assert predictions.verdict("nonzero", (0, 355), (1, 4), min_n=150) == "confirmed"
    assert predictions.verdict("nonzero", (0, 355), (0, 149), min_n=150) == "too early"
    assert predictions.verdict("nonzero", (0, 355), (0, 150), min_n=150) == "refuted"


def test_a_share_has_to_move_past_the_margin_to_count():
    # 40% -> 52%: the real case this script was written on. It went the wrong way.
    assert predictions.verdict("down", (12, 30), (24, 46), min_n=20) == "refuted"
    # 40% -> 38% is not a fall anyone has shown.
    assert predictions.verdict("down", (40, 100), (38, 100), min_n=20) == "refuted"
    assert predictions.verdict("down", (40, 100), (30, 100), min_n=20) == "confirmed"
    assert predictions.verdict("up", (10, 100), (13, 100), min_n=20) == "confirmed"
    assert predictions.verdict("up", (10, 100), (11, 100), min_n=20) == "refuted"


def test_a_share_is_not_judged_on_a_handful_or_without_a_baseline():
    assert predictions.verdict("down", (28, 34), (0, 9), min_n=10) == "too early"
    assert predictions.verdict("down", (0, 0), (3, 40), min_n=10) == "too early"


def test_an_at_risk_count_is_worse_only_past_the_margin():
    assert predictions.regressed((14, 353), (3, 9), min_n=10) == "too early"
    assert predictions.regressed((14, 353), (4, 100), min_n=10) == "held"
    assert predictions.regressed((14, 353), (9, 100), min_n=10) == "worse"
    assert predictions.regressed((0, 100), (1, 100), min_n=10) == "worse"  # it did not happen before
    assert predictions.regressed((0, 100), (0, 100), min_n=10) == "held"


# -- the ledger --------------------------------------------------------------------------


def _ledger(tmp_path: Path, entries: list[dict]) -> Path:
    path = tmp_path / "predictions.json"
    path.write_text(json.dumps(entries), encoding="utf-8")
    return path


CLAIM = {
    "id": "c", "adr": "0001", "shipped": "2026-01-10", "baseline_since": "2026-01-01",
    "metric": "bad", "per": "all", "expect": "down", "min_n": 10,
    "claim": "less of it", "evidence": "8 of 10",
}


def test_a_claim_with_a_missing_field_or_an_unknown_expectation_is_refused(tmp_path):
    with pytest.raises(ValueError, match="evidence"):
        predictions.load(_ledger(tmp_path, [{k: v for k, v in CLAIM.items() if k != "evidence"}]))
    with pytest.raises(ValueError, match="expect"):
        predictions.load(_ledger(tmp_path, [{**CLAIM, "expect": "better"}]))
    with pytest.raises(ValueError, match="twice"):
        predictions.load(_ledger(tmp_path, [CLAIM, CLAIM]))


# The ledger is this repository's own record; the public tree has none.
@pytest.mark.skipif(not predictions.LEDGER.is_file(), reason="no claims ledger in this tree")
def test_the_repositorys_own_ledger_loads_and_names_counts_the_review_produces():
    entries = predictions.load()
    assert entries, "the ledger is empty"
    # Every count a claim names is one `harness_review` can produce: a detector, or one of
    # the totals it prints. A typo here would read as a permanent "0 of 0".
    totals = {
        "steps", "turn-starts", "turn-ends", "task-calls", "todo-nudges", "wake-turns",
        "spill-locators", "interrupted-ends", "checks", "tokens re-read by steps", "shell-results", "reader-pages",
        "compaction-starts", "folds", "fold-item-hints", "fold-hints", "page-fetches",
        "workflow-runs", "tool-lists", "tool-list chars", "system chars", "progress headers",
        "progress header chars", "tool-searches", "tool-describes", "programs", "memory writes saved",
        "repairs recorded", "repairs recalled", "cuts keeping parts",
        "repairs asked for a lesson", "memory writes after a lesson was asked for",
        "failed-checks", "unnamed-failures in a whole output", "unnamed-failures cut by its pipe",
        "searched-citing answers", "visual turns", "visuals shown", "searching sessions",
        "grep calls", "grep results", "cut details",
        "question turns", "request turns", "requests-without-writes",
        "design pages", "design checks", "design first drafts with issues", "design first drafts off a phone",
        "design first drafts that misbehave when used",
        # The pages whose last check still had a control doing nothing.
        "design pages left with dead clicks",
        # A cache drop by its cause (`cache-drops <cause>`), counted beside the detector's total.
        "cache-drops request/header",
        # OpenCode Go's non-chat wires, from the store's own catalogue.
        "gateway-wire prompt tokens", "gateway-wire uncached tokens",
        # The person's messages into a running turn that say how long they waited.
        "person-steers",
        # OpenCode Go's own steps and its unexplained drops (prediction `opencode-affinity-headers`).
        "opencode-go steps", "opencode-go unexplained cache-drops",
        # The prunes and compactions with a request sent before them to price against.
        "priced cuts",
    }
    known = set(harness_review.DETECTORS) | totals
    for entry in entries:
        named = {entry["metric"], entry["per"]}
        named |= {risk[key] for risk in entry.get("at_risk") or [] for key in ("metric", "per")}
        assert named <= known, f"{entry['id']} names {sorted(named - known)}"


def test_each_claim_is_judged_on_the_two_sides_of_its_ship_date(tmp_path):
    seen: list[tuple] = []

    def review(logs, since, until):
        seen.append((since, until))
        before = until is not None
        return Counter({"bad": 8 if before else 2, "all": 10 if before else 20, "risk": 1 if before else 9}), {}

    entries = [
        {**CLAIM, "at_risk": [{"metric": "risk", "per": "all"}]},
        {**CLAIM, "id": "later", "shipped": None},
    ]
    rows = predictions.judge(entries, [], review, harness_review.stamp)
    first, second = rows
    assert first["before"] == (8, 10) and first["after"] == (2, 20)
    assert first["verdict"] == "confirmed"
    assert first["at_risk"][0]["verdict"] == "worse"  # 1/10 -> 9/20: the price is reported too
    assert second["verdict"] == "not live" and "before" not in second
    # Before = [baseline_since, shipped); after = [shipped, open). One read per window.
    shipped = harness_review.stamp("2026-01-10")
    assert seen == [(harness_review.stamp("2026-01-01"), shipped), (shipped, None)]


def test_strict_fails_only_on_a_refuted_claim_nobody_resolved(tmp_path, monkeypatch, capsys):
    logs = tmp_path / "conversations"
    logs.mkdir()
    after = harness_review.stamp("2026-01-11")
    (logs / "s.events.jsonl").write_text(
        "\n".join(
            json.dumps({"seq": i, "type": "turn/start", "turn": i + 1, "t": after + i})
            for i in range(3)
        ),
        encoding="utf-8",
    )
    # Three turns since it shipped and no goal was set: enough to say it did not happen.
    refuted = {**CLAIM, "metric": "goal-set", "per": "turn-starts", "expect": "nonzero", "min_n": 3}
    path = _ledger(tmp_path, [refuted])
    assert predictions.main([str(tmp_path), "--ledger", str(path), "--strict"]) == 1
    assert "REFUTED" in capsys.readouterr().out
    path = _ledger(tmp_path, [{**refuted, "resolution": "reverted in 1234abc"}])
    assert predictions.main([str(tmp_path), "--ledger", str(path), "--strict"]) == 0
    assert "resolution: reverted in 1234abc" in capsys.readouterr().out


def test_a_claim_is_confounded_by_another_on_its_metric_that_shipped_inside_its_window():
    """Two changes that move one count, live in one window, cannot be told apart by the store; the
    verdict says so instead of crediting each with the whole move (Harness²'s `confounded`)."""

    def review(logs, since, until):
        return Counter({"bad": 5, "all": 20, "other": 1}), {}

    entries = [
        CLAIM,
        {**CLAIM, "id": "same-day-before", "shipped": "2026-01-09T12:00:00"},
        {**CLAIM, "id": "inside", "shipped": "2026-01-20"},
        {**CLAIM, "id": "long-before", "shipped": "2026-01-02"},
        {**CLAIM, "id": "other-metric", "metric": "other", "shipped": "2026-01-15"},
        {**CLAIM, "id": "not-live", "shipped": None},
        {**CLAIM, "id": "ended", "shipped": "2026-01-03", "until": "2026-01-08"},
    ]
    rows = {row["id"]: row for row in predictions.judge(entries, [], review, harness_review.stamp)}
    assert rows["c"]["confounded_by"] == ["same-day-before", "inside"]
    # A claim that ended before another shipped is not confounded by it; one a day before it is.
    assert rows["ended"]["confounded_by"] == ["long-before"]
