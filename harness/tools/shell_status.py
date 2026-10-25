"""How a finished shell command's status is read (split from `shell.py`, size ceiling).

The trailer line (`<marker> <exit> <details…> <cwd>`), the exit a reader should believe
for a pipeline or a PowerShell line, a parse failure that never ran anything, and an
exit 0 whose own output reports a failed check. Pure functions over text.
"""

from __future__ import annotations

import re
from typing import Optional

from .checks import cut_by_command, failure_marks, failure_summary, is_check_command as is_verification_command
from .shell_kinds import Shell
from .shell_sequence import sequence_failure


#: A PowerShell PARSE failure never runs a native command, so `$LASTEXITCODE` is never
#: set — the trailer then reports 0 (or whatever the PREVIOUS command left behind) while
#: the transcript carries the error record. Measured live (session a5a5ce3d): `cd "x" &&
#: python a.py` on Windows PowerShell 5.1 printed "The token '&&' is not a valid statement
#: separator" and the executor reported exit_code 0, status ok. These signatures convert
#: that verdict to the failure it is, with the fix the model needs in the error text.
_PARSE_ERROR_SIGNATURES = (
    "is not a valid statement separator in this version",
    "ParserError:",
)

#: bash refusing a line before running any of it. The same shape as a PowerShell parse failure:
#: nothing ran, so the exit code says little and the words are the diagnosis.
_BASH_PARSE_SIGNATURES = (
    "syntax error near unexpected token",
    "syntax error: unexpected end of file",
    "unexpected EOF while looking for matching",
)

#: PowerShell speaking about bash-only syntax it cannot read (a heredoc, `< file`, `cmd 2>&1 |`).
_POWERSHELL_FOREIGN_SYNTAX = (
    "operator is reserved for future use",
    "Missing file specification after redirection",
)

#: Programs a POSIX-trained model reaches for that PowerShell does not have as commands.
#: `cat`, `ls`, `rm`, `curl` and friends are PowerShell ALIASES, so they never appear here.
_POSIX_TOOLS = frozenset(
    "grep sed awk head tail wc xargs touch which chmod chown sudo source export "
    "sort uniq cut tr tee basename dirname readlink stat file diff patch unzip".split()
)
#: 5.1 says "as the name of a cmdlet", 7 says "as a name of a cmdlet"; cmd says "as an internal or
#: external command".
_UNRECOGNISED = re.compile(
    r"(?:The term '([^']+)' is not recognized as (?:the|a) name of a cmdlet"
    r"|'([^']+)' is not recognized as an internal or external command)"
)

#: cmd refusing a line (it reports and carries on to its trailer, so the exit code is 1, not a parse
#: failure's absence): said in words, never in a structured status.
_CMD_PARSE_SIGNATURES = (
    "The syntax of the command is incorrect",
    "was unexpected at this time",
    "The system cannot find the batch label",
)


def _switch_to(other: Optional[str]) -> str:
    return f' (or run it with shell="{other}")' if other else ""


def _parse_error_hint(
    output: str, shell: Optional[Shell] = None, other: Optional[str] = None
) -> Optional[str]:
    """What a refused command line needs, when the shell never ran any of it.

    `shell` is the dialect it was run in (PowerShell when not given, which is what this read for
    before there were two) and `other` the kind of the other shell this machine has, so the hint
    can name the way out that costs the model one argument instead of a rewrite."""
    if shell is not None and shell.is_cmd:
        if any(signature in output for signature in _CMD_PARSE_SIGNATURES):
            return (
                "cmd could not parse this command line (one line only; a multi-line command runs as "
                f"a batch file; quote paths with spaces){_switch_to(other)}"
            )
        return None
    if shell is not None and not shell.is_powershell:
        if any(signature in output for signature in _BASH_PARSE_SIGNATURES):
            return (
                "bash could not parse this command line — nothing ran. If it is PowerShell "
                f"syntax, run it as PowerShell{_switch_to(other)}"
            )
        return None
    if any(signature in output for signature in _POWERSHELL_FOREIGN_SYNTAX):
        return (
            "PowerShell cannot read this: it has no `<<` heredoc or `< file` input redirect. "
            "Pipe a here-string (@'…'@ | cmd) or write the file first"
            f"{_switch_to(other)}"
        )
    if not any(signature in output for signature in _PARSE_ERROR_SIGNATURES):
        return None
    if "statement separator" in output:
        return (
            "the shell could not parse this command line: this PowerShell has no `&&` — "
            "sequence commands with `;` instead (cd path ; python script.py)"
            f"{_switch_to(other)}"
        )
    return "the shell could not parse this command line; fix the syntax and run again"


