"""What `run_shell` and its task tools say about a result: to the model (`render`), to a
person (`present_*`), and to the session log (`facts`).

Split from `shell.py` (size ceiling): that file runs commands, this one describes what came
back. Pure functions of the result dict; nothing here starts or reads a process.
"""

from __future__ import annotations

import json
from typing import Any

from . import cards
from .checks import cut_by_command, failure_marks, is_setup, reports_failure, runner_of
from .contract import (
    FULL_CHARS,
    LOCATOR,
    SHOWN_CHARS,
    SYSTEM_EXEC_CAPS,
    ToolContract,
    ToolPresentation,
    clip,
)

# -- what a person sees (tools/contract.py) -------------------------------------
# `run_shell`'s `description` argument exists precisely so an approval prompt can show
# INTENT rather than the raw command; until now nothing read it. These two presenters
# are what finally spend it.
#
# No `render` and no `timeout_ms` here on purpose. The result dict is already the right
# shape for the model (exit code, cwd and the truncation flag all matter to it), and the
# tool governs its own deadline through `timeout_seconds` / `_MAX_TIMEOUT`: it kills the
# process group, which no outer budget can do. Declaring one here would either compete
# with that (if below it) or restate it (if equal), so `run_shell` inherits the runtime
# ceiling in `tools/deadline.py` instead — deliberately set ABOVE `_MAX_TIMEOUT`, so the
# backstop can only fire if this tool's own loop never comes back at all.


def _shell_result_card(
    command: str,
    output: str,
    *,
    cwd: Any = None,
    truncated: bool = False,
    omitted_lines: int = 0,
    exit_code: Any = None,
    shell: Any = None,
) -> dict[str, Any]:
    """The result card for one shell call, terminal when it can be.

    A terminal card is headed by the command — that is its whole reason for existing —
    so with no command to head it the card becomes a generic one carrying the output.
    That is the honest degradation for a result whose `command` key is missing, and it
    is also what keeps a card from being a prompt line with nothing after the `$`.

    A check's counts and named failures were drawn above its output until the owner
    found them too loud (2026-10-15): the output and its exit status say it.
    """
    if not command:
        return cards.generic_result(content=[output]) if output else cards.generic_result()
    return cards.terminal_result(
        title=command,
        output=output,
        exit_code=exit_code,
        cwd=cwd,
        truncated=truncated,
        omitted_lines=omitted_lines,
        shell=shell if isinstance(shell, str) else None,
    )


def _shell_present_call(arguments: dict[str, Any]) -> ToolPresentation:
    described = str(arguments.get("description") or "").strip()
    command = str(arguments.get("command") or "").strip()
    summary = described or "Run a terminal command"
    if arguments.get("run_in_background"):
        summary = f"{summary} (in the background)"
    # A background run gets no terminal card: it returns a task id, not output, and a card
    # promising a terminal for something that returned in a millisecond is a lie the row
    # then has to un-tell. Its command still rides in `detail`.
    card = (
        None
        if arguments.get("run_in_background")
        else cards.terminal_call(command, description=described)
    )
    # The raw command stays, one rung down: intent is what a person judges, but hiding
    # the mechanism from someone who CAN read it would be its own kind of dishonesty.
    return ToolPresentation(
        summary=summary, detail=clip(command), kind="execute", card=card
    )


