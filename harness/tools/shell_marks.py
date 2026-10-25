"""Shell integration for the person's terminal: the shell marks where each command starts and ends.

The terminal panel used to know only whether the SHELL was alive, so it said "running" while
PowerShell sat at an idle prompt and could not tell a finished command from a failed one. A
shell can say more, the way VS Code's and Windows Terminal's terminals have it say: around its
prompt and each command it prints invisible OSC sequences (FinalTerm's OSC 133, and VS Code's
OSC 633;E for the command line), which the emulator reads and does not draw:

    ESC ] 133 ; A BEL        a prompt starts
    ESC ] 133 ; B BEL        the prompt ends (the person types here)
    ESC ] 633 ; E ; <line> BEL   the command line that was entered
    ESC ] 133 ; C BEL        the command runs (its output starts)
    ESC ] 133 ; D ; <n> BEL  the command finished with exit code n

`integration(shell_path)` gives the argv and environment that make a shell print them. The
person's own startup files still run first (PowerShell's profile, `~/.bashrc`); the hooks wrap
the prompt they define rather than replace it. cmd.exe has no hook that runs around a command,
so it marks only its prompt; any other shell is left as it is. Only the panel's terminals ask
for this: the agent's `type="pty"` shell never does, so its output stays exactly the program's.
"""

from __future__ import annotations

import base64
import tempfile
from pathlib import Path
from typing import Optional

# PowerShell 5.1 and 7: the prompt wraps whatever prompt the profile set, and PSReadLine's Enter
# says which line ran before it runs. `$?` is read first thing, before anything can reset it.
_POWERSHELL = r"""
$global:__reeveRan = $false
function global:__reeveOsc([string]$s) { [Console]::Write([string][char]27 + ']' + $s + [string][char]7) }
$global:__reevePrompt = $function:prompt
function global:prompt {
  $ok = $global:?
  $code = if ($ok) { 0 } elseif ($global:LASTEXITCODE) { $global:LASTEXITCODE } else { 1 }
  if ($global:__reeveRan) { __reeveOsc ('133;D;' + $code); $global:__reeveRan = $false }
  __reeveOsc '133;A'
  $text = if ($global:__reevePrompt) { & $global:__reevePrompt } else { 'PS ' + (Get-Location) + '> ' }
  $text + ([string][char]27 + ']133;B' + [string][char]7)
}
if (Get-Module PSReadLine) {
  Set-PSReadLineKeyHandler -Chord Enter -ScriptBlock {
    $line = $null; $cursor = $null
    [Microsoft.PowerShell.PSConsoleReadLine]::GetBufferState([ref]$line, [ref]$cursor)
    if ($line.Trim().Length -gt 0) {
      $global:__reeveRan = $true
      __reeveOsc ('633;E;' + ($line -replace '[\x00-\x1f\x7f;\\]', ' '))
      __reeveOsc '133;C'
    }
    [Microsoft.PowerShell.PSConsoleReadLine]::AcceptLine()
  }
}
"""

# bash (Git Bash on Windows too): the person's ~/.bashrc first, then a prompt that says where it
# starts and ends, and a DEBUG trap that says which line ran — once per line, not per command in
# a pipeline.
_BASH = r"""
[ -f ~/.bashrc ] && . ~/.bashrc
__reeve_ran=0
__reeve_armed=0
__reeve_precmd() {
  local code=$?
  if [ "$__reeve_ran" = 1 ]; then printf '\033]133;D;%s\007' "$code"; __reeve_ran=0; fi
  __reeve_armed=1
}
__reeve_preexec() {
  [ "$__reeve_armed" = 1 ] || return
  [ -n "$COMP_LINE" ] && return
  case "$BASH_COMMAND" in __reeve_precmd*) return ;; esac
  __reeve_armed=0
  __reeve_ran=1
  local line
  line=$(HISTTIMEFORMAT= history 1 | sed -e 's/^ *[0-9]* *//' | tr -d '\000-\037;\\')
  printf '\033]633;E;%s\007\033]133;C\007' "$line"
}
PROMPT_COMMAND="__reeve_precmd${PROMPT_COMMAND:+; $PROMPT_COMMAND}"
PS1="\[\033]133;A\007\]${PS1}\[\033]133;B\007\]"
trap '__reeve_preexec' DEBUG
"""

# cmd.exe: $E is ESC in PROMPT; there is no hook around a command, so only the prompt is marked.
_CMD_PROMPT = "$E]133;A$E\\$P$G$E]133;B$E\\"


def _encoded(script: str) -> str:
    """A script for `-EncodedCommand`: UTF-16LE in base64, which survives any argv quoting."""
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


def _bash_rcfile() -> str:
    """The rcfile, written once per process to the temp folder (the same text every time)."""
    path = Path(tempfile.gettempdir()) / "reeve-shell-marks.bash"
    try:
        if not path.is_file() or path.read_text(encoding="utf-8") != _BASH:
            path.write_text(_BASH, encoding="utf-8", newline="\n")
    except OSError:
        return ""
    return str(path)


def integration(shell_path: str) -> tuple[Optional[list[str]], dict[str, str], bool]:
    """(extra argv after the shell's own, extra environment, whether commands are marked).

    None for the argv means the shell is started as it was before.
    """
    name = Path(shell_path).name.lower()
    if name in {"powershell.exe", "powershell", "pwsh", "pwsh.exe"}:
        return ["-NoExit", "-EncodedCommand", _encoded(_POWERSHELL)], {}, True
    if name in {"bash", "bash.exe"}:
        rcfile = _bash_rcfile()
        if rcfile:
            return ["--rcfile", rcfile, "-i"], {}, True
        return None, {}, False
    if name in {"cmd", "cmd.exe"}:
        return None, {"PROMPT": _CMD_PROMPT}, False
    return None, {}, False
