"""The tool-card vocabulary: what a surface draws, and the rules that keep it honest.

`harness/tools/cards.py` is a second presentation vocabulary beside `ToolPresentation`'s
sentence. These tests hold the three properties that make it safe to put on a wire:

1. a card is validated at construction, so a presenter that builds one wrongly fails where a
   blank summary would — loudly, and inside the `safe_*` wrapper that degrades the row;
2. the builders refuse a card that would render as a lie (a diff with no hunks, a terminal
   with no command, a read whose line numbers do not ascend);
3. the counts a diff carries agree with the receipt's, because a card that disagrees with the
   number beside it on the same screen is worse than either.
"""

from __future__ import annotations

import pytest

from harness.tools import cards
from harness.tools.contract import ToolContract, ToolPresentation, safe_present_result


class TestFileDiff:
    def test_keeps_a_creation_distinguishable_from_a_removal(self):
        created = cards.file_diff("a.py", None, "one\n")
        assert created["old_text"] is None
        assert created["new_text"] == "one\n"

    def test_carries_the_engine_counts_when_given_them(self):
        hunk = cards.file_diff("a.py", "old", "new", added=1, removed=1)
        assert (hunk["added"], hunk["removed"]) == (1, 1)

    def test_omits_them_when_not_given(self):
        # A literal fragment's sides count for themselves, and inventing numbers for a
        # context-windowed hunk is how a card reports `+7 −7` for a one-line edit.
        hunk = cards.file_diff("a.py", "old", "new")
        assert "added" not in hunk and "removed" not in hunk

    def test_refuses_a_hunk_with_nothing_to_show(self):
        with pytest.raises(ValueError):
            cards.file_diff("", "old", "new")
        with pytest.raises(ValueError):
            cards.file_diff("a.py", 7, "new")
        with pytest.raises(ValueError):
            cards.file_diff("a.py", None, None)


class TestTerminalCard:
    def test_separates_the_exit_status_from_the_output(self):
        # The card draws the status as a pill, so it must not be a marker line inside the
        # text — a surface would have to parse back out what the engine already knew.
        card = cards.terminal_result(title="ls", output="a\nb\n", exit_code=2)
        assert card["exit_code"] == 2
        assert "exit code" not in card["output"]

    def test_a_call_card_refuses_to_be_built_with_no_command_to_head_it(self):
        # A call card exists to show WHAT is about to run. With no command it is a prompt line
        # with nothing after the `$`.
        with pytest.raises(ValueError):
            cards.terminal_call("")

    def test_a_result_card_may_defer_its_title_to_the_pending_one(self):
        # "Omit to keep the pending-state title" — a result that repeats the command would be
        # saying it twice, and the surface already has the call card. The row still refuses to
        # DRAW a terminal with no title from either side (see the GUI's terminalCardModel).
        card = cards.terminal_result(output="x", exit_code=0)
        assert "title" not in card
        cards.validate(card)

    def test_a_result_card_refuses_a_title_that_is_not_a_string(self):
        with pytest.raises(ValueError):
            cards.validate({"card": "terminal", "title": 7})

    def test_a_call_card_can_carry_a_description_and_a_cwd(self):
        card = cards.terminal_call("pytest -q", description="Run the tests", cwd="/ws")
        assert card == {
            "card": "terminal",
            "title": "pytest -q",
            "description": "Run the tests",
            "cwd": "/ws",
        }


class TestSearchCard:
    def test_folds_triples_into_file_groups_in_first_seen_order(self):
        files = cards.matches_by_file(
            [
                {"file": "b.py", "line": 3, "text": "x"},
                {"file": "a.py", "line": 1, "text": "y"},
                {"file": "b.py", "line": 9, "text": "z"},
            ]
        )
        # Not alphabetical: a search returns its best hits first, and re-sorting would bury
        # the thing the tool ranked.
        assert [group["path"] for group in files] == ["b.py", "a.py"]
        assert [m["line_number"] for m in files[0]["matches"]] == [3, 9]

    def test_drops_a_hit_with_no_file_rather_than_grouping_it_under_nothing(self):
        files = cards.matches_by_file([{"line": 1, "text": "x"}, {"file": "a.py", "line": 2, "text": "y"}])
        assert [group["path"] for group in files] == ["a.py"]

    def test_the_total_defaults_to_what_was_found(self):
        files = cards.matches_by_file([{"file": "a.py", "line": 1, "text": "x"}])
        card = cards.search_matches(files)
        assert card["total"] == 1
        assert card["truncated"] is False

    def test_refuses_an_empty_matches_card(self):
        # Zero hits is an answer, and it is the sentence's job — an empty card renders as a
        # failure rather than as a result.
        with pytest.raises(ValueError):
            cards.search_matches([])
        with pytest.raises(ValueError):
            cards.search_paths([])


