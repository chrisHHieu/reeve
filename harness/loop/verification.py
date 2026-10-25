"""The verification ledger: what the last check actually said (JSON-free, deterministic).

The harness already records that a check *ran* (the engine's verification gate tracks
`wrote_code` / `ran_after`) but not what it *said*. This module derives the missing
fact from the transcript alone: the last verification command, whether it passed,
and how many file edits happened since. No new state, no provider call, no egress.

Its readers are the harness's own gates, which act on the fact and never on the
answer's wording: the check-failed nudge, the goal's proof, the
view cut and the turn's receipt.

Scope: `run_shell` results as they land in the model-facing transcript (the rendered
text plus its `[exit N; cwd …]` status line, or a JSON object when a caller kept the
raw result). `run_code` sub-calls and background shell tasks are out of scope for now
and are reported as "no check ran" rather than guessed at.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Iterator, Mapping, Optional, Sequence

# Which edits void a check lives in `work_edits`; the names stay importable from here.
from .call_pairs import answered_calls
from .work_edits import EDIT_TOOLS, is_work_edit, work_edits_after
from .verdict import TASK_TOOL, VERIFY_AGENT, Verdict, last_verdict  # noqa: F401 — re-exported
# What a check is and how its output says it failed live in `tools/checks`; the names
# stay importable from here for the ledger's readers.
from harness.tools.checks import failure_summary, is_check_command, reports_failure, shown_command  # noqa: F401

SHELL_TOOL = "run_shell"


_EXIT_RE = re.compile(r"exit (-?\d+|unknown)")

_SUMMARY_CHARS = 120


@dataclass(frozen=True)
class Verification:
    """One settled check: what ran, whether it passed, what changed since."""

    tool: str
    command: str
    ok: Optional[bool]
    summary: str
    edits_since: int
    timed_out: bool = False
    #: Transcript index of the check's result message. The engine's check-loop streak
    #: advances only when a NEW result landed, and this is that identity: two
    #: runs of the same command are two checks, which is the whole stuck signal.
    index: int = -1


#: Durations a check prints (`in 1.08s`, `12ms`, `(0:00:12)`): they differ run to run.
_DURATION_RE = re.compile(r"\b\d+(?:\.\d+)?\s*(?:ms|s|sec|secs|seconds)\b|\(\d+:\d{2}:\d{2}\)")


def outcome_key(summary: str) -> str:
    """What a check said, stripped of what changes between identical runs: durations and
    banner rules (`==== 4 failed, 67 passed in 19.34s ====` -> `4 failed, 67 passed in`).
    Two checks with one key told the agent the same thing."""
    text = _DURATION_RE.sub("", summary or "")
    text = re.sub(r"[=\-*_]{2,}", " ", text)
    return " ".join(text.split()).lower()


is_verification_command = is_check_command


def parse_status(content: str) -> tuple[Optional[bool], bool]:
    """`(ok, timed_out)` from the rendered status line, or from a JSON result body."""
    text = content or ""
    timed_out = "timed out" in text
    code: Optional[int] = None
    match = None
    for match in _EXIT_RE.finditer(text):
        pass
    if match is not None and match.group(1) != "unknown":
        code = int(match.group(1))
    stripped = text.lstrip()
    if code is None and stripped.startswith("{"):
        try:
            body = json.loads(stripped)
        except (ValueError, TypeError):
            body = None
        if isinstance(body, dict):
            raw = body.get("exit_code")
            if isinstance(raw, int) and not isinstance(raw, bool):
                code = raw
            timed_out = timed_out or bool(body.get("timed_out"))
    if code is None:
        return None, timed_out
    return (code == 0 and not timed_out and not reports_failure(text)), timed_out


# A runner's colour codes. vitest's last line reached the receipt as `ESC[2m Duration ESC[22m
# 2.22s` and was drawn as boxes and `[2m` (owner's screenshot, 2026-10-15).
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def _summary_of(content: str) -> str:
    """The last non-empty line that is not the status line — what a person reads first."""
    lines = [line.strip() for line in _ANSI.sub("", content or "").splitlines() if line.strip()]
    for line in reversed(lines):
        if line.startswith("[") and "exit" in line:
            continue
        return line[:_SUMMARY_CHARS]
    return ""


def _command_of(arguments: Any) -> str:
    """The whole command a shell call ran. Whether it is a check, and which, is read on all of it;
    a reader that is told it shortens it (`shown_command`). Cut to its first 120 characters here,
    a check after a long `cd` or a first step was no check at all: 114 of 618 in the session store,
    and the repairs those runs proved went unrecorded (2026-10-23)."""
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except (ValueError, TypeError):
            return arguments.strip()
    if isinstance(arguments, Mapping):
        return str(arguments.get("command") or "").strip()
    return ""


def last_verification(
    messages: Sequence[Mapping[str, Any]], exempt: Sequence[str] = ()
) -> Optional[Verification]:
    """The most recent verification command in the transcript, or None.

    Deterministic and side-effect free: the same transcript always answers the same
    fact, which is what makes it usable as a label.
    """
    return _latest_run(messages, is_verification_command, exempt)


def normalize_command(command: str) -> str:
    """Whitespace-collapsed, so `pytest  -q` and `pytest -q` are one command."""
    return " ".join(str(command or "").split())


def last_run(
    messages: Sequence[Mapping[str, Any]], command: str, exempt: Sequence[str] = ()
) -> Optional[Verification]:
    """The most recent `run_shell` of exactly this command (whitespace-normalized), or
    None — any command, not only verification-shaped ones, because an acceptance check
    the person named (`node scripts/check.js`) is a check whatever it looks like."""
    wanted = normalize_command(command)
    if not wanted:
        return None
    return _latest_run(messages, lambda seen: normalize_command(seen) == wanted, exempt)


def last_variant(messages: Sequence[Mapping[str, Any]], command: str) -> Optional[str]:
    """The newest `run_shell` command that STARTS with this one but is not it (a pipe or
    a redirect added: `pytest -q | tail -20`), or None.

    Such a run never counts as the check: a pipe reports its last stage's exit code in a
    POSIX shell, so a red suite behind `| tail` reads green. It is only named, so the
    refusal can say what happened instead of "not run" (live 2026-09-25: the model ran
    `python -m pytest -q 2>&1 | Select-Object -Last 20` and was told the check never ran).
    """
    wanted = normalize_command(command)
    if not wanted:
        return None
    seen: Optional[str] = None
    for message in messages:
        if message.get("role") != "assistant":
            continue
        for call in message.get("tool_calls") or []:
            function = call.get("function") or {}
            if str(function.get("name") or "") != SHELL_TOOL:
                continue
            ran = normalize_command(_command_of(function.get("arguments")))
            if ran != wanted and ran.startswith(wanted):
                seen = ran
    return seen


def check_results(
    messages: Sequence[Mapping[str, Any]], matches: Any = is_verification_command
) -> Iterator[tuple[int, str, str]]:
    """Every `run_shell` result whose command `matches`, in transcript order:
    `(index of the result message, the command, the result's text)`."""
    # Each result is read with the call it answers BY POSITION (`call_pairs`): ids are unique only within
    # one response, and a table keyed by id over the whole transcript kept the last call of each id.
    for index, message, call in answered_calls(list(messages)):
        if message.get("role") != "tool" or call is None:
            continue
        function = call.get("function") or {}
        command = _command_of(function.get("arguments"))
        if str(function.get("name") or "") == SHELL_TOOL and matches(command):
            yield index, command, str(message.get("content") or "")


def _latest_run(
    messages: Sequence[Mapping[str, Any]], matches: Any, exempt: Sequence[str] = ()
) -> Optional[Verification]:
    ordered = list(messages)
    fact: Optional[Verification] = None
    result_index = -1
    for index, command, content in check_results(ordered, matches):
        ok, timed_out = parse_status(content)
        fact = Verification(
            tool=SHELL_TOOL,
            command=command,
            ok=ok,
            summary=_summary_of(content),
            edits_since=0,
            timed_out=timed_out,
        )
        result_index = index

    if fact is None:
        return None
    edits = work_edits_after(ordered, result_index, command=fact.command, exempt=exempt)
    return Verification(
        tool=fact.tool,
        command=fact.command,
        ok=fact.ok,
        summary=fact.summary,
        edits_since=edits,
        timed_out=fact.timed_out,
        index=result_index,
    )


def as_row(fact: Optional[Verification]) -> Optional[dict[str, Any]]:
    """The fact as a plain row, for the turn's receipt (None when nothing ran). The command is
    as it is said, not all of it."""
    if fact is None:
        return None
    return {
        "command": shown_command(fact.command),
        "ok": fact.ok,
        "edits_since": fact.edits_since,
        "summary": fact.summary,
        "timed_out": fact.timed_out,
    }
