"""Which shell commands are checks, which runner each one calls, and whether a pipe cuts its output.

A check is a command that runs a test suite, a type checker, a linter or a build, so that what it
prints says whether the work is right. The verification ledger (`loop/verification.py`), the shell's
status line (`tools/shell_status.py`), the repair record and the harness review all ask
this one question, so it lives here, below all of them.
"""

from __future__ import annotations

import re
from typing import Optional

#: `(runner, pattern)`: a command is a check when one matches; the runner is the name the
#: repair record and the harness review count by. Order only matters for naming: the runner a
#: command is filed under is the one whose match starts first. The set is the ledger's as it was,
#: plus Node's own runner, which the store showed checks were run with (2026-10-09).
_RUNNERS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (name, re.compile(pattern))
    for name, pattern in (
        ("pytest", r"\bpytest\b"),
        ("unittest", r"\bpython\d?\s+-m\s+unittest\b"),
        ("ruff", r"\bruff\b"),
        ("mypy", r"\bmypy\b"),
        ("pyright", r"\bpyright\b"),
        ("tsc", r"\btsc\b"),
        ("eslint", r"\beslint\b"),
        ("biome", r"\bbiome\b"),
        ("vitest", r"\bvitest\b"),
        ("jest", r"\bjest\b"),
        ("mocha", r"\bmocha\b"),
        ("playwright", r"\bplaywright\s+test\b"),
        # Node's own runner: `node --test`, with or without loader flags before it.
        ("node-test", r"\bnode\b[^|;&\n]*\s--test\b"),
        ("package-script", r"\b(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?(?:test|tests|lint|typecheck|build|check)\b"),
        ("cargo", r"\bcargo\s+(?:test|check|build)\b"),
        ("go", r"\bgo\s+(?:test|check|build)\b"),
        ("dotnet", r"\bdotnet\s+(?:test|check|build)\b"),
        ("maven", r"\bmvn\s+(?:test|check|build)\b"),
        ("gradle", r"\bgradle\s+(?:test|check|build)\b"),
        ("make", r"\bmake\s+\S*test\S*\b"),
    )
)

#: A runner the list above does not name, called the way most runners are: `<tool> test`,
#: `<tool> run lint`, `<tool> exec check` (`deno test`, `swift test`, `mix test`, `just check`,
#: `bundle exec rspec` is named below by its runner). The runner is the tool's own name. The list
#: above stays for the runners called by their name alone, and names what it already named; this
#: is what keeps a runner nobody listed from never being a check. `build` is not one of
#: the verbs here: `docker build` checks nothing.
_BY_VERB = re.compile(
    r"(?<![\w./\\-])(?P<tool>[A-Za-z][\w.-]*)\s+(?:(?:run|exec|x)\s+)?(?:test|tests|check|lint|typecheck)(?![\w.-])"
)
#: Commands that put the word in front of a file or a string, and run nothing: `echo test`,
#: `cat check`, `cd test`, `git check-ignore` (the hyphen already keeps that one out).
_NOT_RUNNERS = frozenset({
    "echo", "printf", "cat", "type", "less", "more", "head", "tail", "cd", "pushd", "ls", "dir",
    "mkdir", "rmdir", "rm", "del", "touch", "cp", "mv", "copy", "move", "grep", "rg", "findstr",
    "find", "which", "where", "git", "code", "open", "start", "set", "export",
})

#: A stage after the check that keeps only part of what it printed. The harness already bounds a
#: long output and keeps the whole of it in a file; a pipe through one of these throws the rest
#: away before the harness sees it. Measured on the store (2026-10-09): 20 of the 33 failed checks
#: whose failures had no name were piped through one of these.
_CUTTER = re.compile(
    r"\|\s*(?:tail|head|grep|egrep|findstr|more|less|sls|Select-String"
    r"|Select-Object\s+(?:-Last|-First|-Index|-Skip)|select\s+-(?:last|first))\b",
    re.IGNORECASE,
)


