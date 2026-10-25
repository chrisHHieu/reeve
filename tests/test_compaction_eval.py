"""Plumbing for the compaction recall eval (`scripts/eval_compaction.py`).

The eval itself needs a live model and money, so it is not a pytest. What is covered here
is everything that can be wrong WITHOUT a model: that the scenario plants each fact where
it says it does, that each policy really produces the view it claims, that scoring reads a
right answer as right and a refusal as wrong, and that the report has a row per policy.

The failure mode this guards against is the one that makes an eval worthless rather than
merely wrong: a fact that was never planted, or planted outside the region under test, so
every policy scores 100% and the table proves nothing.
"""

from __future__ import annotations

import asyncio

import pytest

from scripts import eval_compaction as E


class _Answering:
    """A model that answers from the context it was given.

    It answers the question it was ASKED, which is the property a naiver stub gets wrong:
    quoting any token it can see makes `none` score below 100%, and the ceiling stops being
    a ceiling — so the harness looks like it measures loss when it measures its own stub.
    """

    def __init__(self, *, always_refuse=False):
        self.always_refuse = always_refuse
        self.prompts: list[str] = []

    def complete(self, *, model, messages, tools=None, **settings):
        from harness.llm.providers import AssistantTurn

        prompt = "\n".join(str(m.get("content", "")) for m in messages)
        self.prompts.append(prompt)
        # The summarizer role must satisfy the same section contract a live model does, or
        # `build_state`'s structure check (and its corrective re-ask) turns this stub into
        # the failure under test.
        if "Produce ALL of the following sections" in prompt:
            return E._StubSummarizer().complete(
                model=model, messages=messages, tools=tools, **settings
            )
        if self.always_refuse or "CONTEXT:" not in prompt or "QUESTION:" not in prompt:
            return AssistantTurn(text="NOT_IN_CONTEXT", finish_reason="stop")
        asked = prompt.split("QUESTION:", 1)[1]
        for fact in E.default_facts():
            if fact.question in asked:
                token = fact.token if fact.token in prompt else "NOT_IN_CONTEXT"
                return AssistantTurn(text=f"It was {token}.", finish_reason="stop")
        return AssistantTurn(text="NOT_IN_CONTEXT", finish_reason="stop")

    def capabilities(self, model):
        from harness.llm.providers import ModelCapabilities

        return ModelCapabilities()


# -- the scenario ------------------------------------------------------------------


def test_every_fact_is_planted_in_the_conversation():
    """An unplanted fact scores zero for every policy and reads as "compaction lost it"."""
    scenario = E.build_scenario("t")
    joined = " ".join(str(m.get("content", "")) for m in scenario.messages)
    for fact in scenario.facts:
        assert fact.token in joined, fact.token


def test_the_old_and_middle_facts_sit_inside_the_region_that_gets_summarized():
    """The measurement is only meaningful if the facts are in the part a policy may drop.
    Otherwise the table shows the verbatim tail working and nothing else."""
    scenario = E.build_scenario("t")
    span = " ".join(str(m.get("content", "")) for m in scenario.messages[: scenario.boundary])
    for fact in scenario.facts:
        if fact.depth in ("old", "middle"):
            assert fact.token in span, f"{fact.depth} fact is not inside the span"
        if fact.depth == "recent":
            assert fact.token not in span, "the recent fact must be tail-only"


def test_both_old_facts_are_planted_and_not_collapsed_into_one():
    """A dict keyed by depth silently keeps only the last fact per depth, which is how this
    generator was wrong the first time: the table would have had a `old` column measuring
    one fact while the scenario declared two."""
    scenario = E.build_scenario("t")
    old = [f for f in scenario.facts if f.depth == "old"]
    assert len(old) == 2
    joined = " ".join(str(m.get("content", "")) for m in scenario.messages)
    assert all(f.token in joined for f in old)


def test_the_scenario_is_long_enough_for_the_question_to_be_real():
    from harness.context import compaction as C

    scenario = E.build_scenario("t")
    assert C.estimate_tokens(scenario.messages) > 20_000
    assert 0 < scenario.boundary < len(scenario.messages)


# -- the policies ------------------------------------------------------------------


def test_each_policy_produces_the_view_it_claims():
    scenario = E.build_scenario("t")
    provider = _Answering()

    full, calls = E.apply_policy(scenario, "none", provider, "stub:model")
    assert full == scenario.messages and calls == 0

    small, _ = E.apply_policy(scenario, "tail10k", provider, "stub:model")
    bigger, _ = E.apply_policy(scenario, "tail25k", provider, "stub:model")
    from harness.context import compaction as C

    assert C.estimate_tokens(small) < C.estimate_tokens(bigger)
    assert C.estimate_tokens(bigger) < C.estimate_tokens(scenario.messages)


