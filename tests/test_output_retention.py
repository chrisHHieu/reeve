"""Bounded tool output that names its own gap (harness/tools/retention.py).

Nothing here is about making output smaller — every tool already capped what it
returned. It is about three honesty problems in how they capped it:

1. **Each site kept one end.** Shell kept the tail ("builds put the verdict at the
   end"), `web_fetch` kept the head. Which end matters is a property of the content,
   so keeping both and dropping the middle is the only choice that is not a guess.
2. **The gap was invisible.** `truncated: True` says something is missing but not
   where or how much, and a model that cannot see the size of the hole reasons as if
   the text were whole — which is how a truncated log becomes a confident wrong answer.
3. **The budget has to mean something.** A caller asking for at most N characters is
   making a context-window promise. The marker comes OUT of the budget, never on top
   of it; the same reasoning that permits 45 extra characters permits a megabyte later.
"""

from __future__ import annotations

import random

import pytest

from harness.tools.retention import Retained, retain


def _lines(n, prefix="line"):
    return "\n".join(f"{prefix}{i}" for i in range(1, n + 1))


# -- text that fits is left completely alone ------------------------------------


def test_text_within_budget_is_returned_untouched():
    kept = retain("hello", budget=20)
    assert kept.text == "hello"
    assert kept.truncated is False
    assert (kept.omitted_chars, kept.omitted_lines, kept.omitted_bytes) == (0, 0, 0)


def test_text_exactly_at_budget_is_not_marked():
    """A bound that announces itself when it dropped nothing trains a model to
    distrust the announcement."""
    text = "x" * 20
    assert retain(text, budget=20).text == text


# -- the budget is a promise ----------------------------------------------------


@pytest.mark.parametrize("budget", [0, 1, 20, 50, 120, 200, 1_000, 20_000])
def test_the_result_never_exceeds_the_budget(budget):
    assert len(retain(_lines(1000), budget=budget).text) <= budget


def test_the_budget_holds_across_random_inputs():
    """The marker's length depends on the gap and the gap depends on the marker's
    length, so the split is solved by refinement — this is the property that
    refinement has to guarantee no matter where it settles."""
    random.seed(7)
    for _ in range(400):
        text = "".join(
            random.choice("ab\n é日") for _ in range(random.randint(0, 400))
        )
        budget = random.randint(0, 300)
        share = random.choice([0.0, 0.35, 0.7, 1.0])
        kept = retain(text, budget=budget, head_share=share)
        assert len(kept.text) <= budget


def test_a_budget_too_small_for_the_marker_says_so_rather_than_lying():
    kept = retain(_lines(500), budget=5)
    assert len(kept.text) <= 5
    assert kept.truncated is True
    assert kept.omitted_chars == len(_lines(500))


# -- both ends survive ----------------------------------------------------------


def test_both_ends_survive_and_the_middle_names_itself():
    kept = retain(_lines(1000), budget=200)
    assert kept.text.startswith("line1")
    assert kept.text.rstrip().endswith("line1000")
    assert "omitted from the middle" in kept.text
    assert kept.truncated is True


def test_the_marker_reports_the_real_counts():
    kept = retain(_lines(1000), budget=200)
    assert kept.omitted_lines > 0
    assert f"{kept.omitted_lines:,}" in kept.text
    # The accounting describes the gap, not the whole text.
    assert kept.omitted_chars == len(_lines(1000)) - (
        len(kept.text) - kept.text.count("…") * 0 - _marker_len(kept)
    )


def _marker_len(kept: Retained) -> int:
    start = kept.text.index("\n… ")
    end = kept.text.index(" …\n") + len(" …\n")
    return end - start


def test_a_head_only_split_still_marks_the_gap():
    kept = retain(_lines(500), budget=200, head_share=1.0)
    assert kept.text.startswith("line1")
    assert kept.truncated is True


def test_a_tail_only_split_still_marks_the_gap():
    kept = retain(_lines(500), budget=200, head_share=0.0)
    assert kept.text.rstrip().endswith("line500")
    assert kept.truncated is True


def test_the_tail_gets_the_larger_share_by_default():
    """An exit status, a stack trace's last frame and a test summary all land at the
    end; the head usually only needs enough to identify what ran."""
    kept = retain(_lines(1000), budget=400)
    head, tail = kept.text.split("omitted from the middle …\n")
    assert len(tail) > len(head)


# -- sizes -----------------------------------------------------------------------


def test_bytes_and_characters_are_counted_separately():
    """Deliberately character-based — Python slices by code point, so a byte-oriented
    retainer would be machinery for a problem this language does not have. Sizes are
    still reported in bytes because that is the unit a person recognises."""
    kept = retain("é" * 1000, budget=100)
    assert kept.omitted_bytes == kept.omitted_chars * 2


def test_multibyte_text_is_never_split_into_mojibake():
    kept = retain("日本語" * 500, budget=120)
    kept.text.encode("utf-8")  # would raise on a broken surrogate
    assert "�" not in kept.text


