"""Splitting a command line into the commands it runs, refusing every operator that could do more."""

from __future__ import annotations

import re
import shlex

# A leading `NAME=value` is an inert environment assignment (`LC_ALL=C grep …`) only for the
# variables that shape output. Others make a reader run a program: `GIT_EXTERNAL_DIFF=sh git diff`,
# `GIT_PAGER`, `LESSOPEN`. An assignment outside this list stays in place and fails as the head.
ENV_ASSIGNMENT = re.compile(r"^(LC_[A-Z]+|LANG|LANGUAGE|TZ|NO_COLOR|COLUMNS|TERM)=[^;&|<>`]*$")

# `a; b`, `a && b` and `a || b` run each command, so a list of reads is a read. `&` puts one in
# the background and `|&` pipes its stderr; both refuse.
_SEQUENCE = {";", "&&", "||"}
_REFUSED = {"&", "|&"}
_REDIRECT_CHARS = {">", "<", "&", "0", "1", "2"}
# `2>&1` joins two streams and opens no file, the one redirection a read may carry.
_STREAM_JOIN = re.compile(r"[0-9]?>&[0-9]-?(?=$|[\s|;&])")


def without_assignments(argv: list[str]) -> list[str]:
    start = 0
    while start < len(argv) and ENV_ASSIGNMENT.match(argv[start]):
        start += 1
    return argv[start:]


def _is_redirection(token: str) -> bool:
    return bool(token) and set(token) <= _REDIRECT_CHARS and any(c in token for c in "<>&")


def _without_stream_joins(command: str) -> str:
    """`command` with every unquoted `2>&1` taken out. A quoted one is a grep pattern's, and
    taking it out would make the next operand read as the pattern instead of a file."""
    out: list[str] = []
    quote = ""
    at = 0
    while at < len(command):
        char = command[at]
        if not quote and (at == 0 or command[at - 1].isspace()):
            joined = _STREAM_JOIN.match(command, at)
            if joined:
                at = joined.end()
                continue
        if quote == "'":
            quote = "" if char == "'" else quote
        elif char == "'" and not quote:
            quote = "'"
        elif char == '"':
            quote = "" if quote == '"' else '"'
        out.append(char)
        at += 1
    return "".join(out)


def command_stages(command: str) -> list[list[str]] | None:
    """Every command `command` runs, as argument lists in order, or None when it is refused.

    `|`, `;`, `&&` and `||` separate commands, and `2>&1` is dropped. Every other operator (`&`,
    a redirection to a file, `<<`, a substitution, a parenthesis, a line break) refuses the whole
    command, and so does an empty command."""
    if not command or not command.strip():
        return None
    # A substitution can sit inside double quotes, which the tokenizer strips, so the raw text is
    # checked. That also refuses a literal `$(` in a grep pattern, which is the intended trade.
    if any(marker in command for marker in ("`", "$(", "<(", ">(")):
        return None
    # A line break ends a command in both shells, and the tokenizer reads it as a space:
    # `git status\nrm -rf ~` lexed as one `git status` with three pathspecs.
    if "\n" in command or "\r" in command:
        return None
    lexer = shlex.shlex(_without_stream_joins(command), posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError:
        return None  # unbalanced quotes and the like
    stages: list[list[str]] = [[]]
    for token in tokens:
        if token == "|" or token in _SEQUENCE:
            stages.append([])
        elif token in _REFUSED or _is_redirection(token) or set(token) <= {"(", ")"}:
            # A bare parenthesis is a subshell in bash and an evaluated expression in PowerShell
            # (`ls (Remove-Item x)` runs the removal), so it refuses like any other operator.
            return None
        else:
            stages[-1].append(token)
    return None if any(not stage for stage in stages) else stages
