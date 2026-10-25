"""The layered security corpora are well formed, big enough and varied enough
(`scripts/validate_layered_corpora.py`).

The floors were lowered when the browser tools were retired (2026-09-15), and again when the rows for
connectors Reeve no longer has were taken out (2026-10-25): their rows left with them.
A floor is there to catch the corpus shrinking by accident, so it follows what remains rather than a
fixed number.
"""

from __future__ import annotations

import pytest

from scripts.validate_layered_corpora import validate_all

GATE, REVIEWER, SEQUENCES = "permission_gate.jsonl", "reviewer_actions.jsonl", "action_sequences.jsonl"


@pytest.fixture(scope="module")
def summary():
    return validate_all()


def test_every_corpus_has_at_least_its_floor_of_rows(summary):
    for corpus, floor in ((GATE, 104), (REVIEWER, 98), (SEQUENCES, 47)):
        assert summary[corpus]["rows"] >= floor, corpus
    assert summary["total"] >= 250


def test_every_decision_class_is_there(summary):
    assert set(summary[GATE]["labels"]) == {"allow_without_reviewer", "reviewer_eligible", "human_only", "hard_deny"}
    for corpus in (REVIEWER, SEQUENCES):
        assert set(summary[corpus]["labels"]) == {"allow", "ask", "deny"}, corpus


def test_the_rows_cover_many_tools_and_tags(summary):
    # the retired tools took their distinct names with them; these floors follow the rest, as above
    for corpus, tools, tags in ((GATE, 28, 60), (REVIEWER, 31, 60), (SEQUENCES, 22, 39)):
        assert summary[corpus]["tools"] >= tools and summary[corpus]["tags"] >= tags, corpus


def test_no_row_names_a_tool_the_product_no_longer_has(summary):
    """The rows for connectors that left the product were taken out with them; a new row must name a
    tool Reeve has."""
    for corpus in (GATE, REVIEWER, SEQUENCES):
        assert not summary[corpus]["retired_tools"], corpus
