"""A command LINE's failure: which earlier statement failed when a later one succeeded.

The trailer reads the status of a line's LAST statement. `_pipeline_exit` already sees
inside that statement (bash's `PIPESTATUS`, PowerShell's leftover `$LASTEXITCODE`), but a
program that fails EARLIER in the line and is followed by one that succeeds leaves no
trace in either: `ruff check .; pytest -q` with a red ruff and a green pytest reads exit 0.
The verification ledger then books a passing check, and every gate it feeds (the
red-check note, the check-loop stop, a goal's proof) is blind to it. Until now only the
runner's own summary text could catch that (`_masked_failure_note`), and only for runners
whose wording it knew.

This module makes the shell report it exactly:

- **A program ended by its own pipeline**. Windows PowerShell 5.1 ends a native program
  whose output goes into `Select-Object -First N` as soon as N objects arrived, and the program's
  `$LASTEXITCODE` becomes -1 whatever it would have exited with (measured: a program that would have
  exited 3 reads -1; one that finished before the cut keeps its own code; PowerShell 7 waits for the
  program and keeps the real one). The same parser finds, from the syntax tree and not from the
  text, which statements pipe into `Select-Object -First/-Index`, and the line reports `cut=1`
  when such a statement's program read -1. `Select-Object` is only a candidate in the text
  (`|` and `select`), so only those lines are wrapped.
- **PowerShell** parses the line with its own parser (`Parser::ParseInput`) and runs it
  with a probe between top-level statements that records the first native program whose
  exit code went non-zero, and where it sits in the command (`err=<code>@<start>-<end>`).
  A statement that reads `$?` or `$LASTEXITCODE` is the command handling that status
  itself, so a failure before it is not recorded. A parse error prints the parser's own
  words and exits 1 without a trailer, exactly as the host would have.
- **bash** installs an `ERR` trap, which bash already fires only for failures nobody
  handled (`|| true`, `if`, `!` and the non-final parts of `&&`/`||` lists never fire it).
  A line that reads `$?`, `PIPESTATUS` or sets its own trap is left alone: it is handling
  statuses itself.

Only a line that can hold more than one statement is wrapped (a `;` or a line break);
every single-statement command runs exactly as before.
"""

from __future__ import annotations

import re
from typing import Optional

#: PowerShell treats these as single quotes too; inside a single-quoted string each one is
#: escaped by doubling it.
_PS_QUOTES = ("'", "‘", "’", "‚", "‛")

#: Past this the wrapper (about 2k characters) could push the line over what CreateProcess
#: accepts (32,767); such a command runs as before rather than failing to start.
_MAX_WRAPPED_CHARS = 24_000

