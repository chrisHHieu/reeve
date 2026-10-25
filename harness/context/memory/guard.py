"""Load-time guard on recalled memory.

A memory file is plaintext that any process or person can edit, and the recalled block
rides INSIDE the user message — so a line that reads like an instruction is exactly the
shape an injection takes. Offending LINES are replaced with a visible placeholder in the
injected block only; the file on disk is untouched, because the person still needs to see
what is actually stored (the live entry is kept and blocked in the snapshot for
the same reason).
"""

from __future__ import annotations

#: Deliberately literal markers: this must never block a real memory that merely talks
#: about instructions. Anything fuzzier belongs in the decision layer, not here.
MARKERS = (
    "ignore previous",
    "ignore all previous",
    "ignore the above",
    "disregard previous",
    "disregard the above",
    "you are now",
    "new instructions:",
    "system prompt:",
    "do not tell the user",
    "without telling the user",
    "do not mention this to the user",
)

PLACEHOLDER = (
    "[BLOCKED: a memory line looked like an instruction and was removed from context]"
)


def fence_injections(block: str) -> str:
    """The block with instruction-shaped lines replaced by the placeholder."""
    out: list[str] = []
    for line in (block or "").splitlines():
        low = line.lower()
        out.append(PLACEHOLDER if any(marker in low for marker in MARKERS) else line)
    return "\n".join(out)
