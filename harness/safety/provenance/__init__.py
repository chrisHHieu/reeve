"""What the agent itself created this session, and the one fact that follows from it.

The engine never shows file contents, so `python scripts/setup.py` cannot be judged from its text:
its effect is inside a file nobody at the approval card sees. The engine does know something the
person does not, which is whether it wrote or downloaded that file moments ago. This package keeps
that record and renders it as one line of fixed vocabulary.

Not here, on purpose: reading file contents, analysing what a script does, or tracing values out of
untrusted text (general taint tracking is a separate and larger design). A miss leaves behaviour as
it was, so partial coverage only ever moves toward caution; a detector's false negatives would
breed false confidence.
"""

from __future__ import annotations

from .commands import command_paths, fetched_paths
from .keys import resolve, resolve_remote
from .session_files import (
    DOWNLOADED,
    WRITTEN,
    Match,
    Origin,
    SessionFiles,
    created_paths,
    referenced_paths,
)

__all__ = [
    "DOWNLOADED", "WRITTEN", "Match", "Origin", "SessionFiles", "command_paths",
    "created_paths", "fetched_paths", "referenced_paths", "resolve", "resolve_remote",
]
