"""Reading a check: whether a command is one, whether its output says it failed, and what failed.

The verification ledger, the shell's status line, the repair record and the harness review all
read checks; this is the one place that does, below all of them.
"""

from __future__ import annotations

from .commands import SHOWN_CHARS, cut_by_command, is_check_command, runner_args, runner_of, shown_command
from .marks import MAX_MARKS, SETUP, failure_marks, is_setup, spoken
from .summary import failure_summary, reports_failure

__all__ = [
    "MAX_MARKS",
    "SETUP",
    "SHOWN_CHARS",
    "cut_by_command",
    "failure_marks",
    "failure_summary",
    "is_check_command",
    "is_setup",
    "reports_failure",
    "runner_args",
    "runner_of",
    "shown_command",
    "spoken",
]
