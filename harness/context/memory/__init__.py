"""Reeve's memory: markdown files: an index plus topic files.

- `auto_memory.AutoMemory` — one memory directory: a project's notes (`PROJECT`) or who
  the user is (`USER`), a `MEMORY.md` index plus one file per fact;
- `prompt.memory_section` — what the model is told about the directories it may write;
- `gate` / `admission` / `anchors` — what a model may write there, and the evidence a
  memory is saved with and checked against;
- `settings.MemorySettingsStore` — the switches and the user's SOUL.md;
- `threats` / `guard` — the injection screen on anything replayed into a prompt.
"""

from . import threats
from .auto_memory import (
    INDEX_MAX_BYTES,
    INDEX_MAX_LINES,
    INDEX_NAME,
    PROJECT,
    TYPES as AUTO_MEMORY_TYPES,
    USER,
    AutoMemory,
    MemoryScope,
    auto_memory_tools,
)
from .settings import MemorySettingsStore, format_user_rules

__all__ = sorted(
    {
        "AUTO_MEMORY_TYPES",
        "AutoMemory",
        "INDEX_MAX_BYTES",
        "INDEX_MAX_LINES",
        "INDEX_NAME",
        "MemoryScope",
        "MemorySettingsStore",
        "PROJECT",
        "USER",
        "auto_memory_tools",
        "format_user_rules",
        "threats",
    }
)