def _silent_exit_hint(output: str, shell: Optional[Shell]) -> Optional[str]:
    """PowerShell that exited without a word and without ever running the command.

    PowerShell 7 reports no parse error at all for an INCOMPLETE line (an unclosed `{`, `(` or
    quote): it exits 1 with empty output (measured, 7.6). Only said for a shell that ran nothing,
    so a command that really printed nothing is never blamed."""
    if output.strip() or (shell is not None and not shell.is_powershell):
        return None
    return (
        "PowerShell exited without running this command and said nothing — usually an "
        "unclosed `{`, `(` or quote. Check the brackets and quotes balance, then run it again"
    )


def _unknown_tool_hint(output: str, shell: Optional[Shell], other: Optional[str]) -> Optional[str]:
    """A POSIX tool named in PowerShell or cmd, when bash is right there. Said only for a name that
    is certainly a POSIX program: a typo'd cmdlet gets no advice it cannot use."""
    if other != "bash" or (shell is not None and shell.kind == "bash"):
        return None
    where = shell.label if shell is not None else "PowerShell"
    for match in _UNRECOGNISED.finditer(output):
        name = match.group(1) or match.group(2)
        if name in _POSIX_TOOLS:
            return (
                f"`{name}` is a POSIX program and this command ran in {where}: "
                'run it with shell="bash", or use the equivalent'
            )
    return None


def _parse_exit_code(line: str, marker: str) -> Optional[int]:
    parts = line.strip().split()
    try:
        return int(parts[parts.index(marker) + 1])
    except (ValueError, IndexError):
        return None


_DETAIL_PREFIXES = ("ps=", "native=", "err=", "cut=")


def _detail_tokens(line: str, marker: str) -> list[str]:
    """The `name=value` tokens between the exit code and the cwd."""
    parts = line.strip().split()
    try:
        rest = parts[parts.index(marker) + 2 :]
    except ValueError:
        return []
    tokens: list[str] = []
    for token in rest:
        if not token.startswith(_DETAIL_PREFIXES):
            break
        tokens.append(token)
    return tokens


def _parse_details(line: str, marker: str) -> dict[str, str]:
    """`{"ps": "1,0", "err": "3@0-13"}` — what the shell knows beyond the last
    statement's status, by name. An empty value is absent."""
    details: dict[str, str] = {}
    for token in _detail_tokens(line, marker):
        name, _, value = token.partition("=")
        if value:
            details[name] = value
    return details


def _parse_cwd(line: str, marker: str) -> Optional[str]:
    parts = line.strip().split()
    try:
        rest = parts[parts.index(marker) + 2 :]
    except ValueError:
        return None
    rest = rest[len(_detail_tokens(line, marker)) :]
    return " ".join(rest) or None


#: A stage killed by SIGPIPE because a later stage stopped reading (`yes | head -1`) did
#: nothing wrong; only the LAST stage's 141 is a real failure.
_SIGPIPE = 141


def _masked_failure_note(output: str) -> Optional[str]:
    """The note for an exit 0 whose own output says a check failed.

    The shell now reports an earlier failing program itself (`shell_sequence`), so this
    reads only what the trailer still cannot see: a failure in a bash pipeline that is
    not the line's last, in the non-final part of an `&&` list, or before a statement
    that reads the status and then lets it go. The runner's own summary still says so;
    this puts it on the status line the model reads first. The verification ledger reads
    the same summary as a failure; this note only stops the model from reading the exit
    code alone."""
    summary = failure_summary(output)
    if summary is None:
        return None
    return (
        f"but the output reports a failure: {summary!r} — a command earlier in this "
        "line failed and a later one succeeded; run that check on its own for its status"
    )


def _cut_check_note(command: str, output: str, failed: bool) -> Optional[str]:
    """The note for a failed check whose own pipe left nothing that names what failed.

    The harness bounds a long output itself and keeps the whole of it in a file; a `| tail` or a
    `| Select-Object -Last 10` throws the rest away first. Measured on the store (2026-10-09): 20 of
    the 33 failed checks that named nothing were piped that way, so neither the model nor the
    repair record could tell which test or file failed."""
    if not failed or not cut_by_command(command) or failure_marks(output):
        return None
    return (
        "the pipe after this check cut its output and what is left names no failing test or "
        "file; the harness bounds long output itself and keeps all of it in a file, so run the "
        "check without the pipe to see what failed"
    )


