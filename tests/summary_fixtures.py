"""A summarizer reply that satisfies the eight-section contract, for scripted providers.

Real models follow `SUMMARY_SYSTEM_PROMPT`; a fake answering `## Summary` is invalid input
to `build_state`, which re-asks once and then raises. Tests that only need "a summary
happened" use this and keep their content assertions in the note.
"""

from __future__ import annotations

from harness.context.compaction import SUMMARY_SECTIONS


def valid_summary(note: str = "ok") -> str:
    """All eight headings, each carrying `note` (so content assertions still match).

    No leading/trailing whitespace: `summarize_span` strips the reply, so a fixture that
    ended in a newline would never equal what the engine stores.
    """
    return "\n\n".join(f"## {name}\n{note}" for name in SUMMARY_SECTIONS)