def test_prune_only_keeps_the_whole_conversation_length():
    """Pruning rewrites content, it does not drop messages — so the fact count is unchanged
    and only the bulk is gone. That distinction is the reason `prune-only` is a column of
    its own rather than a variant of `tail`."""
    scenario = E.build_scenario("t")
    view, calls = E.apply_policy(scenario, "prune-only", _Answering(), "stub:model")
    assert calls == 0, "the cheap lever never calls a model"
    assert len(view) == len(scenario.messages)


def test_the_summarize_policy_calls_the_summarizer_exactly_once():
    scenario = E.build_scenario("t")
    view, calls = E.apply_policy(scenario, "current", _Answering(), "stub:model")
    assert calls == 1
    from harness.context import compaction as C

    assert C.estimate_tokens(view) < C.estimate_tokens(scenario.messages)


# -- scoring -----------------------------------------------------------------------


def test_an_answer_carrying_the_fact_scores_as_recalled():
    fact = E.Fact(depth="old", token="src/ledger.py", question="q", what="a path")
    assert E.score(fact, "the file was src/ledger.py")
    assert E.score(fact, "It was SRC/LEDGER.PY."), "case must not decide the score"


def test_a_refusal_scores_as_lost():
    """`NOT_IN_CONTEXT` is the honest answer when the policy dropped the fact, and it must
    read as a miss rather than as a non-answer to be ignored."""
    fact = E.Fact(depth="old", token="src/ledger.py", question="q", what="a path")
    assert not E.score(fact, "NOT_IN_CONTEXT")
    assert not E.score(fact, "")


def test_a_policy_that_kept_everything_scores_full_recall():
    """The ceiling, and the sanity check that the harness can produce a 100%: if even
    `none` cannot score full marks, the questions are unanswerable and the table is noise."""
    scenario = E.build_scenario("t")
    result = asyncio.run(E.run_policy(scenario, "none", _Answering(), "stub:model"))
    assert result.overall == 1.0
    assert all(result.recall[depth] == 1.0 for depth in E.DEPTHS)


def test_a_policy_that_dropped_everything_scores_zero_recall():
    scenario = E.build_scenario("t")
    result = asyncio.run(
        E.run_policy(scenario, "none", _Answering(always_refuse=True), "stub:model")
    )
    assert result.overall == 0.0


def test_recall_is_reported_per_depth_as_a_rate():
    scenario = E.build_scenario("t")
    result = asyncio.run(E.run_policy(scenario, "tail10k", _Answering(), "stub:model"))
    for depth in E.DEPTHS:
        assert 0.0 <= result.recall[depth] <= 1.0
    # `recent` is in the verbatim tail of every keeping policy, so it must survive — this is
    # the assertion that would catch a tail policy that did not actually keep the tail.
    assert result.recall["recent"] == 1.0


def test_the_result_reports_what_it_saved():
    scenario = E.build_scenario("t")
    result = asyncio.run(E.run_policy(scenario, "tail10k", _Answering(), "stub:model"))
    assert result.tokens_before > result.tokens_after > 0
    assert 0 < result.saved_pct < 1


# -- the report --------------------------------------------------------------------


def test_the_report_has_a_row_per_policy_and_the_columns_that_matter():
    results = [
        asyncio.run(E.run_policy(E.build_scenario("t"), name, _Answering(), "stub:model"))
        for name in ("none", "tail10k")
    ]
    report = E.format_report(results, "stub:model", "2026-09-10")
    for name in ("none", "tail10k"):
        assert f"`{name}`" in report
    for column in ("recall (all)", "old", "middle", "recent", "tokens kept", "summary calls"):
        assert column in report
    assert "2026-09-10" in report
    assert "stub:model" in report


def test_an_unknown_policy_is_a_usage_error_not_a_crash():
    assert E.main(["--model", "stub:model", "--policies", "nope"]) == 2


def test_the_stub_run_says_it_is_not_evidence(tmp_path, capsys):
    """A stub run produces a table that looks exactly like a real one. The report has to say
    which it is, because the file gets committed next to real ones."""
    out = tmp_path / "report.md"
    assert E.main(["--model", "stub:model", "--stub", "--out", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    assert "STUB RUN" in text
    assert "not evidence" in text


@pytest.mark.parametrize("policy", sorted(E.POLICIES))
def test_every_declared_policy_can_actually_run(policy):
    """A policy in the matrix that raises takes the whole report with it."""
    scenario = E.build_scenario("t")
    view, _ = E.apply_policy(scenario, policy, _Answering(), "stub:model")
    assert view, policy
