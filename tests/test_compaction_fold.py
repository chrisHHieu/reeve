"""The fold tier of the prune pass — Self-GC's move, engine-side and locator-gated.

An aged tool result that carries a spill locator folds to a stub (first line + path)
instead of keeping its head and tail in every outbound request. A result with no
locator keeps the old retention behavior: a fold without a recovery path would be a
deletion wearing a costume."""

from __future__ import annotations

from harness.context.compaction import FOLD_RESULT_BUDGET, PRUNE_RESULT_BUDGET, _fold_stub, _spill_locator, count_fold_results, prune_tool_results


def test_a_locator_less_result_gains_a_reading_path_when_spilled(tmp_path):
    """Without a sink the middle of a large locator-less result was cut with no way to
    read it back (the model view lost it; `search_history` excludes tool roles)."""
    from harness.tools.spill import SpillStore

    store = SpillStore(tmp_path / "spill")
    big = "line of output\n" * 1_000
    messages = _tool_messages(big)

    pruned = prune_tool_results(messages, 3, spill=store.sink("pruned"))

    text = pruned[2]["content"]
    spilled = list((tmp_path / "spill").glob("pruned-*.log"))
    assert len(spilled) == 1
    assert spilled[0].read_text(encoding="utf-8") == big
    assert "the complete output is at" in text
    assert str(spilled[0]) in text

    # A caller that stays pure (measurement, tests) keeps the old behavior: text only.
    assert "the complete output is at" not in prune_tool_results(messages, 3)[2]["content"]


def _marker(path: str) -> str:
    return (
        "\n… 1,204 lines (45.2 KB) omitted from the middle — "
        f"the complete output is at {path} …\n"
    )


def _big_result(text: str, call_id: str = "call_1") -> dict:
    return {"role": "tool", "tool_call_id": call_id, "content": text}


def _tool_messages(text: str) -> list[dict]:
    return [
        {"role": "user", "content": "build it"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "call_1", "type": "function",
             "function": {"name": "run_shell", "arguments": "{}"}},
        ]},
        _big_result(text),
    ]


def test_aged_result_with_locator_folds_to_a_stub():
    path = r"C:\Users\sam\Reeve\.reeve\output\shell-3.log"
    text = "BUILD LOG first line here\n" + ("x" * 3000) + _marker(path) + "tail details"
    messages = _tool_messages(text)

    out = prune_tool_results(messages, upto=3)

    folded = out[2]["content"]
    assert "BUILD LOG first line here" in folded  # the identifying head survives
    assert path in folded  # the recovery path survives
    assert "read_file" in folded  # and it says HOW to come back
    assert len(folded) < 600  # vs 5,000+ that retention would have kept
    # The canonical input is never touched.
    assert messages[2]["content"] == text


def test_aged_result_without_a_locator_keeps_plain_retention():
    body = "line\n" * (FOLD_RESULT_BUDGET * 2)
    messages = _tool_messages(body)

    out = prune_tool_results(messages, upto=3)

    kept = out[2]["content"]
    assert "folded" not in kept  # nothing to recover — no fold
    assert len(kept) < len(body)  # but retention still bounded it
    assert len(kept) <= PRUNE_RESULT_BUDGET + 200


def test_small_result_is_neither_folded_nor_retained():
    messages = _tool_messages("short and complete")
    out = prune_tool_results(messages, upto=3)
    assert out[2]["content"] == "short and complete"


def test_fold_is_idempotent_across_passes():
    text = "HEAD\n" + ("x" * 3000) + _marker("/w/.reeve/output/shell-1.log") + "tail"
    messages = _tool_messages(text)
    once = prune_tool_results(messages, upto=3)
    twice = prune_tool_results(once, upto=3)
    assert once[2]["content"] == twice[2]["content"]


def test_spill_locator_extracts_both_path_styles():
    assert _spill_locator(_marker(r"C:\w\.reeve\output\shell-1.log")) == (
        r"C:\w\.reeve\output\shell-1.log"
    )
    assert _spill_locator(_marker("/home/u/.reeve/output/build-2.log")) == (
        "/home/u/.reeve/output/build-2.log"
    )
    assert _spill_locator("no marker here at all") is None


def test_fold_stub_announces_its_sizes():
    stub = _fold_stub("first line\nsecond line", "/w/out.log")
    assert stub.startswith("first line\n")
    assert "2 lines" in stub and "/w/out.log" in stub


def test_count_fold_results_counts_only_the_qualified_range():
    good = _big_result("HEAD\n" + ("x" * 3000) + _marker("/w/a.log") + "tail", "c1")
    nolocator = _big_result("x" * (FOLD_RESULT_BUDGET * 2), "c2")
    small = _big_result("tiny", "c3")
    messages = [good, nolocator, small]
    assert count_fold_results(messages, 0, 3) == 1
    assert count_fold_results(messages, 1, 3) == 0
