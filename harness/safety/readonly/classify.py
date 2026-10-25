"""Which commands only read: the allow-list and the flags that disqualify a command.

What this accepts runs in every preset with no ask, so each verb is listed with the
flags that turn it into a writer or a runner: `sort -o`, `rg --pre`, sed's `e`, `r` and `w`, awk's
pipes. A flag nobody has looked at yet costs a question, never an unreviewed effect.
"""

from __future__ import annotations

import re

from .lexing import command_stages, without_assignments

# Commands that read local state. The ones a flag or an operand can turn into a writer or a
# runner are policed in `stage_is_readonly`.
SIMPLE_READERS = {
    "ls", "cat", "head", "tail", "wc", "nl", "sort", "uniq", "cut", "tr",
    "grep", "egrep", "fgrep", "rg", "ugrep", "file", "stat", "du", "df",
    "pwd", "echo", "printf", "which", "whoami", "id", "date", "uname",
    "basename", "dirname", "realpath", "readlink", "jq", "column", "diff",
    "comm", "strings", "md5sum", "shasum", "sha1sum", "sha256sum",
    "hexdump", "xxd", "od", "true", "false", "yamllint", "actionlint",
}

# Git subcommands that only read. Several other "read" subcommands gain a write or exec
# behaviour through particular flags, so those are handled one by one in `git_is_readonly`.
GIT_READERS = {
    "status", "log", "show", "diff", "blame", "shortlog", "describe",
    "rev-parse", "rev-list", "ls-files", "ls-tree", "grep", "cat-file",
    "name-rev", "merge-base", "count-objects", "var", "check-ignore",
}

_BRANCH_FLAGS = {
    "--show-current", "--list", "-a", "-r", "-v", "-vv", "--contains",
    "--merged", "--no-merged", "--all",
}
_TAG_FLAGS = {"-l", "--list", "-n", "--contains", "--merged"}
# `--ext-diff` runs the configured diff program, `--no-index` diffs any two paths on the disk
# (outside the repository the operands are not scoped by), and `git grep -O` opens a pager command.
_GIT_RUNNERS = ("--output", "--ext-diff", "--no-index", "-O", "--open-files-in-pager")
_FIND_WRITERS = ("-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fls", "-fprintf")
_AWK = {"awk", "gawk", "mawk", "nawk"}
# Flags, per verb, that write a file, run a program or read one the operands do not name.
_WRITING_FLAGS: dict[str, tuple[str, ...]] = {
    "sort": ("-o", "--output", "--compress-program"),
    "rg": ("--pre",),
    "ugrep": ("--filter", "--save-config"),
    "file": ("-C", "--compile"),
    "date": ("-s", "--set"),
    "jq": ("--rawfile", "--slurpfile"),
    "xxd": ("-r",),
}
# Verbs whose second operand is an output file (`uniq in out`, `xxd in out`).
_OUTPUT_OPERAND = {"uniq", "xxd"}

# A sed script that runs a command (`e`), reads another file (`r`, `R`) or writes one (`w`, `W`),
# as a command or as an `s///` flag. It errs wide, since a false hit only costs one approval.
_SED_COMMAND = re.compile(r"(^|[;{}\n/!0-9$\s])[ewWrR](\s|;|}|$)")
_SED_FLAG = re.compile(r"/[gpiImM0-9]*[ew](\s|;|}|$)")
# awk's ways out: a redirection or a pipe to a program, `system()`, a command `getline`, and the
# environment (`ENVIRON`), which is where tokens live.
_AWK_WAYS_OUT = (">", "|", "system", "getline", "ENVIRON")


