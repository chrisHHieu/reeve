"""The continuation guard: don't ask a looping answer to continue.

Output-token continuation extends an answer the provider cut at the output limit. When
the cut happened inside a repetition loop, the extension buys a longer loop and costs a
whole request. The guard is deliberately fail-open — it must never stop a real answer,
only refuse to fund a visible loop.
"""

from __future__ import annotations

from harness.loop.output_guard import repetition_dominated


def test_a_window_repeating_through_the_text_is_a_loop():
    assert repetition_dominated(("0123456789" * 6) * 30)


def test_a_line_repeated_five_times_is_a_loop():
    text = ("a distinct line\n" * 5) + ("filler " * 120)
    assert repetition_dominated(text)


def test_a_long_varied_answer_is_not_a_loop():
    text = " ".join(f"word{index}" for index in range(300))
    assert not repetition_dominated(text)


def test_a_short_fragment_fails_open():
    assert not repetition_dominated("looping looping looping")


def test_one_recurring_phrase_is_not_domination():
    text = ("start. " + " ".join(f"unique{index}" for index in range(200))) * 2
    assert not repetition_dominated(text)
