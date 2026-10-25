"""Which shells this machine has, and which one a command is written for.

`run_shell` used to be one dialect per OS: `powershell.exe` on Windows, `/bin/bash` elsewhere. A
model writes bash far more fluently than PowerShell, so on Windows it kept sending `&&`, heredocs
and `grep | head` to a PowerShell 5.1 that cannot parse them: 68 of 2,721 `run_shell` results in
the real session store (2.5%) failed to parse or recognise the command, each one a full extra model
call. Other harnesses give the model the shells the machine has and let it pick per command;
this is the same idea kept inside the one `run_shell` tool, so approvals, hooks, the verification
ledger and the cards all keep keying on one name.

Four kinds, named the way a model names them:

  powershell  Windows PowerShell 5.1 (Windows only: every Windows machine has it)
  pwsh        PowerShell 7+ (anywhere it is installed)
  bash        `/bin/bash` on POSIX; **Git Bash** on Windows when Git for Windows is installed
  cmd         `cmd.exe` (Windows only)

Three protocols sit behind them, because what wraps a command (the exit-status trailer, the
earlier-failure probe, the environment preamble) is written once per dialect: PowerShell (both
versions), bash, and cmd. PowerShell 5.1 and 7 are two kinds, not one with a switch, because they
are not interchangeable: 5.1 has no `&&` and starts in half the time (measured: ~255 ms against
~485 ms per command), 7 prints nothing for an unclosed line.

Two traps this module exists to avoid:

  - On Windows, `bash` on PATH is usually `C:\\Windows\\System32\\bash.exe`: the WSL launcher. It
    runs a Linux filesystem where `D:\\project` does not exist. It is never accepted.
  - Git Bash is found next to Git itself, not by searching for a name that WSL also answers to.

The default is the OS's own shell (PowerShell 5.1 on Windows, bash elsewhere), so a model that never
passes `shell` behaves exactly as before. `REEVE_DEFAULT_SHELL` names another available kind.
"""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Optional

POWERSHELL = "powershell"
PWSH = "pwsh"
BASH = "bash"
CMD = "cmd"
#: The order a model is offered them in, and the order a refusal looks for a way out.
KINDS = (POWERSHELL, PWSH, BASH, CMD)

#: Other ways a model spells a kind. `pwsh` is NOT an alias for `powershell`: asking for 7 and
#: silently getting 5.1 would be a promise the machine did not keep.
ALIASES = {
    "ps": POWERSHELL,
    "powershell.exe": POWERSHELL,
    "pwsh.exe": PWSH,
    "sh": BASH,
    "git-bash": BASH,
    "gitbash": BASH,
    "cmd.exe": CMD,
}

#: Folders that hold the WSL launcher and the Store app-execution aliases, not a shell the
#: harness can use. A path under one of them is a different machine's filesystem or a stub.
_NOT_A_SHELL = ("\\windows\\system32\\", "\\windows\\syswow64\\", "\\windowsapps\\")


@dataclass(frozen=True)
class Shell:
    """One usable shell: the dialect a command is written in, and the executable that runs it."""

    kind: str
    path: str
    label: str  # what a person reads: "PowerShell 7", "PowerShell 5.1", "Git Bash", "bash", "cmd"

    @property
    def is_powershell(self) -> bool:
        """Either PowerShell: they share one protocol."""
        return self.kind in (POWERSHELL, PWSH)

    @property
    def is_modern_powershell(self) -> bool:
        return self.kind == PWSH

    @property
    def is_cmd(self) -> bool:
        return self.kind == CMD


def _not_a_shell(path: str) -> bool:
    lowered = path.replace("/", "\\").lower()
    return any(part in lowered for part in _NOT_A_SHELL)


def _git_bash_candidates(which: Callable[[str], Optional[str]], env: Mapping[str, str]) -> list[str]:
    """Where Git for Windows keeps its bash, most specific first."""
    found: list[str] = []
    override = env.get("REEVE_BASH")
    if override:
        found.append(override)
    git = which("git")
    if git:
        # <root>\cmd\git.exe, <root>\bin\git.exe or <root>\mingw64\bin\git.exe: bash is <root>\bin.
        here = Path(git).resolve().parent
        for ancestor in (here, here.parent, here.parent.parent):
            found.append(str(ancestor / "bin" / "bash.exe"))
    for variable in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
        base = env.get(variable)
        if base:
            found.append(str(Path(base) / "Git" / "bin" / "bash.exe"))
    local = env.get("LocalAppData")
    if local:
        found.append(str(Path(local) / "Programs" / "Git" / "bin" / "bash.exe"))
    return found


