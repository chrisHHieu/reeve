"""A conservative classifier for shell commands that only read.

A command it accepts, whose every read is inside the session's roots, runs with no ask in every
preset, plan included, as `read_file` and `grep` do. Its rules:

- Local filesystem reads only. Network clients (curl, wget, ssh, nc) are out even for a GET,
  because an auto-allowed network command is a way to send data out under prompt injection.
  Interpreters (python, ruby, `sh -c`) and anything that can write, run or change state are out,
  and so is anything that prints the environment (`env`, a `$VAR`), where tokens live.
- A pipeline is fine, and every stage must pass. Any other shell operator (`;`, `&&`, `||`, `&`,
  redirections, substitutions, a parenthesis) rejects the command.
- When in doubt it refuses: an unknown command, input that does not parse, a binary named by path,
  a doubtful flag. A false refusal costs one approval and a false accept costs an unreviewed side
  effect, so the asymmetry settles each edge case.

It is not a sandbox: a command it refuses goes to the session's permission mode as before.
"""

from __future__ import annotations

from .classify import is_readonly_command
from .targets import read_targets

__all__ = ["is_readonly_command", "read_targets"]
