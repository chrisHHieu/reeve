"""Command-prefix allowances: when a shell command may run because an allowed prefix covers it.

A prefix can only vouch for the words it matched, so everything after is unexamined. Two things
follow: the unexamined tail must be able to be nothing but arguments, and the start must match the
words, not the text.
"""

from __future__ import annotations

import shlex
from pathlib import Path

# Constructs whose contents cannot be evaluated, so a command carrying one never auto-runs on a
# prefix: command or process substitution, redirection (a write the allowance never vetted) and
# variable expansion (the value was set out of view).
OPAQUE_CONSTRUCTS = ("`", "$(", "$", ">", "<", "(")

# What chains several commands into one string. Each part is checked against the allowances on its
# own. Refusing the whole command outright would block a harmless `git status && git diff` while
# still auto-allowing `find . -exec rm {} +` under a `find` prefix, since `-exec` needs no separator.
SEPARATORS = ("&&", "||", ";", "|&", "|", "&", "\n", "\r")

# Programs that run another program named in their arguments. A rule for the outer program cannot
# vouch for the inner one, so these always go to approval.
ARGUMENT_EXECUTORS = {
    "xargs", "env", "nohup", "nice", "stdbuf", "timeout", "watch", "sudo", "doas",
    "ssh", "docker", "podman", "kubectl", "npx", "pnpx", "bunx", "uvx",
}
# Interpreters given inline code, as in `python -c "..."` or `node -e "..."`.
INLINE_CODE_FLAGS = {"-c", "-e", "--eval", "--command", "-Command", "-EncodedCommand"}
INTERPRETERS = {
    "sh", "bash", "zsh", "dash", "ksh", "fish", "powershell", "pwsh", "cmd",
    "python", "python3", "node", "deno", "bun", "ruby", "perl", "php",
}
# Flags that turn a search or list tool into one that runs or deletes.
EXECUTING_FLAGS = {"-exec", "-execdir", "-delete", "-ok", "-okdir", "-fprintf"}


def split_commands(command: str) -> list[str]:
    """The parts of a compound command, split on its separators, longest first so `&&` is not read
    as two `&`. The split is textual and ignores quoting on purpose: splitting too much only makes
    more parts to justify, never fewer."""
    parts = [command]
    for separator in SEPARATORS:
        parts = [piece for part in parts for piece in part.split(separator)]
    return [part.strip() for part in parts if part.strip()]


def is_prefix_eligible(argv: list[str]) -> bool:
    """False for a command no prefix rule can vouch for, because it runs code the rule never saw:
    another program named in its arguments, inline source, or a flag that executes or deletes."""
    if not argv:
        return False
    program = Path(argv[0]).name.lower()
    program = program[:-4] if program.endswith(".exe") else program
    if program in ARGUMENT_EXECUTORS:
        return False
    if program in INTERPRETERS and any(arg in INLINE_CODE_FLAGS for arg in argv[1:]):
        return False
    return not any(arg.lower() in EXECUTING_FLAGS for arg in argv[1:])


def command_allowed(command: str, allowed_commands: list[str]) -> bool:
    """True only when every part of a (possibly compound) command is covered by an allowed prefix.

    A construct whose contents cannot be evaluated disqualifies the whole command. A compound
    command is split and each part checked alone, so `git status && git diff` runs when both are
    allowed and `git status && rm -rf ~` does not. A part that runs code named in its arguments
    (`xargs`, `sh -c`, `find -exec`, `-delete`) is never eligible, so a `find` rule cannot auto-run
    `find . -exec rm {} +`. Matching is on parsed words: `git status` covers `git status -s` and
    never `git statusfoo` or a bare `git`."""
    if not command.strip():
        return False
    if any(construct in command for construct in OPAQUE_CONSTRUCTS):
        return False
    parts = split_commands(command)
    if not parts:
        return False
    prefixes: list[list[str]] = []
    for allowed in allowed_commands:
        try:
            words = shlex.split(allowed)
        except ValueError:
            continue
        if words:
            prefixes.append(words)
    if not prefixes:
        return False
    for part in parts:
        try:
            argv = shlex.split(part)
        except ValueError:
            return False  # unbalanced quotes and the like count as not allowed
        if not argv or not is_prefix_eligible(argv):
            return False
        if not any(argv[: len(prefix)] == prefix for prefix in prefixes):
            return False
    return True
