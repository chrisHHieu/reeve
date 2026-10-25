"""What `run_shell` tells the model about the shell(s) it is writing for.

Split from `shell.py` (size ceiling): this is words, not mechanism. The schema's base text carries
`WINDOWS_NOTE`, the sentence that was true when there was one dialect per OS; `shell_note` is what
replaces it once the machine's real shells are known.
"""

from __future__ import annotations

from .shell_kinds import BASH, CMD, POWERSHELL, PWSH, Shell

#: What the model is told when this machine offers it no choice. `run_shell_schema` swaps it for
#: the right note when there are several shells or the only PowerShell is 7.
WINDOWS_NOTE = (
    "On Windows the session is PowerShell 5.1: `&&` does not exist (parse errors are "
    "reported as errors with the fix — sequence with `;` instead), and environment "
    "variables read as $env:NAME. "
)

#: One line per kind: the dialect's own facts, and what it is for. Said only for shells that exist.
_ABOUT = {
    POWERSHELL: "has no `&&` (sequence with `;`), variables read as $env:NAME",
    PWSH: "`&&` works, variables read as $env:NAME",
    BASH: "git, grep/sed/awk, curl, pipelines, heredocs and `&&`; variables read as $NAME",
    CMD: "batch files and legacy tools; one line, variables read as %NAME%",
}


def shell_note(shells: list[Shell], default: str) -> str:
    """The sentence(s) that tell the model which dialect(s) it is writing for."""
    if len(shells) == 1:
        only = shells[0]
        if only.is_modern_powershell:
            return f"{only.label}: `&&` and `||` work, environment variables read as $env:NAME. "
        return WINDOWS_NOTE
    lines = "; ".join(
        f"`{shell.kind}` ({shell.label}{', the default' if shell.kind == default else ''}: "
        f"{_ABOUT.get(shell.kind, '')})"
        for shell in shells
    )
    kinds = {shell.kind for shell in shells}
    admin = (
        " PowerShell suits Windows administration (registry, services, Get-*) and .NET."
        if kinds & {POWERSHELL, PWSH}
        else ""
    )
    windows_paths = " A bash command can name Windows paths as D:/dir." if BASH in kinds and (kinds & {POWERSHELL, PWSH, CMD}) else ""
    return (
        f"Shells available: {lines}. Pass `shell` to choose, and write the command in "
        f"that shell's language.{admin}{windows_paths} "
    )
