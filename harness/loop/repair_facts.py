"""A repair, read from the transcript: a check that named a failure, an edit, and a later
look at the same thing that no longer names it.

The verification ledger says what the last check said. This reads one fact further: which
failures earlier checks named, and whether the last check looked at them again. Like the
ledger it is derived from the transcript alone, with no state of its own and no model
call, so the same transcript always gives the same repair.

    red    `pytest tests/x.py`  ->  FAILED tests/x.py::test_a, FAILED tests/x.py::test_b
    edit   src/a.py
    again  `pytest -q tests/`   ->  FAILED tests/x.py::test_b
    =      test_a was repaired, by an edit to src/a.py

A later run counts as a second look only when it covered the failure (`covers`): the same
check, give or take how its output is shown, or a run of the same runner whose paths include the test.
A narrower or a different check that passes says nothing about a failure it did not run,
and a passing run does not list what it passed. A run that failed without naming anything
(output cut by a pipe, a runner this module does not read) proves nothing, and is read as
no look at all.

What kept a check from running at all (a `setup:` mark; a module that would not
import, a command the shell did not know) is usually fixed in how the check is run, not in the
code. Any later run of the same runner looks at it again, and a run that no longer names it
proves a repair when work was edited OR the check now runs as another command; the passing
command is then what the record keeps.

Measured on the session store, 2026-10-07 (6 sessions, 240 checks): asking for the very
same command saw 28 of the failures that were repaired, this rule 43.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

from harness.tools.checks import failure_marks, is_setup, runner_args, runner_of
from .verification import check_results, normalize_command, parse_status
from .work_edits import work_edit_paths

#: What changes how a check's output is shown and not what it checks: a leading `cd`, a
#: pipe or a redirect after it, and the runner's display flags.
_LEAD = re.compile(r"^\s*(?:cd\s+\S+\s*(?:&&|;)\s*)+")
_TAIL = re.compile(r"\s*(?:2>&1)?\s*\|.*$|\s+2>&1\s*$", re.S)
_DISPLAY = re.compile(
    r"\s(?:-q+|-v+|-s|-r[a-zA-Z]+|--tb=\S+|--no-header|--color=\S+|-p\s+no:\S+|--quiet|--verbose)(?=\s|$)"
)
#: Flags that make a run look at less than its paths say, or stop before the end, in the runners
#: the store has shown: pytest's, jest's and vitest's (`-t`, `--testNamePattern`, `--bail`,
#: `--onlyChanged`, `--changed`), mocha's (`--grep`, `--bail`), go's (`-run`, `-failfast`).
_NARROWS = re.compile(
    r"\s(?:-k|-m|--lf|--ff|--deselect|-x|--maxfail|--sw|--stepwise"
    r"|-t|--testNamePattern|--bail|-o|--onlyChanged|--changed|--grep|-g|-run|-failfast)(?=\s|=|$)"
)


def what_it_checks(command: str) -> str:
    """A check's command without what only changes how its output is shown."""
    text = _TAIL.sub("", _LEAD.sub("", str(command or "")))
    return normalize_command(_DISPLAY.sub(" ", f" {text} "))


def _run_scope(command: str) -> Optional[tuple[str, list[str]]]:
    """The runner a check calls and the paths or names it looks at ([] = the whole suite); None
    when it is not a check, or when a flag makes it look at less than its words say. Only what
    follows the runner is read: the `-m` of `python -m pytest` is Python's. It was pytest's alone,
    so a vitest, jest, go or cargo run over the whole suite never counted as looking again at a
    failure a narrower run had named."""
    found = runner_args(what_it_checks(command))
    if found is None:
        return None
    runner, words = found
    if _NARROWS.search(" " + " ".join(words) + " "):
        return None
    return runner, [word.replace("\\", "/").strip("'\"").rstrip("/") for word in words if not word.startswith("-")]


def covers(later: str, earlier: str, mark: str) -> bool:
    """Whether a run of `later` looked again at the failure `mark` that a run of `earlier`
    named: the same check, or a run of the same runner over the whole suite, the test's file, a folder
    above it or the test itself, with nothing that narrows or cuts the run short."""
    if what_it_checks(later) == what_it_checks(earlier):
        return True
    if is_setup(mark):
        # What kept a check from running is usually fixed in how it is run (an environment
        # variable, the folder, `uv run`), so a later run of the same runner looks again at it.
        runner = runner_of(later)
        return runner is not None and runner == runner_of(earlier)
    if not mark.startswith("test:"):
        return False
    scope = _run_scope(later)
    if scope is None or scope[0] != runner_of(earlier):
        return False
    paths = scope[1]
    node = mark.split(":", 1)[1]
    file = node.split("::", 1)[0]
    return not paths or any(
        path == node or path == file or file.startswith(path + "/") or node.startswith(path + "::")
        for path in paths
    )


