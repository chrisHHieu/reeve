"""Whether a check's output says it failed: a runner's own summary line.

A check fails when this appears, whatever the exit status: `pyright *> log; Get-Content log` exits 0
with "180 errors" in its output, and so does any pipeline a shell reports by its last stage. Each
pattern is a SUMMARY shape (counts are non-zero, and pytest's carries its timing), so an
application's own log line such as "1 failed login" is not one.
"""

from __future__ import annotations

import re
from typing import Optional

_FAILED_SUMMARY = tuple(
    re.compile(pattern, re.MULTILINE)
    for pattern in (
        # pytest: "1 failed, 3 passed in 0.12s" / "=== 2 errors in 1.0s ==="
        r"\b[1-9]\d* (?:failed|errors?)\b[^\n]*\bin \d+(?:\.\d+)?s\b",
        # jest ("Tests:       1 failed, …") and vitest ("Test Files  1 failed | …")
        r"^\s*Tests?(?: Files)?:?\s+[1-9]\d* failed\b",
        # tsc
        r"\berror TS\d+:",
        # ruff / mypy: "Found 3 errors." / "Found 2 errors in 1 file"
        r"\bFound [1-9]\d* errors?\b",
        # pyright: "180 errors, 0 warnings, 0 informations"
        r"\b[1-9]\d* errors?, \d+ warnings?\b",
        # eslint: "✖ 4 problems (2 errors, 2 warnings)"
        r"\b[1-9]\d* problems? \([1-9]\d* errors?\b",
        # Node's own runner, its default reporter ("ℹ fail 1") and its TAP one ("# fail 1")
        r"^\s*(?:ℹ|#) fail [1-9]\d*\s*$",
    )
)


def failure_summary(content: str) -> Optional[str]:
    """The line where a runner reports its failure (`Found 3 errors.`), or None."""
    text = content or ""
    for pattern in _FAILED_SUMMARY:
        match = pattern.search(text)
        if match:
            start = text.rfind("\n", 0, match.start()) + 1
            end = text.find("\n", match.end())
            return text[start : end if end >= 0 else len(text)].strip()[:120]
    return None


def reports_failure(content: str) -> bool:
    """Whether a check's output carries a test/lint/type runner's failing summary."""
    return failure_summary(content) is not None


__all__ = ["failure_summary", "reports_failure"]
