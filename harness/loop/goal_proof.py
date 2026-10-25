"""What proves a goal done — read from the transcript by code, never from the model's word.

Split from `goals.py` (size ceiling). The rules: acceptance checks
must have passed with no edit after them; a check-less goal needs the independent
`verify` subagent's PASS where one can be asked; a red last check left standing refuses
`done` either way. Pure: a goal and a transcript in, sentences out. Nothing is run here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Sequence

from harness.tools.checks import shown_command

from .verification import last_run, last_variant, last_verdict, last_verification, normalize_command

if TYPE_CHECKING:
    from .goals import Goal

#: Acceptance checks one goal may carry — each is re-read from the transcript per `done`.
MAX_CHECKS = 8


def clean_checks(raw: Any) -> list[str]:
    """The acceptance commands as a clean list: strings, whitespace-collapsed, deduped."""
    if isinstance(raw, str):
        raw = [raw]
    out: list[str] = []
    for item in raw if isinstance(raw, (list, tuple)) else []:
        command = normalize_command(str(item or ""))
        if command and command not in out:
            out.append(command)
    return out[:MAX_CHECKS]


def check_states(goal: "Goal", messages: Any, exempt: Sequence[str] = ()) -> list[dict[str, str]]:
    """Each acceptance check's standing, read from the transcript the way `completion_gaps`
    reads it — for the goal strip, which otherwise showed rounds and nothing about whether
    the work passes. `passed` means passed with no edit after it; `stale` passed but a file
    changed since; `variant` ran only as a piped/redirected form."""
    try:
        transcript = list(messages or [])
    except TypeError:
        transcript = []
    out: list[dict[str, str]] = []
    for command in goal.checks:
        run = last_run(transcript, command, exempt)
        if run is None:
            state = "variant" if last_variant(transcript, command) else "not_run"
        elif run.ok is False:
            state = "failed"
        elif run.ok is None:
            state = "unknown"
        elif run.edits_since:
            state = "stale"
        else:
            state = "passed"
        out.append({"command": command, "state": state})
    return out


def completion_gaps(
    goal: "Goal", messages: Any, *, verifier: bool = False, exempt: Sequence[str] = ()
) -> list[str]:
    """Why `done` is not proven yet, one sentence each — empty when it is.

    A goal is the one loop in this harness that runs with nobody watching, and the
    model deciding it is finished is the model grading its own work ("someone in your
    crew must not believe you"). The proof is
    read from the transcript by code (the verification ledger), never from the model's
    reason:

    * every acceptance check has a passing run with no file edited after it;
    * a goal with no checks, in a session that can delegate (`verifier`), needs an
      independent `VERDICT: PASS` from the `verify` subagent with no edit after it —
      a fresh context judging work it did not write;
    * either way, the newest verification in the session is not a failure left
      standing (a red check with no edit since is the known state of the work).

    No command is run here: the model runs them through `run_shell`, so the permission
    path, the execution world and the log are the ordinary ones.
    """
    try:
        transcript = list(messages or [])
    except TypeError:
        transcript = []
    gaps: list[str] = []
    for command in goal.checks:
        run = last_run(transcript, command, exempt)
        if run is None:
            variant = last_variant(transcript, command)
            if variant:
                gaps.append(
                    f"`{command}` only ran as `{shown_command(variant)}` — run it exactly as declared (a "
                    "pipe or redirect can hide its exit code)"
                )
            else:
                gaps.append(f"`{command}` has not been run in this session")
        elif run.ok is False:
            gaps.append(f"`{command}` failed on its last run")
        elif run.ok is None:
            gaps.append(f"`{command}` ran but its exit status is unknown")
        elif run.edits_since:
            gaps.append(f"`{command}` passed, but {run.edits_since} file edit(s) came after it")
    if not goal.checks and verifier:
        verdict = last_verdict(transcript, exempt)
        if verdict is None:
            gaps.append(
                "no acceptance checks are declared and no independent verdict exists — "
                'ask the verify subagent (task, subagent_type="verify") with the objective, '
                "what changed and how to check it"
            )
        elif verdict.verdict != "PASS":
            gaps.append(f"the verify subagent's last verdict was {verdict.verdict}")
        elif verdict.edits_since:
            gaps.append(
                f"the verify subagent passed the work, but {verdict.edits_since} file edit(s) "
                "came after it — verify again"
            )
    newest = last_verification(transcript, exempt)
    if newest is not None and newest.ok is False and not newest.edits_since:
        # A declared check already has its own sentence above: told by the command, not by text.
        if normalize_command(newest.command) not in {normalize_command(command) for command in goal.checks}:
            gaps.append(f"the last check, `{shown_command(newest.command)}`, failed and nothing changed since")
    return gaps


def proven(
    goal: "Goal", messages: Any, *, verifier: bool = False, exempt: Sequence[str] = ()
) -> bool:
    """Whether the transcript positively proves the goal — the driver's end-of-round
    judge, so a finished goal stops without another round spent asking the
    model to say so. Stricter than "no gaps": `completion_gaps` has nothing to object to
    in a check-less goal where no verifier can be asked, and nothing proved it either.
    Proof is declared checks all passing with no edit since, or the verify subagent's
    PASS with no edit since — with no red check left standing either way."""
    try:
        transcript = list(messages or [])
    except TypeError:
        return False
    if completion_gaps(goal, transcript, verifier=verifier, exempt=exempt):
        return False
    if goal.checks:
        return True
    verdict = last_verdict(transcript, exempt)
    return verdict is not None and verdict.verdict == "PASS" and not verdict.edits_since


def proof_instruction(goal: "Goal") -> str:
    """The round prompt's line on how `done` will be judged — said up front, so the
    refusal is never the first time the model hears the rule."""
    if goal.checks:
        return (
            "Acceptance checks — run each exactly as written (no pipe or redirect); done "
            "is accepted only once each has passed with no file edited after it: "
            + "; ".join(f"`{command}`" for command in goal.checks)
        )
    return (
        "Before calling done, have the work judged independently: the verify subagent "
        '(task, subagent_type="verify") with the objective, what changed and how to '
        "check it. done is accepted after its VERDICT: PASS with no file edited since."
    )


__all__ = ["MAX_CHECKS", "clean_checks", "completion_gaps", "proof_instruction", "proven"]
