"""Turning how a command ended into the result dict the model and the tool card read."""

from __future__ import annotations

import time
from typing import Any, Optional

from ..retention import retain
from ..shell_background import BackgroundTask
from ..shell_kinds import Shell
from ..shell_status import _cut_check_note, _masked_failure_note, _parse_error_hint, _silent_exit_hint, _unknown_tool_hint


class OutcomeMixin:
    def _result(
        self,
        command,
        exit_code,
        output,
        *,
        timed_out,
        truncated=False,
        omitted_lines=0,
        spilled=None,
        error=None,
        elapsed_seconds: Optional[float] = None,
        ended: Optional[str] = None,
        full_chars: Optional[int] = None,
    ):
        result = {
            "command": command,
            "cwd": self.cwd,
            "exit_code": exit_code,
            "output": output,
            "timed_out": timed_out,
            "truncated": truncated,
        }
        if elapsed_seconds is not None:
            # How long the command itself ran, so a stopped one can be told from a fast one without
            # reading the timing sidecar the engine keeps apart.
            result["elapsed_seconds"] = round(elapsed_seconds, 2)
        if truncated and omitted_lines:
            # The marker inside `output` already tells the model. This is the copy the tool card reads.
            result["omitted_lines"] = omitted_lines
        if spilled:
            # The marker names the path too, but a path long enough to crowd out the content is
            # dropped from the text. This copy is always there.
            result["full_output"] = spilled
        if ended:
            # How the wait ended (`shell_wait`): reported, ended, held, stopped or deadline. It is
            # data for the log (`_shell_facts`) and never a sentence.
            result["ended"] = ended
        if full_chars:
            result["output_chars"] = full_chars
        if error:
            result["error"] = error
        return result

    def _keep(self, lines: list[str]):
        """The command's output within budget, spilling what does not fit."""
        kept = retain("".join(lines), budget=self.max_output_chars, spill=self._spill)
        total = len(kept.text) + kept.omitted_chars if kept.truncated else None
        return kept, total

    def _background_result(
        self, task: BackgroundTask, lines: list[str], started: float, ended: str = "deadline"
    ) -> dict[str, Any]:
        """What a command that outran its wait is told.

        The command was not stopped, and the result says so in the words the model needs: the task id
        so it can read or kill it, the promise of a notification so it does not poll, and the output
        so far. `timed_out` stays in the payload for surfaces that key on it, but a reader should
        branch on the status. `ended` is `released` when the wait gave up because the person typed
        into the turn: their message follows this result, and it is answered first."""
        self._adopt(task)
        elapsed = time.monotonic() - started
        kept, total = self._keep(lines)
        result = self._result(
            task.command,
            None,
            kept.text,
            timed_out=True,
            truncated=kept.truncated,
            omitted_lines=kept.omitted_lines,
            spilled=kept.spilled,
            elapsed_seconds=elapsed,
            ended=ended,
            full_chars=total,
        )
        result["status"] = "background"
        result["task_id"] = task.id
        why = (
            " The wait ended early because the user sent a message, which follows: read it "
            "before going on with this command."
            if ended == "released"
            else ""
        )
        note = (
            f"still running after {elapsed:.0f}s — it was NOT stopped.{why} You will be told "
            "when it finishes: do not poll it (no sleep/ps loops). shell_task_output reads "
            "output before then; shell_task_kill stops it."
        )
        if not kept.text.strip() and ended != "released":
            note += (
                " Nothing was printed before the wait gave up, which is also what a "
                "command waiting for input looks like — commands run with stdin closed, so "
                "re-run it non-interactively (a flag such as -y, --yes or "
                "--non-interactive) if that could be it."
            )
        result["note"] = note
        return result

    def _unreported_result(
        self, command, task, waited, kept, total, chosen: Shell, other, exit_code, aborted, elapsed
    ) -> dict[str, Any]:
        """A command whose script never reached its trailer.

        The trailer's number is missing, so the process's own exit code is the honest report: a
        PowerShell parse error exits 1 and `exit 3` exits 3. A parse failure never runs a native
        command, which is why its diagnosis has to be read from the output and not from the code."""
        code = exit_code if exit_code is not None else task.proc.poll()
        if aborted:
            error = "interrupted by user"
        else:
            error = _parse_error_hint(kept.text, chosen, other) or (
                _silent_exit_hint(kept.text, chosen) if code == 1 else None
            )
        # A note only for what is not known. A command that ends its own shell (`exit 7`) prints no
        # trailer and loses nothing: the pipe closed, so every line was read, and the process's code
        # is the status. It once carried "exited before it reported a status" next to `[exit 7]`
        # (4 of 11 commands in one real turn, all correct), and the review counted that sentence as
        # loss.
        if error is None and code is None:
            error = (
                "the command's shell ended without an exit status — the output above "
                "is everything it printed."
            )
        elif error is None and waited.outcome == "held":
            error = (
                "a process this command started still holds its output open — "
                "anything it prints from here on is not in this result."
            )
        return self._result(
            command,
            code,
            kept.text,
            timed_out=False,
            truncated=kept.truncated,
            omitted_lines=kept.omitted_lines,
            spilled=kept.spilled,
            elapsed_seconds=elapsed,
            error=error,
            ended=waited.outcome,
            full_chars=total,
        )

    def _reported_result(
        self, command, waited, kept, total, chosen: Shell, other, exit_code, exit_note, aborted, elapsed
    ) -> dict[str, Any]:
        if aborted:
            error = "interrupted by user"
        elif exit_code in (None, 0):
            error = _parse_error_hint(kept.text, chosen, other)
        else:
            error = _unknown_tool_hint(kept.text, chosen, other) or (
                # cmd reports a line it cannot parse and still reaches its trailer, with a failing
                # status.
                _parse_error_hint(kept.text, chosen, other) if chosen.is_cmd else None
            )
        result = self._result(
            command,
            exit_code,
            kept.text,
            timed_out=False,
            truncated=kept.truncated,
            omitted_lines=kept.omitted_lines,
            spilled=kept.spilled,
            error=error,
            elapsed_seconds=elapsed,
            ended=waited.outcome,
            full_chars=total,
        )
        if not exit_note and exit_code == 0:
            exit_note = _masked_failure_note(kept.text)
        cut = _cut_check_note(command, kept.text, failed=exit_code not in (None, 0) or (exit_code == 0 and bool(exit_note)))
        if cut:
            exit_note = f"{exit_note}; {cut}" if exit_note else cut
        if exit_note:
            result["exit_note"] = exit_note
        return result