#: A step that installs packages: a runner's name inside it is a package, not a check.
#: `uv pip install ... pytest` was filed as a failed pytest run (2026-10-19), one of the five
#: "failed checks that name no failure" that refuted a claim about naming failures.
_INSTALL_STEP = re.compile(
    r"\b(?:(?:uv\s+)?pip3?\s+install|uv\s+(?:add|tool\s+install)|poetry\s+add|pipx\s+install"
    r"|conda\s+install|(?:npm|pnpm|bun)\s+(?:install|i|add)|yarn\s+(?:add|global\s+add)"
    r"|cargo\s+install|brew\s+install|choco\s+install|winget\s+install|go\s+install)\b",
    re.IGNORECASE,
)
#: Where one step of a command line ends and the next begins.
_STEP_BREAK = re.compile(r"&&|\|\||[;|\n]")


def _installing(text: str, at: int) -> bool:
    """Whether position `at` lies inside a step of `text` that installs packages."""
    begun = 0
    for brk in _STEP_BREAK.finditer(text, 0, at):
        begun = brk.end()
    return _INSTALL_STEP.search(text[begun:at]) is not None


def _matches(text: str) -> list[tuple[int, int, str]]:
    """`(start, end, runner)` for the first match of each runner in `text`; the runners the list
    names first, and a runner found by its verb only when none of those is."""
    found: list[tuple[int, int, str]] = []
    for name, pattern in _RUNNERS:
        for match in pattern.finditer(text):
            if not _installing(text, match.start()):
                found.append((match.start(), match.end(), name))
                break
    if found:
        return found
    for match in _BY_VERB.finditer(text):
        tool = match.group("tool").lower()
        if tool not in _NOT_RUNNERS and not _installing(text, match.start()):
            return [(match.start(), match.end(), tool)]
    return []


def _starts(text: str) -> list[tuple[int, str]]:
    return [(start, name) for start, _end, name in _matches(text)]


def runner_args(command: str) -> Optional[tuple[str, list[str]]]:
    """The runner a check calls and the words after it in its own step (`pytest -q tests/a.py` is
    `("pytest", ["-q", "tests/a.py"])`), or None for a command that is not a check. What a run looks
    at is read from these, whichever runner it is."""
    text = command or ""
    found = _matches(text)
    if not found:
        return None
    _start, end, name = min(found)
    words = _STEP_BREAK.split(text[end:], maxsplit=1)[0].split()
    # `vitest run`, `jest exec`: the subcommand the verb rule also allows, not a path.
    while words and words[0] in ("run", "exec"):
        words.pop(0)
    return name, words


def is_check_command(command: str) -> bool:
    """Whether a shell command runs a check."""
    return bool(_starts(command or ""))


def runner_of(command: str) -> Optional[str]:
    """The runner a check calls (the first one in the line), or None for a command that is not a
    check. `cd backend; UV_X=1 uv run pytest -q` and `pytest tests/` are both `pytest`."""
    found = _starts(command or "")
    return min(found)[1] if found else None


#: How long a command is when it is said (a note to the model, the turn's receipt, a repair's
#: record): enough to recognise it, and a heredoc or a long chain of steps does not fill a note.
SHOWN_CHARS = 120


def shown_command(command: str, limit: int = SHOWN_CHARS) -> str:
    """A command as it is said to a reader: whitespace collapsed, and a long one kept at both ends
    with `…` between, since what it runs is as often at its end (`cd <folder>; … pytest -q`) as at
    its start. Only for saying it: what a command is (a check or not, which runner, the same run
    as another) is always read on the whole of it. Reading the first 120 characters hid 114 of
    618 checks in the session store (2026-10-23)."""
    text = " ".join(str(command or "").split())
    if len(text) <= limit:
        return text
    head = (limit - 1) // 2
    return f"{text[:head]}…{text[len(text) - (limit - 1 - head):]}"


def cut_by_command(command: str) -> bool:
    """Whether a check's own command line pipes its output through a stage that keeps only part of
    it, after the check runs."""
    text = command or ""
    found = _starts(text)
    return bool(found) and _CUTTER.search(text, min(found)[0]) is not None


__all__ = ["SHOWN_CHARS", "cut_by_command", "is_check_command", "runner_args", "runner_of", "shown_command"]
