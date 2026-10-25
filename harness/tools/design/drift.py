"""A later change to the app that moves a reviewed design (step 5).

When the person reviews the app beside its locked design and calls it done ("Looks good"), the lock
records the app's tokens the design's colours resolve to, with their values then (`reviewed.tokens`).
After any write of one of the app's theme files, those values are read again; a token a reviewed
design uses that now has another value, or is gone, is said in the write's result. The app is not
run: a theme token changed is a page changed, and that is known from the files.

This is the cheap half of drift, and the one that catches the common case (a colour changed for one
screen moves every screen built on it). A layout change in a shared component is not seen here; the
person can open the review again for that.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

from .app_read import AppFacts, read_app
from .locks import LOCKS_DIR
from .pages import DESIGN_DIR

#: What a theme file can be; a write of anything else is not looked at.
_THEME_SUFFIXES = frozenset({".css", ".scss", ".ts", ".js", ".mjs", ".cjs"})


@dataclass
class Drift:
    """One token a reviewed design uses that a write moved."""

    page: str
    token: str
    before: str
    after: Optional[str]
    source: str
    reviewed_at: int
    url: str


def reviewed_tokens(app: AppFacts, colors: Iterable[str]) -> dict[str, dict[str, str]]:
    """The app's tokens the design's `colors` resolve to now, for the record of a review."""
    out: dict[str, dict[str, str]] = {}
    for color in colors:
        for token in app.colors:
            if token.name in out:
                continue
            if token.name in app.token_for(color):
                out[token.name] = {"value": token.value, "source": token.source}
    return out


def design_colors(lock: dict[str, Any]) -> list[str]:
    """Every colour the lock measured, by role, at its widest width."""
    system = (lock.get("system") or [{}])[0]
    seen: list[str] = []
    for entries in (system.get("colors") or {}).values():
        for entry in entries or []:
            color = str(entry.get("key") or "")
            if color and color not in seen:
                seen.append(color)
    return seen


def _locks(workspace: Path) -> list[tuple[str, dict[str, Any]]]:
    """Reviewed locks under `design/` and `<folder>/design/`, with the page each is of."""
    out: list[tuple[str, dict[str, Any]]] = []
    folders = [workspace / DESIGN_DIR]
    try:
        folders += [child / DESIGN_DIR for child in workspace.iterdir() if child.is_dir() and not child.name.startswith(".")]
    except OSError:
        pass
    for folder in folders:
        locks = folder / LOCKS_DIR
        if not locks.is_dir():
            continue
        for path in sorted(locks.glob("*.json"))[:200]:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            reviewed = data.get("reviewed") if isinstance(data, dict) else None
            if isinstance(reviewed, dict) and isinstance(reviewed.get("tokens"), dict) and reviewed["tokens"]:
                out.append((str(data.get("page") or path.stem), data))
    return out


def find_drift(workspace: Path, written: Iterable[Path]) -> list[Drift]:
    """The tokens of reviewed designs that the write of `written` moved. Never raises."""
    try:
        candidates = [path.resolve() for path in written if path.suffix.lower() in _THEME_SUFFIXES]
        if not candidates:
            return []
        locks = _locks(workspace)
        if not locks:
            return []
        app = read_app(workspace)
        if not app.found or app.root is None:
            return []
        themes = {(app.root / name).resolve() for name in app.theme_files}
        touched = [path for path in candidates if path in themes]
        # A theme file emptied of tokens drops out of `theme_files`; a write named like one counts.
        touched += [path for path in candidates if path not in themes and path.name.startswith("tailwind.config")]
        if not touched:
            return []
        now = {token.name: token for token in app.colors}
        out: list[Drift] = []
        for page, lock in locks:
            reviewed = lock["reviewed"]
            for name, then in reviewed["tokens"].items():
                before = str((then or {}).get("value") or "")
                token = now.get(name)
                after = token.value if token is not None else None
                if after == before:
                    continue
                out.append(
                    Drift(
                        page=page,
                        token=name,
                        before=before,
                        after=after,
                        source=token.source if token is not None else str((then or {}).get("source") or ""),
                        reviewed_at=int(reviewed.get("at") or 0),
                        url=str(reviewed.get("url") or ""),
                    )
                )
        return out
    except Exception:  # a drift note is an aside on a write that already succeeded
        return []


def drift_text(drifts: list[Drift]) -> str:
    """What the write's result says: each reviewed page this moved, and how."""
    if not drifts:
        return ""
    by_page: dict[str, list[Drift]] = {}
    for drift in drifts:
        by_page.setdefault(drift.page, []).append(drift)
    lines = ["[design drift] This write changed tokens that a design the person reviewed in the app uses:"]
    for page, items in by_page.items():
        moved = "; ".join(
            f"`{item.token}` {item.before} → {item.after}" if item.after else f"`{item.token}` {item.before} → removed"
            for item in items
        )
        first = items[0]
        when = time.strftime("%Y-%m-%d", time.localtime(first.reviewed_at / 1000)) if first.reviewed_at else "earlier"
        where = f" ({first.url})" if first.url else ""
        lines.append(f"- {page}, reviewed in the app{where} on {when}: {moved}.")
    lines.append(
        "Say so in your answer: the person decides whether those pages change too, or the new value gets a"
        " token of its own so they keep the reviewed one."
    )
    return "\n".join(lines)