def _shell_present_result(
    arguments: dict[str, Any], value: Any, card_meta: Any = None
) -> ToolPresentation:
    if not isinstance(value, dict):
        return ToolPresentation(summary="Command finished", kind="execute")
    # The command is repeated on the RESULT card even though the call card carried it:
    # only the result presentation is persisted, so a reopened session has no other way to
    # learn what ran — a reloaded terminal row would read `$ ` and nothing else.
    command = str(value.get("command") or arguments.get("command") or "").strip()
    if value.get("task_id") and value.get("status") == "running":
        return ToolPresentation(
            summary=f"Started in the background as {value['task_id']}", kind="execute"
        )
    error = str(value.get("error") or "").strip()
    if error and "exit_code" not in value:
        # A start failure: nothing ran, so there is no exit status to pill. A generic card
        # carries the diagnosis instead — the same rule applies to a failed shell
        # result, because a terminal card is for a command that actually terminal'd.
        return ToolPresentation(
            summary="Command could not start",
            detail=clip(error),
            kind="execute",
            card=cards.generic_result(content=[error]),
        )
    lines = len(str(value.get("output") or "").splitlines())
    detail = f"{lines} line{'' if lines == 1 else 's'} of output"
    # The path travels on the presentation because it cannot travel any other way: the
    # surface sees a 300-character preview of the result JSON, and `full_output` sits
    # behind an `output` key that is already 20,000 characters long.
    link = value.get("full_output") or None
    omitted = value.get("omitted_lines")
    omitted = omitted if isinstance(omitted, int) and omitted > 0 else 0
    if value.get("truncated"):
        detail += f", {omitted:,} omitted" if omitted else " (shortened)"
    # Whatever the command managed before it was stopped or failed is still the most
    # useful thing on the row, so every path below keeps a card.
    output = str(value.get("output") or "")
    truncated = bool(value.get("truncated"))
    cwd = value.get("cwd")
    shell = value.get("shell")
    if value.get("status") == "background" or value.get("timed_out"):
        ran = value.get("elapsed_seconds")
        if isinstance(ran, (int, float)):
            # How long it held before the wait gave up: the number that separates "it was
            # almost done" from "it was never going to finish" on the row a person reads.
            detail += f", still running after {ran:g}s"
        return ToolPresentation(
            summary="Command still running",
            detail=detail,
            kind="execute",
            link=link,
            card=_shell_result_card(
                command, output, cwd=cwd, truncated=truncated, omitted_lines=omitted, shell=shell
            ),
        )
    if error:
        return ToolPresentation(
            summary=clip(error),
            detail=detail,
            kind="execute",
            link=link,
            card=_shell_result_card(
                command, output, cwd=cwd, truncated=truncated, omitted_lines=omitted, shell=shell
            ),
        )
    code = value.get("exit_code")
    if code is None:
        # The exit marker never parsed — say so rather than implying success, and let the
        # card carry no pill: a card must not invent a status the tool did not report.
        return ToolPresentation(
            summary="Command finished, exit status unknown",
            detail=detail,
            kind="execute",
            link=link,
            card=_shell_result_card(
                command, output, cwd=cwd, truncated=truncated, omitted_lines=omitted, shell=shell
            ),
        )
    summary = "Command succeeded" if code == 0 else f"Command failed (exit {code})"
    return ToolPresentation(
        summary=summary,
        detail=detail,
        kind="execute",
        link=link,
        card=_shell_result_card(
            command, output, cwd=cwd, truncated=truncated, omitted_lines=omitted, exit_code=code,
            shell=shell,
        ),
    )


def _shell_status(value: dict[str, Any], *, prefix: str = "") -> str:
    """The bracketed status line: exit code, cwd, timing, truncation, spill path.

    `cwd` is kept because the working directory survives a call — a `cd` in an earlier
    command still holds (the trailer reports it), so the result is how the model stays
    oriented.
    """
    notes: list[str] = [prefix] if prefix else []
    exit_code = value.get("exit_code")
    exit_note = value.get("exit_note")
    notes.append(
        (f"exit {exit_code} ({exit_note})" if exit_note else f"exit {exit_code}")
        if isinstance(exit_code, int)
        else (f"exit unknown ({exit_note})" if exit_note else "exit unknown")
    )
    cwd = value.get("cwd")
    if isinstance(cwd, str) and cwd:
        notes.append(f"cwd {cwd}")
    if value.get("status") == "background" or value.get("timed_out"):
        task = value.get("task_id")
        notes.append(f"still running as {task}" if task else "still running")
    if value.get("truncated"):
        omitted = value.get("omitted_lines")
        notes.append(f"truncated, {omitted} lines omitted" if isinstance(omitted, int) else "truncated")
    full = value.get("full_output")
    if isinstance(full, str) and full:
        notes.append(f"full output: {full}")
    return "[" + "; ".join(notes) + "]"