#: The PowerShell wrapper. `%SRC%` is the command as a single-quoted literal. The command
#: is run by `Invoke-Expression` in the current scope (variables and `cd` persist exactly
#: as they would at top level), and the last statement's `$?` / `$LASTEXITCODE` are read
#: INSIDE that text so the trailer sees the command's status, not Invoke-Expression's.
_PS_WRAPPER = r"""$__cwSrc = %SRC%
$__cwRun = $__cwSrc; $__cwErr = 0; $__cwAt = ''; $__cwSeen = $null; $__cwCutIn = $false; $__cwCutAny = $false
$__cwCuts = { param($__n)
  $__f = $false
  if ($__n) { foreach ($__p in $__n.FindAll({ param($a) $a -is [System.Management.Automation.Language.PipelineAst] }, $true)) {
    $__els = @($__p.PipelineElements)
    for ($__i = 1; $__i -lt $__els.Count; $__i++) {
      $__e = $__els[$__i]
      if ($__e -isnot [System.Management.Automation.Language.CommandAst]) { continue }
      if ("$($__e.GetCommandName())" -notmatch '^(?:[\w.]+\\)?select(?:-object)?$') { continue }
      foreach ($__x in $__e.CommandElements) {
        if ($__x -isnot [System.Management.Automation.Language.CommandParameterAst]) { continue }
        $__pn = "$($__x.ParameterName)"
        if ($__pn.Length -ge 1 -and ('first'.StartsWith($__pn, [System.StringComparison]::OrdinalIgnoreCase) -or ($__pn.Length -ge 3 -and 'index'.StartsWith($__pn, [System.StringComparison]::OrdinalIgnoreCase)))) { $__f = $true }
      }
    }
  } }
  $__f
}
try {
  $__cwPErr = $null
  $__cwAst = [System.Management.Automation.Language.Parser]::ParseInput($__cwSrc, [ref]$null, [ref]$__cwPErr)
  if ($__cwPErr) {
    foreach ($__cwE in $__cwPErr) { "ParserError: $($__cwE.Message) (line $($__cwE.Extent.StartLineNumber), column $($__cwE.Extent.StartColumnNumber))" }
    exit 1
  }
  $__cwS = @($__cwAst.EndBlock.Statements)
  if ($__cwS.Count -gt 0) { $__cwCutIn = [bool](& $__cwCuts $__cwS[$__cwS.Count - 1]) }
  if ($__cwS.Count -gt 1 -and -not $__cwAst.ParamBlock -and -not $__cwAst.BeginBlock -and -not $__cwAst.ProcessBlock) {
    $__cwB = New-Object System.Text.StringBuilder
    $__cwPrev = 0; $__cwFrom = $__cwS[0].Extent.StartOffset; $__cwHandled = $false
    for ($__cwI = 1; $__cwI -lt $__cwS.Count; $__cwI++) {
      $__cwSt = $__cwS[$__cwI]
      $__cwReads = [bool]$__cwSt.Find({ param($a) $a -is [System.Management.Automation.Language.VariableExpressionAst] -and ('?', 'LASTEXITCODE' -contains $a.VariablePath.UserPath) }, $true)
      if ($__cwReads) { $__cwHandled = $true; continue }
      $__cwRec = if ($__cwHandled) { '$false' } else { '$true' }
      $__cwEnd = $__cwS[$__cwI - 1].Extent.EndOffset
      [void]$__cwB.Append($__cwSrc.Substring($__cwPrev, $__cwSt.Extent.StartOffset - $__cwPrev))
      $__cwCutLit = if (& $__cwCuts $__cwS[$__cwI - 1]) { '$true' } else { '$false' }
      [void]$__cwB.Append("`n`$__cwL = `$global:LASTEXITCODE; if (`$__cwL -eq -1 -and $__cwCutLit) { `$__cwCutAny = `$true; `$__cwL = 0 }; if ($__cwRec -and `$__cwL -and `$__cwL -ne `$__cwSeen -and -not `$__cwErr) { `$__cwErr = `$__cwL; `$__cwAt = '$__cwFrom-$__cwEnd' }; `$__cwSeen = `$__cwL`n")
      $__cwPrev = $__cwSt.Extent.StartOffset; $__cwFrom = $__cwPrev; $__cwHandled = $false
    }
    [void]$__cwB.Append($__cwSrc.Substring($__cwPrev))
    $__cwRun = $__cwB.ToString()
  }
} catch { $__cwRun = $__cwSrc }
Invoke-Expression ($__cwRun + "`n`$__cwOkIn = `$?; `$__cwNatIn = `$global:LASTEXITCODE")"""

#: bash: the first unhandled failure's status. `$?` is read first thing, before the test
#: that guards the assignment replaces it; bash restores `$?` and `PIPESTATUS` after a
#: trap, so the trailer still reads the last statement's own.
_BASH_TRAP = "__cw_err=0; trap '__cw_e=$?; [ \"$__cw_err\" != 0 ] || __cw_err=$__cw_e' ERR\n"

#: A bash line that handles statuses itself: its own reading of them decides.
_BASH_SELF_HANDLED = re.compile(r"\$\?|PIPESTATUS|\btrap\b")


def _ps_literal(text: str) -> str:
    for quote in _PS_QUOTES:
        text = text.replace(quote, quote + quote)
    return "'" + text + "'"


def _may_hold_statements(command: str) -> bool:
    return ";" in command or "\n" in command.strip()


def _may_end_a_program_early(command: str) -> bool:
    """Whether a PowerShell line could pipe into `Select-Object`: only a candidate, decided by the
    syntax tree once the line is parsed (a `select` in a string or a comment costs one parse)."""
    return "|" in command and "select" in command.lower()


def sequence_script(command: str, *, powershell: bool) -> str:
    """The script text that runs `command` and records an earlier failure in it. `powershell` is
    the dialect the command is written in, not the operating system."""
    if len(command) > _MAX_WRAPPED_CHARS:
        return command
    if powershell:
        if not (_may_hold_statements(command) or _may_end_a_program_early(command)):
            return command
    elif not _may_hold_statements(command):
        return command
    if powershell:
        return _PS_WRAPPER.replace("%SRC%", _ps_literal(command), 1)
    if _BASH_SELF_HANDLED.search(command):
        return command
    return _BASH_TRAP + command


def sequence_failure(token: str, command: str) -> Optional[tuple[int, str]]:
    """`(code, note)` for an `err=` detail, or None when nothing earlier failed.

    `token` is `<code>` (bash) or `<code>@<start>-<end>` (PowerShell: the failing
    statement's character offsets in the command, which the note quotes)."""
    code_text, _, where = token.partition("@")
    try:
        code = int(code_text)
    except ValueError:
        return None
    if not code:
        return None
    start_text, _, end_text = where.partition("-")
    try:
        start, end = int(start_text), int(end_text)
    except ValueError:
        return code, (
            f"an earlier command in this line exited {code}; the line's last command "
            "succeeded"
        )
    statement = " ".join(command[start:end].split())
    if len(statement) > 80:
        statement = statement[:77] + "..."
    return code, (
        f"`{statement}` exited {code} earlier in this line; its last statement succeeded"
    )