@pytest.mark.parametrize("size,unit", [(900, " B"), (5_000, " KB"), (5_000_000, " MB")])
def test_the_marker_scales_its_unit(size, unit):
    assert unit in retain("x" * size, budget=200).text


def test_a_single_omitted_line_reads_as_singular():
    # One newline, far from both ends, so it can only fall inside the gap.
    kept = retain("H" * 1000 + "\n" + "T" * 1000, budget=200)
    assert kept.omitted_lines == 1
    assert "1 line (" in kept.text


def test_a_gap_with_no_newline_is_reported_in_characters():
    """Counting lines would say "0 lines omitted" — technically true and completely
    useless for a minified file or a single long line."""
    kept = retain("x" * 1000, budget=200)
    assert kept.omitted_lines == 0
    assert "chars" in kept.text


# -- validation ------------------------------------------------------------------


def test_a_negative_budget_is_refused():
    with pytest.raises(ValueError):
        retain("x", budget=-1)


@pytest.mark.parametrize("bad", [-0.1, 1.1])
def test_a_share_outside_zero_to_one_is_refused(bad):
    with pytest.raises(ValueError):
        retain("x", budget=10, head_share=bad)


def test_retained_is_immutable():
    with pytest.raises(Exception):
        Retained(text="x").text = "y"  # type: ignore[misc]


# -- the two call sites ----------------------------------------------------------


def test_shell_result_keeps_both_ends_and_reports_the_gap():
    from harness.tools.shell import LocalExecutor

    executor = LocalExecutor.__new__(LocalExecutor)  # no shell process needed
    executor.cwd = "."
    executor.max_output_chars = 200

    kept = retain(_lines(400), budget=executor.max_output_chars)
    result = executor._result(
        "cmd",
        0,
        kept.text,
        timed_out=False,
        truncated=kept.truncated,
        omitted_lines=kept.omitted_lines,
    )

    assert result["truncated"] is True
    assert result["omitted_lines"] == kept.omitted_lines
    assert result["output"].startswith("line1")
    assert result["output"].rstrip().endswith("line400")
    assert len(result["output"]) <= 200


def test_shell_result_omits_the_count_when_nothing_was_dropped():
    from harness.tools.shell import LocalExecutor

    executor = LocalExecutor.__new__(LocalExecutor)
    executor.cwd = "."
    result = executor._result("cmd", 0, "short", timed_out=False, truncated=False)
    assert "omitted_lines" not in result


def test_the_run_shell_card_says_how_much_went_missing():
    from harness.tools.shell import RUN_SHELL_CONTRACT

    shown = RUN_SHELL_CONTRACT.present_result(
        {}, {"exit_code": 0, "output": "a\nb", "truncated": True, "omitted_lines": 1203}
    )
    assert "1,203 omitted" in shown.detail


def test_web_fetch_keeps_the_conclusion_of_a_long_page():
    """`text[:cap]` dropped the end of every long article — which is where the
    conclusion is, and what a summary most needs. The head keeps the larger share
    here: a page's subject is stated up front."""
    kept = retain(_lines(400), budget=200, head_share=0.7)
    assert kept.text.rstrip().endswith("line400")
    assert kept.text.startswith("line1")
    head, tail = kept.text.split("omitted from the middle …\n")
    assert len(head) > len(tail)


# -- one cut in both dimensions --------------------------------------------------
#
# Real session, 2026-10-05: `seq 1 3000` through the shell kept 20,000 characters, which was
# 2,075 lines; the engine's 2,000-line ceiling cut 106 more, and the model read three
# statements about one output. A wall of short lines fits any character budget.


def test_a_wall_of_short_lines_is_held_to_the_line_bound_in_the_same_cut():
    from harness.tools.retention import BODY_MAX_LINES, RESULT_CEILING_LINES

    text = "".join(f"LINE-{i}\n" for i in range(1, 3001))
    kept = retain(text, budget=20_000)
    assert len(kept.text) <= 20_000
    assert kept.text.count("\n") + 1 <= BODY_MAX_LINES < RESULT_CEILING_LINES
    assert kept.text.startswith("LINE-1\n") and kept.text.endswith("LINE-3000\n")
    assert kept.text.count("omitted from the middle") == 1
    # What is kept is whole lines, and the count of what is not adds up.
    shown = [line for line in kept.text.splitlines() if line.startswith("LINE-")]
    assert len(shown) + kept.omitted_lines == 3000


def test_lines_alone_can_be_what_is_cut():
    text = "a\n" * 5_000  # 10,000 characters: inside any budget, far past the line bound
    kept = retain(text, budget=1_000_000, max_lines=100)
    assert kept.truncated and kept.text.count("\n") + 1 <= 100
    assert kept.omitted_lines > 4_800


def test_a_text_inside_both_bounds_is_untouched_and_the_line_bound_can_be_lifted():
    text = "a\n" * 500
    assert retain(text, budget=10_000).text == text
    long = "a\n" * 5_000
    assert retain(long, budget=1_000_000, max_lines=None).text == long