def _shell_render(arguments: dict[str, Any], value: Any) -> str:
    """Command output plus a compact status line, instead of the JSON result.

    The JSON form escapes the whole stdout and charges every wrapper key on each
    call, which dominates on short commands. The command itself is not echoed:
    the model issued it, and the result is tied to its call.
    """
    if not isinstance(value, dict):
        return json.dumps(value, default=str)
    if value.get("task_id") and value.get("status") == "running":
        # A background start returns an id and no output — the id IS the answer. The
        # generic path below rendered it as "(no output) [exit unknown; cwd …]", hiding
        # the one fact the next call needs (live sessions then guessed ids and got
        # "unknown task: 1"). The id is quoted so the model copies it verbatim, and the
        # no-polling fact is stated because the session is woken on completion.
        task = str(value["task_id"])
        return (
            f"Started in the background as {task}.\n"
            f'Read new output with shell_task_output(task_id="{task}"); stop it with '
            f'shell_task_kill(task_id="{task}"). You will be told when it finishes — do '
            "not poll it."
        )
    error = value.get("error")
    output = value.get("output") if isinstance(value.get("output"), str) else ""
    if value.get("status") == "background":
        # A command that outran its wait budget: the model must learn three things it
        # cannot see in the output — that nothing was killed, that a notification is
        # coming (so it does not start polling), and the id both task tools answer to.
        task = str(value.get("task_id") or "")
        why = (
            "The wait ended early because the user sent a message, which follows: read it before "
            "going on with this command. It is still running"
            if value.get("ended") == "released"
            else "Still running after the wait budget"
        )
        return (
            f"{output if output else '(no output yet)'}\n{_shell_status(value)}\n"
            f"{why} — it was NOT stopped. You will be told "
            f'when it finishes: do not poll it. shell_task_output(task_id="{task}") reads '
            f'output early; shell_task_kill(task_id="{task}") stops it.'
        )
    if error and not output and value.get("exit_code") is None:
        return f"Error: {error}"
    text = f"{output if output else '(no output)'}\n{_shell_status(value)}"
    return f"{text}\n[{error}]" if isinstance(error, str) and error else text


def _shell_task_render(arguments: dict[str, Any], value: Any) -> str:
    """Background-task output: the retained text, then task status and exit code."""
    if not isinstance(value, dict):
        return json.dumps(value, default=str)
    if value.get("error") and "output" not in value:
        return f"Error: {value.get('error')}"
    task = str(value.get("task_id") or arguments.get("task_id") or "")
    status = str(value.get("status") or "")
    output = value.get("output") if isinstance(value.get("output"), str) else ""
    head = _task_read_head(value)
    body = output if output else ("(no matching lines)" if "pattern" in value else "(no output)")
    if head:
        body = f"{head}\n{body}"
    said = f"{body}\n{_shell_status(value, prefix=f'task {task}: {status}')}"
    if value.get("released"):
        said += "\nThe wait ended early because the user sent a message, which follows: read it first."
    return said


def _task_read_head(value: dict[str, Any]) -> str:
    """The line above a search or a window of the whole output: what was looked at, and how to
    read on. Nothing for the plain read of what is new."""
    total = value.get("total_lines")
    if "pattern" in value:
        line = f"{value.get('matches', 0)} of {total} lines match {value['pattern']!r}"
        if value.get("note"):
            line += f" ({value['note']})"
        return line
    if "from_line" in value:
        line = f"lines {value.get('from_line')}-{value.get('to_line')} of {total}"
        if value.get("next_from_line"):
            line += f" (more: from_line={value['next_from_line']})"
        elif value.get("note"):
            line += f" ({value['note']})"
        return line
    return ""


