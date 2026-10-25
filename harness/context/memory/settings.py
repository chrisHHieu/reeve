"""Memory's switches, and the person's own SOUL.md.

These live beside the memory table, not in it:

- `enabled`: when off, an engine is built with no memory tools, no memories block and no word
  about memory. What was remembered stays, unused. It is read when an engine is built, so a
  conversation already running ends the way it started.
- `auto_memory`: whether the project's markdown memory files are written and read.
- `user_rules`: the person's SOUL.md, who they are and what they want of every conversation. It is
  a real markdown file next to the settings, so it can be opened, edited and backed up without the
  app; it goes into the prompt as written, above the remembered notes, and wins where they
  disagree. Only Settings writes it, through the manager; the agent never writes, edits or
  deletes it.

  SOUL.md is the person's tier of the instructions, the one file at that level:
  `@path` imports in it expand as a project file's do, from anywhere, since the file is the
  person's own, and an older `<state-dir>/AGENTS.md` is folded into it once instead of being read
  as a second home.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Optional

#: SOUL.md is a settings field, not a place for documents: room for any real list of rules, and a
#: bound on what an accidental paste, or a hostile client, could add to every prompt after it.
MAX_USER_RULES_CHARS = 20_000

#: The person's file, next to the settings JSON.
SOUL_FILENAME = "SOUL.md"

#: The second person-level file there used to be (`project.load_agents_md` read it until memory became files).
LEGACY_USER_AGENTS = "AGENTS.md"


class MemorySettingsStore:
    def __init__(self, path: str | Path, *, soul_path: str | Path | None = None) -> None:
        self.path = Path(path)
        self.soul_path = self.path.parent / SOUL_FILENAME if soul_path is None else Path(soul_path)
        self._lock = threading.Lock()

    def _load(self) -> dict:
        try:
            stored = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return stored if isinstance(stored, dict) else {}

    def _save(self, settings: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(settings, indent=2), encoding="utf-8")

    def _switch(self, name: str) -> bool:
        return bool(self._load().get(name, True))  # every switch starts on

    # -- SOUL.md --------------------------------------------------------------------------

    def _merge_legacy_agents(self) -> None:
        """Fold an old `<state-dir>/AGENTS.md` into SOUL.md, once. Both meant "my instructions for
        every conversation" and were read into the prompt from two places. Every word is kept,
        added under a heading, and the old file is renamed so it is never read or merged again."""
        legacy = self.soul_path.parent / LEGACY_USER_AGENTS
        if not legacy.is_file():
            return
        try:
            added = legacy.read_text(encoding="utf-8").strip()
            if added:
                try:
                    soul = self.soul_path.read_text(encoding="utf-8").rstrip()
                except OSError:
                    soul = ""
                if added not in soul:
                    merged = f"{soul}\n\n## From AGENTS.md\n\n{added}\n" if soul else f"{added}\n"
                    self._write_soul(merged[:MAX_USER_RULES_CHARS])
            legacy.replace(legacy.with_name(f"{LEGACY_USER_AGENTS}.merged-into-SOUL"))
        except OSError:
            pass

    def _read_soul(self) -> str:
        """SOUL.md's text. With no file yet, the `user_rules` an older version kept in the JSON
        becomes the file, once, so an upgrade keeps what the person wrote; else empty."""
        self._merge_legacy_agents()
        try:
            return self.soul_path.read_text(encoding="utf-8")[:MAX_USER_RULES_CHARS]
        except OSError:
            pass
        older = self._load().get("user_rules")
        if not isinstance(older, str) or not older.strip():
            return ""
        self._write_soul(older[:MAX_USER_RULES_CHARS])
        return older[:MAX_USER_RULES_CHARS]

    def _write_soul(self, text: str) -> None:
        try:
            self.soul_path.parent.mkdir(parents=True, exist_ok=True)
            self.soul_path.write_text(text, encoding="utf-8")
        except OSError:
            pass

    # -- reading ----------------------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self._switch("enabled")

    @property
    def user_rules(self) -> str:
        """SOUL.md as the person wrote it: what Settings shows and saves."""
        return self._read_soul()

    def prompt_rules(self) -> str:
        """SOUL.md as the model reads it, `@path` imports expanded from anywhere (the file is the
        person's own)."""
        from harness.session.project import expand_imports

        text = self._read_soul()
        return expand_imports(text, source=self.soul_path, workspace=None, external_ok=True) if text else ""

    @property
    def auto_memory(self) -> bool:
        """The markdown notes this machine's agent keeps for itself. When off, their index is not
        put in the prompt and the `auto_memory` tool is not offered; the files stay as they are."""
        return self._switch("auto_memory")

    def snapshot(self) -> dict:
        return {"enabled": self.enabled, "user_rules": self.user_rules, "auto_memory": self.auto_memory}

    # -- writing ----------------------------------------------------------------------------

    def set(self, *, enabled: Optional[bool] = None, user_rules: Optional[str] = None, auto_memory: Optional[bool] = None) -> dict:
        """Change what is given; leave the rest. Returns everything as it now is."""
        with self._lock:
            settings = self._load()
            for name, value in (("enabled", enabled), ("auto_memory", auto_memory)):
                if value is not None:
                    settings[name] = bool(value)
            if user_rules is not None:
                self._write_soul(str(user_rules)[:MAX_USER_RULES_CHARS])
                settings.pop("user_rules", None)  # SOUL.md holds it now
            self._save(settings)
        return self.snapshot()


def format_user_rules(rules: str) -> str:
    """The prompt block that carries SOUL.md; nothing when it is empty."""
    text = (rules or "").strip()
    if not text:
        return ""
    return (
        "User rules — your user's SOUL.md (identity + instructions, written by them in "
        "Settings; always follow these — on any conflict they outrank learned memories):\n"
        f"{text}"
    )
