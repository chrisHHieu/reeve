"""Reading and writing one `SKILL.md`, naming skills, and counting their use."""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from pathlib import Path
from typing import Any, Optional

import yaml

from harness.safety.secrets import state_dir

from .base import _read_skill

logger = logging.getLogger(__name__)

_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_MAX_NAME = 64
_USAGE_LOCK = threading.Lock()


def validate_name(name: str) -> str:
    """A skill name becomes a folder name, so anything that could escape the scope folder is
    refused."""
    name = (name or "").strip()
    if not name:
        raise ValueError("Skill name is required.")
    if len(name) > _MAX_NAME:
        raise ValueError(f"Skill name too long (limit {_MAX_NAME} characters).")
    if ".." in name or "/" in name or "\\" in name or not _NAME.match(name):
        raise ValueError("Skill name may only contain letters, digits, dots, dashes, and underscores.")
    return name


def compat_read_only_error(name: str, what: str) -> str:
    return (
        f"'{name}' comes from a shared skills folder and can't be {what} here — edit it "
        "in that folder, or copy it to your skills to customize."
    )


def frontmatter_source(skill_md: Path) -> str:
    """The optional `source:` frontmatter key (`uploaded` and the like). Absent means it was
    created here."""
    try:
        _skill, data = _read_skill(skill_md)
    except (OSError, UnicodeDecodeError, ValueError):
        return ""
    value = data.get("source")
    return str(value).strip() if value is not None else ""


def render_skill_md(data: dict[str, Any], instructions: str) -> str:
    """A whole frontmatter mapping plus the body, as the file's text.

    `safe_dump(allow_unicode=True, sort_keys=False)` is the writer's whole safety story: a
    description with `: ` in it, or a leading `[` or `#`, or trailing spaces, is quoted by the
    emitter, so the invalid YAML the first writer could produce cannot happen."""
    header = yaml.safe_dump(data, allow_unicode=True, sort_keys=False).rstrip("\n")
    return f"---\n{header}\n---\n\n{instructions.strip()}\n"


def write_skill_md(
    folder: Path,
    *,
    name: str,
    description: str,
    instructions: str,
    source: str = "",
    extra: Optional[dict[str, Any]] = None,
) -> None:
    data: dict[str, Any] = dict(extra or {})
    data["name"] = name
    data["description"] = description
    if source:
        data["source"] = source
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(render_skill_md(data, instructions), encoding="utf-8")


# -- how often a skill was loaded --------------------------------------------------------


def _usage_path() -> Path:
    return state_dir() / "skill-usage.json"


def skill_usage() -> dict[str, dict[str, Any]]:
    """`{name: {"uses": n, "last_used_at": epoch}}`: how often and when the worker loaded each
    skill. A missing or unreadable file means none recorded yet."""
    try:
        data = json.loads(_usage_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def record_skill_use(name: str) -> None:
    """Count one successful `load_skill`. It is best effort: a ledger that cannot be written is
    no reason to fail the load it describes. The `/` popup and Settings had nothing to rank or
    describe a skill by before this."""
    try:
        with _USAGE_LOCK:
            data = skill_usage()
            entry = data.get(name) if isinstance(data.get(name), dict) else {}
            data[name] = {"uses": int(entry.get("uses") or 0) + 1, "last_used_at": time.time()}
            path = _usage_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    except Exception:
        logger.debug("skill usage not recorded for %s", name, exc_info=True)