def find_shells(
    *,
    windows: Optional[bool] = None,
    which: Optional[Callable[[str], Optional[str]]] = None,
    exists: Callable[[str], bool] = os.path.isfile,
    env: Optional[Mapping[str, str]] = None,
) -> dict[str, Shell]:
    """The shells present, keyed by kind, in `KINDS` order.

    Names are looked up on `env`'s PATH (the process's by default), so a test or a caller can
    describe a machine without touching this one."""
    windows = (sys.platform == "win32") if windows is None else windows
    env = dict(os.environ) if env is None else env
    if which is None:
        def which(name: str) -> Optional[str]:
            return shutil.which(name, path=env.get("PATH"))

    shells: dict[str, Shell] = {}

    if windows:
        legacy = which("powershell.exe") or which("powershell") or "powershell.exe"
        if exists(legacy) or which("powershell.exe"):
            shells[POWERSHELL] = Shell(POWERSHELL, legacy, "PowerShell 5.1")

    modern = which("pwsh")
    if modern is None and windows:
        for base in (env.get("ProgramW6432"), env.get("ProgramFiles")):
            candidate = str(Path(base) / "PowerShell" / "7" / "pwsh.exe") if base else ""
            if candidate and exists(candidate):
                modern = candidate
                break
    if modern:
        shells[PWSH] = Shell(PWSH, modern, "PowerShell 7")

    if windows:
        for candidate in _git_bash_candidates(which, env):
            if candidate and not _not_a_shell(candidate) and exists(candidate):
                shells[BASH] = Shell(BASH, candidate, "Git Bash")
                break
        comspec = env.get("ComSpec") or env.get("COMSPEC")
        root = env.get("SystemRoot") or env.get("windir")
        cmd = comspec or (str(Path(root) / "System32" / "cmd.exe") if root else None) or which("cmd.exe")
        if cmd and (exists(cmd) or which("cmd.exe")):
            shells[CMD] = Shell(CMD, cmd, "cmd")
    else:
        posix = "/bin/bash" if exists("/bin/bash") else which("bash")
        if posix:
            shells[BASH] = Shell(BASH, posix, "bash")
    return {kind: shells[kind] for kind in KINDS if kind in shells}


def default_kind(*, windows: Optional[bool] = None) -> str:
    """The OS's own shell, whether or not this machine turns out to have it."""
    windows = (sys.platform == "win32") if windows is None else windows
    return POWERSHELL if windows else BASH


def normalise(kind: Optional[str]) -> str:
    """The kind a model's spelling means, or "" when it named none."""
    text = (kind or "").strip().lower()
    return ALIASES.get(text, text)


#: The kinds a person may make the default in Settings. `cmd` runs one line and no more, so it is
#: there to be asked for by a command, never to be what every command is written in.
PREFERABLE = (POWERSHELL, PWSH, BASH)
#: The person's choice in Settings (amendment of 2026-10-14), "" for the OS's own. The
#: manager sets it from its prefs; a session reads it once, when its world is built.
_preferred = ""


def set_preferred(kind: Optional[str]) -> str:
    """Make `kind` the default for worlds built from now on; anything not in `PREFERABLE` is ""."""
    global _preferred
    wanted = normalise(kind)
    _preferred = wanted if wanted in PREFERABLE else ""
    return _preferred


def preferred() -> str:
    return _preferred


def pick_default(
    shells: Mapping[str, Shell],
    *,
    windows: Optional[bool] = None,
    env: Optional[Mapping[str, str]] = None,
) -> str:
    """The kind a command runs in when it names none.

    `REEVE_DEFAULT_SHELL` when it names a shell this machine has (a developer's or a test's word
    wins); else the person's Settings choice when the machine has it; else the OS's own; else the
    first shell there is (Windows with no 5.1 falls to PowerShell 7, a box with only `pwsh` to
    that). Empty when there is no shell at all."""
    env = os.environ if env is None else env
    for wanted in (normalise(env.get("REEVE_DEFAULT_SHELL")), _preferred):
        if wanted and wanted in shells:
            return wanted
    own = default_kind(windows=windows)
    if own in shells:
        return own
    return next(iter(shells), own)


def alternative(shell: Shell, shells: Mapping[str, Shell]) -> Optional[str]:
    """The shell a refusal in `shell` should point the model at, when the machine has one.

    A PowerShell refusal is about bash syntax far more often than the reverse, so it points at bash
    first, then at 7 (which has `&&`); bash points at PowerShell; cmd at whichever is richest."""
    order = {
        POWERSHELL: (BASH, PWSH),
        PWSH: (BASH,),
        BASH: (POWERSHELL, PWSH),
        CMD: (BASH, POWERSHELL, PWSH),
    }.get(shell.kind, ())
    return next((kind for kind in order if kind in shells), None)


_cache: Optional[dict[str, Shell]] = None


def local_shells() -> dict[str, Shell]:
    """This machine's shells, found once per process: a shell does not appear mid-session, and the
    schema the model sees must not change between two requests."""
    global _cache
    if _cache is None:
        _cache = find_shells()
    return _cache


def forget_local_shells() -> None:
    """For tests and for a person who just installed Git: look again."""
    global _cache
    _cache = None


def default_shell_path(kind: Optional[str] = None) -> str:
    """The executable for a persistent terminal or a hook, from the same finder `run_shell` uses.

    Falls back to the OS's classic name when nothing was found (a minimal container with no bash
    on a path we know): the caller then fails to start with the OS's own message, as before."""
    shells = local_shells()
    kind = normalise(kind) or pick_default(shells)
    found = shells.get(kind)
    if found:
        return found.path
    if kind == POWERSHELL:
        return "powershell.exe" if sys.platform == "win32" else "pwsh"
    if kind == CMD:
        return "cmd.exe"
    return "/bin/bash"
