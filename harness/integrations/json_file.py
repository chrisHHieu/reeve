"""Small stores keep their state in one JSON file; this is the reading and writing they share."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional


def read_json(path: Optional[Path]) -> dict[str, Any]:
    """The file's object, or an empty one when there is no path or no file yet."""
    if path is None or not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Optional[Path], payload: dict[str, Any]) -> None:
    """Save `payload`; a store built without a path keeps its state in memory only."""
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
