"""The line each dialect appends after a command to report how it ended (split from `shell.py`, size
ceiling).

One line, `<marker> <exit> <details…> <cwd>`, parsed by `shell_status._parse_exit_code` /
`_parse_details` / `_parse_cwd`. It reads the exit status of the *preceding* command, so it must run
as its own statement right after it.

`<details…>` are what the shell knows beyond the last statement's status, and
`shell_status._reported_exit` decides from them: bash's `PIPESTATUS` (`ps=1,0`), because `$?` of
`pytest | tail -5` is tail's; PowerShell's `$LASTEXITCODE` (`native=1`), because
`pyright; Get-Content log` succeeds on its last statement after pyright failed; an earlier
statement's failure (`err=`, `shell_sequence`), because `ruff check .; pytest -q` succeeds on its last
statement after ruff failed; and a program ended by its own pipeline (`cut=1`). Found by the
2026-09-25 audit: 27 of 103 checks read as passing while their output said `N failed`, which blinded
every gate the verification ledger feeds.
"""

from __future__ import annotations

from .shell_cmd import cmd_trailer
from .shell_kinds import Shell


def trailer(marker: str, shell: Shell, *, windows: bool) -> str:
    """The trailer for `shell`; `windows` says whether bash is Git Bash (which spells paths its own way)."""
    if shell.is_cmd:
        return cmd_trailer(marker)  # a suffix on the same line: shell_cmd.py says why
    if shell.is_powershell:
        # `cut=1`: a program was ended by `Select-Object -First/-Index` and read -1. Only 5.1 does
        # that: 7 waits for the program and keeps its real exit code (shell_sequence.py).
        cut = (
            ""
            if shell.is_modern_powershell
            else '$(if ($__cwCutAny -or ($__cwCutIn -and $__cwNative -eq -1)) {"cut=1 "})'
        )
        # PowerShell: `$?` is the success bool; `$LASTEXITCODE` is the exit code of the
        # last native program. Success → 0; else the program's code, falling back to 1.
        # A wrapped line (`shell_sequence`) ran under Invoke-Expression, whose own `$?`
        # is not the command's: the command's last statement left its status in
        # `$__cwOkIn` / `$__cwNatIn`. PS 5.1 fails `$?` on an exit-0 program's
        # redirected stderr: that is success.
        return (
            "$__cwOk = $?; $__cwNative = $global:LASTEXITCODE\n"
            "if ($null -ne $__cwOkIn) { $__cwOk = $__cwOkIn; $__cwNative = $__cwNatIn }\n"
            "if (!$__cwOk -and !$__cwNative -and \"$($Error[0].FullyQualifiedErrorId)\" "
            "-like 'NativeCommandError*') { $__cwOk = $true }\n"
            f'"`n{marker} '
            "$(if ($__cwOk) {0} else { if ($__cwNative) {$__cwNative} else {1} }) "
            "native=$(if ($__cwNative) {$__cwNative} else {0}) "
            "$(if ($__cwErr) {\"err=$__cwErr@$__cwAt \"})"
            f'{cut}$($PWD.Path)"\n'
        )
    # Git Bash's `$PWD` is `/d/Project/x`, which nothing outside it can start a process in:
    # the trailer's cwd is the next command's working directory in ANY dialect, so there it
    # is `pwd -W`, the Windows spelling (`D:/Project/x`).
    where = '"$(pwd -W)"' if windows else '"$PWD"'
    return (
        '__cw_rc=$? __cw_ps="${PIPESTATUS[*]}"\n'
        f'printf "\\n%s %s ps=%s err=%s %s\\n" "{marker}" "$__cw_rc" '
        f'"${{__cw_ps// /,}}" "${{__cw_err:-}}" {where}\n'
    )
