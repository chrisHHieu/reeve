"""What changed between two versions of a design, part by part (phase 3d).

Each check keeps an inventory of the page at its narrowest width (`measure.py`): every part a person
sees, keyed by its place in the document (the same path the preview's pointer names a part by), with
its text, box, colours and type. Two inventories of the same page say what a write changed, as a
person would put it: a part added or gone, its words, its colour, its type, its size, where it sits.

The harness reads this, never the model's account of its edit. When the person pointed at parts of
the page in the message the write answers, a change elsewhere is said to the model as a change it
was not asked for: a check line, not a fault, since one change can rightly move another part.
"""

from __future__ import annotations

from typing import Any, Iterable

#: Moves and resizes smaller than this are a reflow, not a change worth naming.
_NUDGE_PX = 6
#: How many changes are named before the rest are counted.
_NAMED = 8


def _sentence(key: str, before: dict[str, Any] | None, after: dict[str, Any] | None) -> list[str]:
    """What changed about one part, as short clauses."""
    if before is None and after is not None:
        words = f" “{after['text']}”" if after.get("text") else ""
        return [f"{after.get('name') or key}{words} added"]
    if after is None and before is not None:
        words = f" “{before['text']}”" if before.get("text") else ""
        return [f"{before.get('name') or key}{words} removed"]
    assert before is not None and after is not None
    name = after.get("name") or key
    out: list[str] = []
    if before.get("text") != after.get("text"):
        out.append(f"{name} text “{before.get('text')}” → “{after.get('text')}”")
    if before.get("color") != after.get("color") and before.get("color") and after.get("color"):
        out.append(f"{name} colour {before['color']} → {after['color']}")
    if before.get("bg") != after.get("bg"):
        out.append(f"{name} background {before.get('bg') or 'none'} → {after.get('bg') or 'none'}")
    if before.get("size") != after.get("size") or before.get("weight") != after.get("weight"):
        out.append(f"{name} type {before.get('size')}px/{before.get('weight')} → {after.get('size')}px/{after.get('weight')}")
    if abs(int(before.get("w", 0)) - int(after.get("w", 0))) > _NUDGE_PX or abs(int(before.get("h", 0)) - int(after.get("h", 0))) > _NUDGE_PX:
        out.append(f"{name} size {before.get('w')}×{before.get('h')} → {after.get('w')}×{after.get('h')}")
    elif abs(int(before.get("x", 0)) - int(after.get("x", 0))) > _NUDGE_PX or abs(int(before.get("y", 0)) - int(after.get("y", 0))) > _NUDGE_PX:
        out.append(f"{name} moved")
    return out


def changes(before: Iterable[dict[str, Any]], after: Iterable[dict[str, Any]]) -> list[dict[str, str]]:
    """Every change from `before` to `after`, as `{key, said}` in the order of the page."""
    old = {str(item.get("key")): item for item in before if item.get("key")}
    new = {str(item.get("key")): item for item in after if item.get("key")}
    order = list(new) + [key for key in old if key not in new]
    out: list[dict[str, str]] = []
    for key in order:
        for said in _sentence(key, old.get(key), new.get(key)):
            out.append({"key": key, "said": said})
    return out


def related(key: str, asked: str) -> bool:
    """Whether a part is the one pointed at, inside it, or holds it."""
    return key == asked or key.startswith(asked + " > ") or asked.startswith(key + " > ")


def unasked(found: list[dict[str, str]], asked: list[str]) -> list[dict[str, str]]:
    """The changes to parts the person did not point at; none when nothing was pointed at."""
    if not asked:
        return []
    return [change for change in found if not any(related(change["key"], selector) for selector in asked)]


def say(found: list[dict[str, str]]) -> list[str]:
    """The changes as lines, the first few named and the rest counted."""
    lines = [change["said"] for change in found[:_NAMED]]
    if len(found) > _NAMED:
        lines.append(f"and {len(found) - _NAMED} more")
    return lines
