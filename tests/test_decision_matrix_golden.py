"""The golden table of permission verdicts: for a fixed set of situations (mode, tool, arguments,
grants) it holds what the PermissionEngine says.

It is there to catch regressions: a change to `permissions.py` or `risk.py` shows as a difference in
exactly the rows it meant to change. A row whose note starts BASELINE-WRONG or BASELINE-ANNOYING
holds today's verdict on purpose, a known gap, and the change that closes it
flips that row as the proof.

The rows are in `tests/corpora/decision_matrix.csv`. Each builds its own engine, and relative paths
are taken from a temporary workspace of its own.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from harness.safety.permissions import Mode, PermissionEngine

_MATRIX = Path(__file__).parent / "corpora" / "decision_matrix.csv"
# The `meta` column: the metadata a connector tool would carry, only the fields classify() and
# evaluate() read. Empty is no metadata at all, so a built-in is classed by its name.
_METADATA = {
    "external": SimpleNamespace(requires_approval=True, category="connector"),
    "read": SimpleNamespace(requires_approval=False, category="connector"),
}


def _metadata(kind: str):
    kind = (kind or "").strip()
    if kind and kind not in _METADATA:
        raise ValueError(f"unknown meta kind: {kind!r}")
    return _METADATA.get(kind)


def _pipes(value: str) -> list[str]:
    return [part for part in (value or "").split("|") if part]


def _verdict(decision) -> str:
    if decision.needs_user:
        return "ask"
    return "allow" if decision.allowed else "deny"


def _rows() -> list[dict]:
    with open(_MATRIX, newline="", encoding="utf-8") as table:
        return list(csv.DictReader(table))


def _roots(value: str, workspace: Path) -> list[dict]:
    """The `roots` column: `<folder>=<access>` entries, pipe-separated, each folder relative to the
    workspace, and access `marked` (the person added it read-only), `reached` (the session took it
    by a read) or `writable`. Empty keeps the workspace as the only root."""
    entries = _pipes(value)
    if not entries:
        return []
    found = [{"path": workspace, "writable": True}]
    for entry in entries:
        folder, _, access = entry.partition("=")
        root = {"path": (workspace / folder).resolve(), "writable": access == "writable"}
        if access == "reached":
            root["reached"] = True
        found.append(root)
    return found


def _engine_for(row: dict, workspace: Path) -> PermissionEngine:
    """An engine set up as the row says: its mode, its lists, its roots, then the grants given in
    the session and a standing rule."""
    settings: dict = {"workspace_root": workspace, "mode": Mode(row["mode"]), "allowed_commands": _pipes(row.get("allowed_commands", "")), "auto_allow_tools": set(_pipes(row.get("auto_allow", "")))}
    roots = _roots(row.get("roots") or "", workspace)
    if roots:
        settings["roots"] = roots
    engine = PermissionEngine(**settings)
    for tool in _pipes(row.get("session_tools", "")):
        engine.allow_tool_for_session(tool)
    for command in _pipes(row.get("session_commands", "")):
        engine.allow_command_for_session(command)
    rule = (row.get("standing") or "").strip()
    if rule:
        tool, _, target = rule.partition(" ")
        engine.task_rules.setdefault(tool, set()).add(target.strip())
    domains = _pipes(row.get("allowed_domains", ""))
    if domains and hasattr(engine, "allowed_domains"):  # only an engine that has the list takes it
        engine.allowed_domains = list(domains)
    return engine


@pytest.mark.parametrize("row", _rows(), ids=lambda row: row["id"])
def test_decision_matrix(row, tmp_path):
    decision = _engine_for(row, tmp_path).evaluate(row["tool"], json.loads(row["args"]) if row.get("args") else {}, _metadata(row.get("meta", "")))
    verdict = _verdict(decision)
    assert verdict == row["expected"], f"{row['id']}: expected {row['expected']}, got {verdict} (reason: {decision.reason!r}) — note: {row.get('note', '')}"