class TestReadCard:
    def test_numbers_must_ascend_and_stay_inside_the_file(self):
        with pytest.raises(ValueError):
            cards.read_result("a.py", [{"number": 5, "text": "x"}, {"number": 5, "text": "y"}], 10)
        with pytest.raises(ValueError):
            cards.read_result("a.py", [{"number": 0, "text": "x"}], 10)

    def test_a_zero_line_file_is_legal(self):
        card = cards.read_result("empty.py", [], 0)
        assert card["lines"] == []

    def test_keeps_the_file_position_not_the_window_position(self):
        card = cards.read_result("a.py", [{"number": 200, "text": "x"}], 400, offset=200)
        assert card["offset"] == 200
        assert card["lines"][0]["number"] == 200


class TestWebCard:
    def test_a_fetch_omits_a_status_it_was_never_given(self):
        # This engine's fetch raises on a non-2xx rather than returning one, so a successful
        # fetch has no status to report and inventing 200 would be a claim nobody made.
        assert "status_code" not in cards.web_fetch("https://x.example")

    def test_a_search_card_may_be_an_answer_alone(self):
        card = cards.web_search([], answer="The answer is 42")
        assert card["sources"] == []

    def test_a_search_card_needs_sources_or_an_answer(self):
        with pytest.raises(ValueError):
            cards.web_search([])
        with pytest.raises(ValueError):
            cards.web_fetch("")


class TestValidate:
    def test_accepts_every_builder_output(self):
        built = [
            cards.generic_call("t", kind="read"),
            cards.generic_result(content=["x"]),
            cards.terminal_call("ls"),
            cards.terminal_result(title="ls", exit_code=0),
            cards.diff_call("w", [cards.file_diff("a.py", None, "x")]),
            cards.diff_result([cards.file_diff("a.py", "x", "y", added=1, removed=1)]),
            cards.read_result("a.py", [{"number": 1, "text": "x"}], 1),
            cards.search_matches([{"path": "a.py", "matches": [{"line_number": 1, "line": "x"}]}]),
            cards.search_paths(["a.py"]),
            cards.web_search([{"url": "https://x.example"}]),
            cards.web_fetch("https://x.example"),
            cards.files_result([{"path": "report.md", "description": "The report"}]),
            cards.fold(folded_messages=38, kept_messages=6, before_tokens=74_895, after_tokens=33_400, saved_tokens=41_495),
        ]
        for card in built:
            cards.validate(card)

    def test_refuses_an_unknown_kind(self):
        with pytest.raises(ValueError):
            cards.validate({"card": "hologram"})

    def test_refuses_a_card_that_is_not_a_dict(self):
        for value in (None, "terminal", 7, []):
            with pytest.raises(ValueError):
                cards.validate(value)

    def test_refuses_a_search_with_no_discriminant(self):
        # `shape` is what stops a matches card being read as a paths card; without it the
        # surface has to guess, and guessing is how a list of files renders as a list of hits.
        with pytest.raises(ValueError):
            cards.validate({"card": "search", "files": [{"path": "a", "matches": []}]})

    def test_refuses_a_negative_count(self):
        with pytest.raises(ValueError):
            cards.validate(
                {"card": "diff", "diffs": [{"path": "a", "new_text": "x", "added": -1}]}
            )


class TestFoldCard:
    """`fold_context`'s result: what the fold did, in numbers, for its row."""

    def test_carries_every_count(self):
        card = cards.fold(folded_messages=38, kept_messages=6, before_tokens=74_895, after_tokens=33_400, saved_tokens=41_495)
        assert card == {
            "card": "fold", "folded_messages": 38, "kept_messages": 6,
            "before_tokens": 74_895, "after_tokens": 33_400, "saved_tokens": 41_495,
        }

    def test_a_count_that_is_missing_or_not_a_count_is_refused(self):
        with pytest.raises(ValueError):
            cards.fold(folded_messages=38, kept_messages=6, before_tokens=1, after_tokens=1)
        with pytest.raises(ValueError):
            cards.validate({"card": "fold", "folded_messages": 38})
        with pytest.raises(ValueError):
            cards.validate({**cards.fold(folded_messages=38, kept_messages=6, before_tokens=74_895, after_tokens=33_400, saved_tokens=41_495), "saved_tokens": -1})


class TestFilesCard:
    """`present`'s declaration — the row the GUI stacks under the turn."""

    def test_carries_the_models_own_description_and_a_none_when_it_gave_none(self):
        # The description is the one thing on the row code cannot derive; None is how the
        # wire says "the model declared a file without explaining it" (the surface then
        # falls back to the file's kind, Deliverables.tsx).
        card = cards.files_result([{"path": "q3.docx", "description": "Figures"}, {"path": "a.md"}])
        assert card["files"] == [
            {"path": "q3.docx", "description": "Figures"},
            {"path": "a.md", "description": None},
        ]

    def test_refuses_a_card_with_nothing_to_open(self):
        with pytest.raises(ValueError):
            cards.files_result([])
        with pytest.raises(ValueError):
            cards.files_result([{"path": "   "}])

    def test_a_description_must_be_a_string_or_none(self):
        with pytest.raises(ValueError):
            cards.validate({"card": "files", "files": [{"path": "a.md", "description": 7}]})
        with pytest.raises(ValueError):
            cards.validate({"card": "files", "files": [{"path": ""}]})