def _task_output_facts(arguments: dict[str, Any], value: Any) -> dict[str, Any]:
    """What the log records about a read of a background job: which kind of read,
    how much it showed, and the job's state."""
    if not isinstance(value, dict) or value.get("error"):
        return {}
    facts: dict[str, Any] = {
        "read": "search" if "pattern" in value else "lines" if "from_line" in value else "new",
        "status": value.get("status"),
        "exit_code": value.get("exit_code") if isinstance(value.get("exit_code"), int) else None,
        "waited": bool(arguments.get("wait")),
    }
    if value.get("released"):
        facts["released"] = True  # the person typed into the turn while this waited
    if "pattern" in value:
        facts["matches"] = value.get("matches")
    if isinstance(value.get("total_lines"), int):
        facts["total_lines"] = value["total_lines"]
    return facts


def _shell_kill_render(arguments: dict[str, Any], value: Any) -> str:
    """One line: which background task ended, how, and its exit code."""
    if not isinstance(value, dict):
        return json.dumps(value, default=str)
    if value.get("error"):
        return f"Error: {value.get('error')}"
    task = str(value.get("task_id") or arguments.get("task_id") or "")
    status = str(value.get("status") or "killed")
    exit_code = value.get("exit_code")
    suffix = f" (exit {exit_code})" if isinstance(exit_code, int) else ""
    return f"task {task}: {status}{suffix}"


def _shell_facts(arguments: dict[str, Any], value: Any) -> dict[str, Any]:
    """What the log records about a command: its status, how the wait ended,
    and where the rest of a cut output is."""
    if not isinstance(value, dict):
        return {}
    code, output = value.get("exit_code"), value.get("output")
    cut = bool(value.get("truncated")) and isinstance(output, str)
    return {
        "exit_code": code if isinstance(code, int) else None,
        "ended": value.get("ended"),
        "task_id": value.get("task_id"),
        "omitted_lines": value.get("omitted_lines"),
        # It failed away from the session folder, after an earlier cd (`shell/cwd_drift.py`).
        "cwd_drift": True if value.get("cwd_drift") else None,
        SHOWN_CHARS: len(output) if cut else None,
        FULL_CHARS: value.get("output_chars"),
        LOCATOR: value.get("full_output"),
        **_check_facts(arguments, code, output if isinstance(output, str) else ""),
    }


def _check_facts(arguments: dict[str, Any], code: Any, output: str) -> dict[str, Any]:
    """For a check: its runner, and for a failed one how many failures its output
    names (`named`; 0 is the case the repair record cannot use), whether those are setup failures,
    and whether the command's own pipe cut what it printed."""
    command = str((arguments or {}).get("command") or "") if isinstance(arguments, dict) else ""
    runner = runner_of(command)
    if runner is None:
        return {}
    facts: dict[str, Any] = {"check": runner, "cut_by_command": cut_by_command(command) or None}
    if (isinstance(code, int) and code != 0) or reports_failure(output):
        marks = failure_marks(output)
        facts["named"] = len(marks)
        facts["setup"] = (bool(marks) and all(is_setup(mark) for mark in marks)) or None
    return facts


RUN_SHELL_CONTRACT = ToolContract(
    capabilities=SYSTEM_EXEC_CAPS,
    render=_shell_render,
    facts=_shell_facts,
    present_call=_shell_present_call,
    present_result=_shell_present_result,
)

#: The background-task helpers return dicts too; rendering them keeps the JSON
#: wrapper off the (sometimes large) retained output.
SHELL_TASK_OUTPUT_CONTRACT = ToolContract(render=_shell_task_render, facts=_task_output_facts)
SHELL_TASK_KILL_CONTRACT = ToolContract(render=_shell_kill_render)

__all__ = [
    "RUN_SHELL_CONTRACT",
    "SHELL_TASK_KILL_CONTRACT",
    "SHELL_TASK_OUTPUT_CONTRACT",
    "_shell_facts",
    "_shell_kill_render",
    "_shell_present_call",
    "_shell_present_result",
    "_shell_render",
    "_shell_result_card",
    "_shell_status",
    "_shell_task_render",
]
