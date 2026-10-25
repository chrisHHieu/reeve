"""Which version of a design page the person locked.

A lock is a file the harness writes when the person locks a page (`handoff.write_lock`):
`design/.locks/<path inside design/>.json`, holding the page's sha256 and what was measured. This
module finds and reads it, for the check (a locked page changed since is said), the list of designs
and the lock dialog; and records on it the two things the person decides once the app is built
(step 3, the review): a difference kept as the app has it, and that the app was looked at and called
done.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Optional

from .pages import DESIGN_DIR

#: Where locks are kept, inside the design folder. A dot folder: never listed as designs.
LOCKS_DIR = ".locks"
HANDOFF_SUFFIX = ".handoff.md"
#: The lock file's own version, for reading one an older Reeve wrote.
LOCK_VERSION = 1


def _when(ms: int) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ms / 1000))


def page_sha(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def design_folder(page: Path) -> Optional[Path]:
    """The nearest `design/` folder holding `page`."""
    for parent in page.parents:
        if parent.name.lower() == DESIGN_DIR:
            return parent
    return None


def lock_path(page: Path) -> Optional[Path]:
    """`design/.locks/<path inside design/>.json`: one lock per page, wherever it sits in the folder."""
    folder = design_folder(page)
    if folder is None:
        return None
    inside = page.relative_to(folder).as_posix()
    return folder / LOCKS_DIR / (inside.replace("/", "__") + ".json")


def handoff_path(page: Path) -> Path:
    """`pricing.b.html` → `pricing.b.handoff.md`, beside it."""
    return page.with_name(page.stem + HANDOFF_SUFFIX)


def read_lock(page: Path) -> Optional[dict[str, Any]]:
    """The page's lock as written, or None when it has none (or one this Reeve cannot read)."""
    where = lock_path(page)
    if where is None or not where.is_file():
        return None
    try:
        data = json.loads(where.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or int(data.get("v") or 0) > LOCK_VERSION:
        return None
    return data


def lock_state(page: Path) -> dict[str, Any]:
    """What the GUI shows of a page's lock: none, locked, or locked and changed since."""
    data = read_lock(page)
    if data is None:
        return {"locked": False}
    return {
        "locked": True,
        "locked_at": data.get("locked_at"),
        "changed": page_sha(page) != data.get("sha256"),
        "handoff": data.get("handoff"),
        "accepted": len(data.get("accepted") or []),
        "kept": len(data.get("kept") or {}),
        "reviewed": data.get("reviewed") if isinstance(data.get("reviewed"), dict) else None,
    }


def locked_line(page: Path) -> str:
    """For a check of a locked page that has changed since: a sentence, else nothing."""
    data = read_lock(page)
    if data is None or page_sha(page) == data.get("sha256"):
        return ""
    when = _when(int(data.get("locked_at") or 0))
    return (
        f"this page was locked by the person on {when} and is no longer the locked version; the app is built"
        f" from the locked version ({data.get('handoff') or 'its handoff'}). Say so in your answer: they lock it"
        " again when they want the change carried into the app."
    )


def _rewrite(page: Path, change) -> Optional[dict[str, Any]]:
    """Apply `change` to the page's lock and write it back; None when it has no lock."""
    where = lock_path(page)
    data = read_lock(page)
    if where is None or data is None:
        return None
    change(data)
    tmp = where.with_name(where.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(where)
    return data


def kept_ids(page: Path) -> set[str]:
    """The differences between app and design the person kept as the app has them."""
    data = read_lock(page) or {}
    kept = data.get("kept")
    return set(kept) if isinstance(kept, dict) else set()


def keep_difference(page: Path, ident: str, text: str, *, now: Optional[float] = None) -> Optional[dict[str, Any]]:
    """The person keeps one difference as the app has it: recorded in the lock, by its id."""
    at = int((now if now is not None else time.time()) * 1000)
    return _rewrite(page, lambda data: data.setdefault("kept", {}).__setitem__(ident, {"text": text[:400], "at": at}))


def mark_reviewed(
    page: Path, url: str, issues: int, *, tokens: Optional[dict[str, Any]] = None, now: Optional[float] = None
) -> Optional[dict[str, Any]]:
    """The person looked at the app beside the design and called it done: when, where, how many
    differences were still open (they may call it done with some), and the app's tokens the design's
    colours resolved to then, with their values (`drift.py` says when a later write moves one)."""
    at = int((now if now is not None else time.time()) * 1000)
    record = {"url": url, "at": at, "issues": issues, "tokens": dict(tokens or {})}
    return _rewrite(page, lambda data: data.__setitem__("reviewed", {**record, "sha256": data.get("sha256")}))