@dataclass(frozen=True)
class Check:
    """One run of a check as the transcript holds it."""

    command: str
    ok: Optional[bool]
    marks: frozenset[str]
    index: int
    #: The run's identity across a transcript whose positions moved (a compaction): the
    #: command, what it printed, and when its result landed. Without the time two runs that
    #: print the same thing ("All checks passed!") would be one run.
    key: str


@dataclass(frozen=True)
class Repair:
    """Failures earlier checks named and the last check, looking again, did not; and what
    was edited between."""

    marks: frozenset[str]
    command: str
    files: tuple[str, ...]
    #: Model steps after the run that first named them up to and counting the one that ran
    #: the check again: what the repair cost.
    steps: int
    check: Check
    #: Each edited path with the id of the call that wrote it first in the repair: that
    #: call's before-image is the file as it was before the repair.
    first_edits: tuple[tuple[str, str], ...] = ()


def _check(index: int, command: str, content: str, landed: Any = None) -> Check:
    ok, _ = parse_status(content)
    identity = f"{normalize_command(command)}\n{content}\n{landed if landed is not None else ''}"
    return Check(
        command=command,
        ok=ok,
        marks=failure_marks(content) if ok is False else frozenset(),
        index=index,
        key=hashlib.sha1(identity.encode("utf-8", "replace")).hexdigest()[:16],
    )


def last_check(messages: Sequence[Mapping[str, Any]]) -> Optional[Check]:
    """The most recent check in the transcript, or None."""
    ordered = list(messages)
    latest = None
    for latest in check_results(ordered):
        pass
    return _check(*latest, landed=ordered[latest[0]].get("ts")) if latest is not None else None


def _settled(check: Check) -> bool:
    """Whether a run says anything about a failure: it passed, or it failed naming what."""
    return check.ok is True or (check.ok is False and bool(check.marks))


def _run_differently(mark: str, first: Check, now: Check) -> bool:
    """A setup failure repaired by how the check is run: the run that no longer names it is
    another command than the one that did. The same command passing on its own (a network that
    came back) repaired nothing."""
    return is_setup(mark) and what_it_checks(now.command) != what_it_checks(first.command)


def repair_of(messages: Sequence[Mapping[str, Any]], exempt: Sequence[str] = ()) -> Optional[Repair]:
    """The repair the LAST check proves, or None.

    Every failure a check names stays open, from the run that first named it, until a run
    that covers it does not name it. The last check proves a repair of the ones it closed
    with at least one work edit since they were first named."""
    ordered = list(messages)
    runs = [_check(*run) for run in check_results(ordered)]
    if not runs or not _settled(runs[-1]):
        return None
    opened: dict[str, Check] = {}  # an open failure -> the run that first named it
    for run in runs[:-1]:
        if not _settled(run):
            continue
        for mark in [m for m, first in opened.items() if m not in run.marks and covers(run.command, first.command, m)]:
            del opened[mark]
        for mark in run.marks:
            opened.setdefault(mark, run)
    now = runs[-1]
    # A failure that went away with no edit since it was first named was not repaired.
    edited_since = {
        first.index: work_edit_paths(ordered, first.index, now.index, now.command, exempt)
        for first in set(opened.values())
    }
    closed = {
        mark: first
        for mark, first in opened.items()
        if mark not in now.marks
        and covers(now.command, first.command, mark)
        and (edited_since[first.index][0] or _run_differently(mark, first, now))
    }
    if not closed:
        return None
    start = min(first.index for first in closed.values())
    steps = sum(1 for message in ordered[start + 1 : now.index] if message.get("role") == "assistant")
    _, files, first_calls = edited_since[start]
    return Repair(
        marks=frozenset(closed), command=now.command, files=tuple(files), steps=steps, check=now,
        first_edits=tuple((path, first_calls[path]) for path in files if path in first_calls),
    )


__all__ = ["Check", "Repair", "covers", "last_check", "repair_of", "what_it_checks"]