def git_is_readonly(args: list[str]) -> bool:
    # Of the global flags only `-C <dir>` and `--no-pager` pass. `-c` and `--config-env` can set
    # core.pager and similar hooks that run programs.
    at = 0
    while at < len(args):
        if args[at] == "-C" and at + 1 < len(args):
            at += 2
        elif args[at] == "--no-pager":
            at += 1
        else:
            break
    if at >= len(args):
        return False
    sub, rest = args[at], args[at + 1 :]
    if any(t.startswith(_GIT_RUNNERS) for t in rest):
        return False
    if sub in GIT_READERS:
        return True
    if sub == "branch":
        return all(t in _BRANCH_FLAGS or t.startswith(("--format=", "--sort=")) for t in rest)
    if sub == "tag":
        return bool(rest) and all(t in _TAG_FLAGS or t.startswith("-n") for t in rest)
    if sub == "stash":
        return bool(rest) and rest[0] in {"list", "show"}
    if sub == "remote":
        return not rest or rest[0] in {"-v", "show", "get-url"}
    if sub == "config":
        return any(t in {"--get", "--get-all", "--get-regexp", "--list", "-l"} for t in rest)
    if sub == "reflog":
        return not rest or rest[0] == "show"
    return False


def _simple_is_readonly(head: str, args: list[str]) -> bool:
    flags = _WRITING_FLAGS.get(head, ())
    if any(t.startswith(flags) for t in args):
        return False
    # A one-letter flag can hide in a cluster: `sort -ro out` is `-r -o out`.
    letters = {flag[1] for flag in flags if len(flag) == 2}
    if any(re.fullmatch(r"-[A-Za-z]+", t) and letters & set(t[1:]) for t in args):
        return False
    if head in _OUTPUT_OPERAND and len([t for t in args if not t.startswith("-")]) > 1:
        return False
    if head == "jq" and any("env" in t.lower() for t in args):
        return False  # `env` and `$ENV` print the environment
    return True


def stage_is_readonly(argv: list[str]) -> bool:
    argv = without_assignments(argv)
    if not argv:
        return False
    head, args = argv[0], argv[1:]
    if "/" in head or "\\" in head:
        return False  # a binary named by path can be anything; only bare names are known
    if head in SIMPLE_READERS:
        return _simple_is_readonly(head, args)
    if head == "command":
        return bool(args) and args[0] in {"-v", "-V"}
    if head in ("cd", "Set-Location"):
        # It moves the shell and reads nothing; where it goes is a read target (`targets.py`).
        return len(args) == 1 and not args[0].startswith("-")
    if head == "git":
        return git_is_readonly(args)
    if head == "sed":
        if any(t.startswith(("-i", "--in-place", "-f", "--file")) for t in args):
            return False
        scripts = [t for t in args if not t.startswith("-")]
        return not any(_SED_COMMAND.search(t) or _SED_FLAG.search(t) for t in scripts)
    if head in _AWK:
        return not any(way in t for t in args for way in _AWK_WAYS_OUT)
    if head == "find":
        return not any(t.startswith(_FIND_WRITERS) for t in args)
    # Bare `env` prints the environment, API keys included, and `env CMD` runs CMD.
    return False


def expands_variable(command: str) -> bool:
    """Whether the shell would expand a variable in `command` (`$HOME`, `${X}`, PowerShell's
    `$env:X`). A variable is how a token in the environment reaches the output. Single quotes
    keep a `$` literal in both shells, so `grep 'end$'` is not one."""
    quote = ""
    for at, char in enumerate(command):
        if quote == "'":
            quote = "" if char == "'" else quote
        elif char == "'" and not quote:
            quote = "'"
        elif char == '"':
            quote = "" if quote == '"' else '"'
        elif char == "$" and at + 1 < len(command) and re.match(r"[A-Za-z0-9_{@*#?!$-]", command[at + 1]):
            return True
    return False


def is_readonly_command(command: str) -> bool:
    """True when every command `command` runs (a pipeline, a `&&` list, …) only reads."""
    text = str(command or "")
    if expands_variable(text):
        return False
    # The lexer drops a backslash, so `c\at` reads as `cat`, and PowerShell runs `c\at` as a path.
    if any("\\" in (segment.split() or [""])[0] for segment in re.split(r"\|\||&&|[|;]", text)):
        return False
    stages = command_stages(text)
    return stages is not None and all(stage_is_readonly(stage) for stage in stages)
