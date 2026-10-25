"""Telling a stuck turn that it is stuck (harness/tools/repeat.py).

The iteration cap already stopped runaway turns, and as a cost backstop it works. As the
only answer to a stuck agent it fails twice: the model is never told it is repeating
itself, so it has no reason to try anything else; and the user is handed "max iterations
reached", which names the counter that stopped the turn and nothing they can act on.

The signal here is deliberately not a call count. Counting alone is wrong in both
directions — `shell_task_output` exists to be polled, and one retry of a broken call is
not a loop. What identifies a stuck agent is the same call returning the same answer:
work with no effect, by definition. That needs no per-tool allowlist, which is the whole
reason to prefer it — a poll returning new output is never flagged, and a failing API
returning the same error is flagged on the third try.

Nothing is blocked. A guard that guessed wrong about legitimate repetition would break
real work, so the model is told what it is doing and decides for itself.
"""

from __future__ import annotations

import pytest

from harness.tools.repeat import RepeatGuard, canonical_arguments


def _guard(**kwargs):
    return RepeatGuard(**kwargs)


# -- what counts as the same work ------------------------------------------------


def test_two_identical_calls_are_not_yet_a_loop():
    """One retry is ordinary. A guard that fires on it would be noise, and noise is how
    a model learns to ignore the guard that matters."""
    guard = _guard()
    assert guard.observe("read_file", {"path": "a.py"}, "contents") is None
    assert guard.observe("read_file", {"path": "a.py"}, "contents") is None


def test_the_third_identical_call_gets_a_quiet_note():
    guard = _guard()
    for _ in range(2):
        guard.observe("read_file", {"path": "a.py"}, "contents")
    note = guard.observe("read_file", {"path": "a.py"}, "contents")
    assert note is not None
    assert "3 calls" in note and "read_file" in note
    assert "Nothing is changing" in note


def test_a_call_that_returns_something_new_is_progress_not_repetition():
    """`shell_task_output` is *designed* to be polled. Counting calls alone would nag at
    the one tool whose whole job is to be called again."""
    guard = _guard()
    for i in range(6):
        assert guard.observe("shell_task_output", {"task_id": "bg-1"}, f"line {i}") is None


def test_different_arguments_are_different_work():
    guard = _guard()
    for path in ("a.py", "b.py", "c.py", "d.py"):
        assert guard.observe("read_file", {"path": path}, "same text") is None


def test_argument_order_does_not_make_a_call_look_new():
    guard = _guard()
    guard.observe("write_file", {"path": "a", "content": "x"}, "ok")
    guard.observe("write_file", {"content": "x", "path": "a"}, "ok")
    assert guard.observe("write_file", {"path": "a", "content": "x"}, "ok") is not None


def test_the_same_call_under_a_different_status_is_counted_apart():
    """A call that was refused and later ran is not the same event twice."""
    guard = _guard()
    guard.observe("run_shell", {"command": "rm -rf /"}, "denied", status="blocked")
    guard.observe("run_shell", {"command": "rm -rf /"}, "denied", status="blocked")
    assert guard.observe("run_shell", {"command": "rm -rf /"}, "denied", status="ok") is None


# -- escalation -------------------------------------------------------------------


def test_the_reminder_hardens_and_names_the_arguments():
    guard = _guard()
    notes = [
        guard.observe("web_fetch", {"url": "https://api.example.com/v1"}, {"error": "500"})
        for _ in range(5)
    ]
    assert notes[2] is not None and "https://api.example.com/v1" not in notes[2]
    firm = notes[4]
    assert firm is not None
    assert "5 times" in firm
    assert "https://api.example.com/v1" in firm  # the arguments, by the second reminder
    assert "tell the user" in firm  # stopping is named as an option


def test_reminders_fire_only_at_the_thresholds():
    guard = _guard(notice_at=(3, 5))
    fired = [
        i + 1
        for i in range(8)
        if guard.observe("t", {}, "same") is not None
    ]
    assert fired == [3, 5]  # silent after the last threshold; the cap is close behind


def test_huge_arguments_do_not_swamp_the_reminder():
    """Only the firm reminder embeds the arguments, so it is the one that can be
    swamped — a 5 KB command pasted back at the model would bury the point being made."""
    guard = _guard()
    args = {"command": "echo " + "x" * 5000}
    notes = [guard.observe("run_shell", args, "out") for _ in range(5)]
    firm = notes[4]
    assert firm is not None and len(firm) < 500


# -- turn scope and bookkeeping ---------------------------------------------------


def test_a_new_turn_starts_from_nothing():
    """The user asking the same thing twice is not the agent going in circles."""
    guard = _guard()
    for _ in range(3):
        guard.observe("read_file", {"path": "a"}, "x")
    guard.reset()
    assert guard.observe("read_file", {"path": "a"}, "x") is None
    assert guard.worst.count == 1


def test_the_worst_repetition_is_what_the_turn_spent_itself_on():
    guard = _guard()
    guard.observe("read_file", {"path": "a"}, "x")
    for _ in range(4):
        guard.observe("web_fetch", {"url": "u"}, {"error": "500"})
    assert guard.worst.tool == "web_fetch"
    assert guard.worst.count == 4
    assert guard.worst.arguments == {"url": "u"}


def test_nothing_observed_means_nothing_to_report():
    assert _guard().worst is None


def test_memory_stays_bounded_across_a_pathological_turn():
    guard = _guard(capacity=8)
    for i in range(500):
        guard.observe("read_file", {"path": f"file-{i}.py"}, "x")
    assert len(guard._seen) <= 8


def test_arguments_that_cannot_be_serialised_still_compare():
    guard = _guard()
    unserialisable = {"handle": object()}
    for _ in range(3):
        note = guard.observe("weird_tool", unserialisable, "x")
    assert note is not None


@pytest.mark.parametrize("bad", [{"notice_at": ()}, {"notice_at": (1, 3)}, {"capacity": 0}])
def test_nonsense_configuration_is_refused(bad):
    with pytest.raises(ValueError):
        RepeatGuard(**bad)


# -- one definition of "the same action" ------------------------------------------


def test_canonical_action_identity_is_stable():
    """The repeat guard's notion of "the same call" hangs on `canonical_arguments`: key
    order and empty shapes must not change identity, or a loop would slip past the guard."""
    args = {"b": 2, "a": [1, {"z": None}]}
    assert canonical_arguments(args) == canonical_arguments(
        {"a": [1, {"z": None}], "b": 2}
    )
    assert canonical_arguments(None) == canonical_arguments({})