class TestSlim:
    def test_passes_a_card_within_the_budget(self):
        card = cards.terminal_result(title="ls", output="x" * 100)
        assert cards.slim(card) is card

    def test_drops_one_past_the_budget(self):
        # A presenter that builds a 5 MB card has a bug worth seeing, and it is not a bug
        # that should take the turn down — the row degrades to the sentence it always had.
        card = cards.terminal_result(title="ls", output="x" * 200_000)
        assert cards.slim(card) is None


class TestTheContractSeam:
    """How a card reaches a presenter, and what happens when it is unusable."""

    def test_present_result_receives_the_card_metadata(self):
        seen: dict[str, object] = {}

        def present(arguments, value, card_meta=None):
            seen["meta"] = card_meta
            return ToolPresentation(summary="done", card=cards.generic_result())

        contract = ToolContract(present_result=present)
        safe_present_result(contract, "t", {}, {}, {"read": {"path": "a.py"}})
        assert seen["meta"] == {"read": {"path": "a.py"}}

    def test_a_two_argument_presenter_still_works_unchanged(self):
        # Five presenters and every direct call in the suite are two-argument; the third is
        # normalized in, not required.
        def present(arguments, value):
            return ToolPresentation(summary="done")

        contract = ToolContract(present_result=present)
        assert contract.present_result({}, {}) is not None
        assert safe_present_result(contract, "t", {}, {}, {"x": 1}).summary == "done"

    def test_a_three_argument_presenter_must_keep_its_third_optional(self):
        # Otherwise a direct two-argument call — which the whole suite makes — is a TypeError
        # on the turn's hot path, where it would look like a broken presenter rather than a
        # broken declaration.
        def present(arguments, value, card_meta):
            return ToolPresentation(summary="done")

        with pytest.raises(TypeError):
            ToolContract(present_result=present)

    def test_a_presenter_taking_one_argument_is_refused_at_construction(self):
        with pytest.raises(TypeError):
            ToolContract(present_result=lambda arguments: ToolPresentation(summary="x"))

    def test_a_bad_card_costs_the_card_and_not_the_sentence(self):
        def present(arguments, value, card_meta=None):
            return ToolPresentation(
                summary="Command succeeded",
                card={"card": "terminal", "title": ""},  # a terminal card with no command
            )

        contract = ToolContract(present_result=present)
        # The raise happens inside `_guard`, so the whole presentation goes — which is why
        # the card builders fail loud at construction instead.
        assert safe_present_result(contract, "t", {}, {}) is None

    def test_an_oversized_card_is_dropped_and_the_sentence_survives(self):
        def present(arguments, value, card_meta=None):
            return ToolPresentation(
                summary="Command succeeded",
                card=cards.terminal_result(title="ls", output="x" * 200_000),
            )

        contract = ToolContract(present_result=present)
        shown = safe_present_result(contract, "t", {}, {})
        assert shown is not None
        assert shown.summary == "Command succeeded"
        assert shown.card is None

    def test_the_card_rides_as_dict(self):
        shown = ToolPresentation(
            summary="Read a.py", kind="read", card=cards.read_result("a.py", [], 0)
        )
        payload = shown.as_dict()
        assert payload["card"]["card"] == "read"

    def test_as_dict_omits_a_card_that_was_never_set(self):
        # Backward compatible by construction: every card-less presentation is byte-identical
        # to what it was before cards existed.
        assert "card" not in ToolPresentation(summary="done").as_dict()


def test_the_engine_counts_survive_to_the_edit_card():
    # GUI tools review, B1: the engine's hunk carries exact counts (its sides are context
    # windows), and the presenter rebuilt the hunk without them - the row then counted
    # sides (+5 -5 for a one-line edit) and the turn header found none (+0 -0).
    from harness.tools.files.present import _replace_present_result

    hunk = {"path": "orders/line.py", "old_text": "a\nb\nqty = 1\nc\n", "new_text": "a\nb\nqty = 2\nc\n",
            "added": 1, "removed": 1}
    card = _replace_present_result({"path": "orders/line.py"}, {"replacements": 1}, {"diffs": [hunk]}).card
    (shown,) = card["diffs"]
    assert (shown["added"], shown["removed"]) == (1, 1)


def test_a_programs_output_reaches_its_card():
    # GUI tools review, B4: run_code had no result card on the belief that the result text
    # rendered underneath; the GUI never drew it, so `45` showed nowhere.
    from harness.tools.code_mode import RUN_CODE_CONTRACT

    shown = safe_present_result(
        RUN_CODE_CONTRACT, "run_code", {"code": "print(45)"}, {"output": "45\n", "tool_calls": 0}
    )
    assert shown.card["card"] == "terminal" and shown.card["output"] == "45"
    assert shown.card["exit_code"] == 0
    failed = safe_present_result(
        RUN_CODE_CONTRACT, "run_code", {"code": "x"}, {"output": "", "error": "NameError: x"}
    )
    assert failed.card["exit_code"] == 1 and "NameError" in failed.card["output"]