def _pipeline_exit(code: Optional[int], detail: str) -> tuple[Optional[int], Optional[str]]:
    """The exit status a reader should believe, and why it differs from the last
    statement's when it does.

    bash (`ps=a,b,…`, a real pipeline only — `if`/`!` leave a PIPESTATUS that disagrees
    with `$?`): the FIRST failing stage wins, so `pytest | tail` reports pytest.
    PowerShell (`native=N`): a native program's non-zero code wins over a last statement
    that succeeded, so `pyright *> log; Get-Content log` reports pyright.
    """
    if code is None:
        return None, None
    name, _, raw = detail.partition("=")
    if name == "ps":
        try:
            stages = [int(value) for value in raw.split(",") if value]
        except ValueError:
            return code, None
        if len(stages) < 2:
            return code, None
        last = len(stages) - 1
        for index, status in enumerate(stages):
            if status and not (status == _SIGPIPE and index < last):
                if index == last:
                    return status, None
                return status, (
                    f"stage {index + 1} of {len(stages)} exited {status}; "
                    f"the pipeline's last stage exited {stages[last]}"
                )
        return code, None
    if name == "native" and code == 0:
        try:
            native = int(raw)
        except ValueError:
            return code, None
        if native:
            return native, (
                f"a program in this command exited {native}, though its last "
                "statement succeeded"
            )
    return code, None


def _ended_by_its_pipeline(details: dict[str, str]) -> bool:
    """Whether a program in the line was ended by a later stage of its own pipeline.

    PowerShell 5.1: the line reported `cut=1` (a `Select-Object -First/-Index` ended a program that
    read -1; see `shell_sequence`). bash: a stage that is not the last died of SIGPIPE (141) because
    a later one stopped reading (`pytest | head`)."""
    if details.get("cut"):
        return True
    try:
        stages = [int(value) for value in details.get("ps", "").split(",") if value]
    except ValueError:
        return False
    return len(stages) > 1 and any(status == _SIGPIPE for status in stages[:-1])


def _status_of_a_cut_program(command: str) -> tuple[Optional[int], Optional[str]]:
    """What a line whose program its own pipeline ended reports.

    The program's own status is not known: PowerShell 5.1 says -1 whatever it would have exited
    with, and a SIGPIPE'd stage never got to say. For a listing or a log tail that is the point of
    `| Select-Object -First 20` / `| head`, and the line is what its last stage says: 0. For a CHECK
    it is not a result at all: the suite was ended before it finished, and booking it as passed (or,
    as before, as failed with `exit -1`) would be a claim nobody measured, so it is `unknown`."""
    if is_verification_command(command):
        return None, (
            "this check was ended by the pipe after the lines it asked for, before it finished: "
            "its result is unknown (neither passed nor failed). Run it without "
            "`| Select-Object -First` / `| head`, or take the tail instead"
        )
    return 0, None


def _reported_exit(
    code: Optional[int], details: dict[str, str], command: str
) -> tuple[Optional[int], Optional[str]]:
    """The exit status a reader should believe for a whole command line, and why it
    differs from the last statement's when it does.

    The first failure wins, as it does inside a pipeline: a program that failed earlier
    in the line (`err`, `shell_sequence`) over one that failed inside the last statement
    (`ps` / `native`, `_pipeline_exit`). A last statement that failed on its own keeps its
    own status."""
    if code is None:
        return None, None
    # PowerShell 5.1 reads a cut program's -1 as the whole line's status when `$?` is false, so a
    # `cut=1` line arrives as -1 and not as the last statement's 0. That -1 is the PIPE's doing, not a
    # failure of the line, so everything below reasons about the status without it.
    cut = bool(details.get("cut"))
    status = 0 if (code == -1 and cut) else code
    if status == 0 and "err" in details:
        earlier = sequence_failure(details["err"], command)
        if earlier is not None:
            return earlier
    if status == 0 and _ended_by_its_pipeline(details):
        return _status_of_a_cut_program(command)
    code = status
    for name in ("ps", "native"):
        if name in details:
            return _pipeline_exit(code, f"{name}={details[name]}")
    return code, None
