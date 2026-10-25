"""Reading file paths out of a shell command, without trying to understand the command."""

from __future__ import annotations

import shlex
from pathlib import Path

# Each fetcher and the flags whose value is the output path. `curl -O` takes no value and saves
# under the URL's own name, so it is handled apart. Case matters for the Unix tools (`curl -o FILE`
# and `-O` differ), so only the PowerShell names are folded, and their entries are lowercase.
_OUTPUT_FLAGS = {
    "curl": {"-o", "--output"},
    "wget": {"-O", "--output-document"},
    "invoke-webrequest": {"-outfile"},
    "iwr": {"-outfile"},
}
_CASE_INSENSITIVE = {"invoke-webrequest", "iwr"}

# Programs whose real input is a file the command line never names; without this `make deploy`
# would look like it touches nothing.
_IMPLICIT_INPUTS: dict[str, tuple[str, ...]] = {
    "make": ("Makefile", "makefile", "GNUmakefile"),
    "npm": ("package.json",),
    "pnpm": ("package.json",),
    "yarn": ("package.json",),
    "bun": ("package.json",),
    "pytest": ("conftest.py",),
    "tox": ("tox.ini",),
    "nox": ("noxfile.py",),
    "docker-compose": ("docker-compose.yml", "docker-compose.yaml", "compose.yaml", "compose.yml"),
}

# A bare token with no path separator is worth resolving as a file when it ends in one of these.
_FILE_SUFFIXES = {
    ".py", ".sh", ".bash", ".zsh", ".js", ".mjs", ".cjs", ".ts", ".rb", ".pl",
    ".php", ".ps1", ".bat", ".cmd", ".jar", ".exe", ".json", ".yml", ".yaml",
    ".ini", ".toml", ".cfg", ".mk",
}


def _looks_like_path(token: str) -> bool:
    if not token or token.startswith("-") or "://" in token:
        return False
    if "/" in token or "\\" in token:
        return True
    return Path(token).suffix.lower() in _FILE_SUFFIXES


def _program(argv: list[str]) -> str:
    name = Path(argv[0]).name.lower()
    return name[:-4] if name.endswith(".exe") else name


def _sub_commands(command: str) -> list[list[str]]:
    """Each part of a compound command as arguments. The split is textual and errs toward more
    parts (see `permissions.split_commands`): extra parts can only surface more paths."""
    from ..permissions import split_commands

    parts: list[list[str]] = []
    for text in split_commands(command):
        try:
            argv = shlex.split(text)
        except ValueError:
            argv = text.split()  # unbalanced quotes; still worth scanning for paths
        if argv:
            parts.append(argv)
    return parts


def command_paths(command: str) -> list[str]:
    """Every path a shell command names, and the files it would read without naming them.

    It does not try to work out which token is "the script": every path-like token is returned and
    checked. Understanding a command from its text cannot be done reliably, so this stays free of
    semantics. A file brought in only through an import or include (the agent writes `helper.py`
    and runs `main.py`) is a known miss, and no cheap analysis would find it."""
    found: list[str] = []
    for argv in _sub_commands(command):
        found.extend(token for token in argv[1:] if _looks_like_path(token))
        program = _program(argv)
        if program == "docker" and len(argv) > 1 and argv[1].lower() == "compose":
            program = "docker-compose"
        found.extend(_IMPLICIT_INPUTS.get(program, ()))
        if _looks_like_path(argv[0]):
            found.append(argv[0])  # ./run.sh
    return found


def fetched_paths(command: str) -> list[str]:
    """Where fetch commands save their output. `curl URL | sh` writes no file and needs no entry,
    because a pipe already keeps a command from a prefix allowance, so it is gated as it is."""
    saved: list[str] = []
    for argv in _sub_commands(command):
        program = _program(argv)
        flags = _OUTPUT_FLAGS.get(program)
        if not flags:
            continue
        fold = program in _CASE_INSENSITIVE
        for at, token in enumerate(argv[1:], start=1):
            if (token.lower() if fold else token) in flags and at + 1 < len(argv):
                saved.append(argv[at + 1])
        if program == "curl" and "-O" in argv[1:]:
            for candidate in argv[1:]:
                if "://" in candidate:
                    name = candidate.split("?")[0].rstrip("/").rsplit("/", 1)[-1]
                    if name:
                        saved.append(name)
                    break
    return saved
