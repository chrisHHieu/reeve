"""One conversation's messages on disk: an append-only JSON-lines file, one message a line."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional


class TranscriptFile:
    def __init__(self, path: Path) -> None:
        self.path = path

    def exists(self) -> bool:
        return self.path.exists()

    def _lines(self) -> list[str]:
        return [line for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def read(self) -> Optional[list[dict]]:
        """The messages, or None when there is no file."""
        if not self.exists():
            return None
        return [json.loads(line) for line in self._lines()]

    def count(self) -> int:
        return len(self._lines()) if self.exists() else 0

    def last(self) -> Optional[dict]:
        """The last message on disk, which `save` compares with the live one to notice an in-place
        update. It streams the file and holds one line."""
        if not self.exists():
            return None
        try:
            latest = None
            with open(self.path, encoding="utf-8") as handle:
                for line in handle:
                    if line.strip():
                        latest = line
            return json.loads(latest) if latest else None
        except (OSError, json.JSONDecodeError):
            return None

    def append(self, messages: list[dict]) -> None:
        with open(self.path, "a", encoding="utf-8") as handle:
            for message in messages:
                handle.write(json.dumps(message) + "\n")

    def rewrite(self, messages: list[dict]) -> None:
        with open(self.path, "w", encoding="utf-8") as handle:
            for message in messages:
                handle.write(json.dumps(message) + "\n")

    def delete(self) -> None:
        if self.exists():
            self.path.unlink()
