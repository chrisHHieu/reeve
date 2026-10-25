"""Check the layered security corpora written by `build_layered_corpora.py`.

    python scripts/validate_layered_corpora.py      (exit 1 and the first defect when one is found)

Each file is read as JSONL and each row checked: its fields, its id (unique across all three), its
labels, that there are holdouts and working rows both, that the coverage tags are there, and that
no row uses a tool name the old corpus got wrong.

Tool names are compared with the product's (the connector catalogue and the core tools), and the
ones it no longer has are counted in the summary as `retired_tools`, not refused. About forty rows
name connectors that have since left the product (Zendesk, HubSpot, Discord, the GitHub tools
before GitHub's own MCP server). Moving each row onto a live tool that carries the same concern is
a security reviewer's call, as it was for the browser rows; until that is done the count says how
far the corpus has drifted. (The refusal this replaces never ran: it sat after a `raise`.)
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CORPUS_DIR = ROOT / "tests" / "corpora"

# file -> (layer, fewest rows). The floors came down when the browser rows left (2026-09-15), and
# again when the rows for connectors Reeve no longer has left (2026-10-25); a floor is there to
# catch a corpus shrinking by accident, so it follows what is left.
FILES = {
    "permission_gate.jsonl": ("permission_gate", 104),
    "reviewer_actions.jsonl": ("reviewer_action", 98),
    "action_sequences.jsonl": ("action_sequence", 47),
}
GATE_LABELS = {"allow_without_reviewer", "reviewer_eligible", "human_only", "hard_deny"}
REVIEW_LABELS = {"allow", "ask", "deny"}
MODES = {"discuss", "plan", "interactive", "custom", "auto-approve", "bypass-approvals"}
STALE_ALIASES = {"send_email", "calendar_list_events", "gmail_delete", "gmail_forward"}
CORE_TOOLS = {
    "apply_patch",
    "apply_unified_diff",
    "ask_user",
    "create_scheduled_task",
    "delete_scheduled_task",
    "grep",
    "list_files",
    "list_scheduled_tasks",
    "load_skill",
    "propose_plan",
    "read_file",
    "read_file_lines",
    "replace_in_file",
    "request_directory",
    "run_shell",
    "save_skill",
    "send_file",
    "send_message",
    "shell_task_kill",
    "shell_task_output",
    "todo_write",
    "update_scheduled_task",
    "web_fetch",
    "web_search",
    "write_file",
}

# What each layer must cover, as tags. No `browser` since the browser tools were retired; what
# they carried (egress, writes out of the roots, credentials leaving) is covered through live tools.
REQUIRED_TAGS = {
    "permission_gate": {
        "connector",
        "credentials",
        "egress",
        "environment",
        "exec",
        "mcp",
        "outside-root",
        "persistence",
        "persistent-authority",
        "privilege",
        "self-protection",
    },
    "reviewer_action": {"connector", "egress", "exec", "explicit-danger", "persistent-authority", "production-tool", "transformed-injection", "wrong-destination"},
    "action_sequence": {"automation", "benign-control", "cross-connector", "exfiltration", "injection", "persistence"},
}


class ValidationError(Exception):
    pass


def production_tools() -> set[str]:
    sys.path.insert(0, str(ROOT))
    from harness.integrations.connectors.catalog import TOOL_DEFS

    return CORE_TOOLS | {definition.name for definition in TOOL_DEFS}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValidationError(f"{path.name}:{number}: invalid JSON: {exc}") from exc
        if not isinstance(row, dict):
            raise ValidationError(f"{path.name}:{number}: row must be an object")
        rows.append(row)
    return rows


def require(row: dict[str, Any], fields: set[str], where: str) -> None:
    missing = sorted(field for field in fields if field not in row)
    if missing:
        raise ValidationError(f"{where}: missing fields: {', '.join(missing)}")


def action_tools(row: dict[str, Any]) -> list[str]:
    """The tool names a row's call, or calls, use."""
    if row.get("layer") == "action_sequence":
        actions = row.get("actions")
        if not isinstance(actions, list) or not actions:
            raise ValidationError(f"{row.get('id')}: actions must be a non-empty list")
        return [str(action.get("tool", "")) for action in actions if isinstance(action, dict)]
    action = row.get("action")
    if not isinstance(action, dict):
        raise ValidationError(f"{row.get('id')}: action must be an object")
    return [str(action.get("tool", ""))]


def _label(row: dict[str, Any], field: str, allowed: set[str], where: str) -> str:
    if row[field] not in allowed:
        raise ValidationError(f"{where}: invalid {field} {row[field]!r}")
    return row[field]


def _labels_of(row: dict[str, Any], layer: str, where: str) -> list[str]:
    """Check the layer's own fields and return the labels the row carries."""
    if layer == "permission_gate":
        require(row, {"mode", "action", "expected_current", "expected_secure", "why"}, where)
        if row["mode"] not in MODES:
            raise ValidationError(f"{where}: invalid mode {row['mode']!r}")
        now, should = (_label(row, field, GATE_LABELS, where) for field in ("expected_current", "expected_secure"))
        if now != should and (row.get("known_gap") is not True or not row.get("failure_point")):
            raise ValidationError(f"{where}: differing expectations require known_gap and failure_point")
        return [now, should]
    if layer == "reviewer_action":
        require(row, {"action", "provenance", "correct", "why", "recommended_gate"}, where)
        _label(row, "recommended_gate", GATE_LABELS, where)
        return [_label(row, "correct", REVIEW_LABELS, where)]
    require(row, {"observations", "actions", "correct", "why"}, where)
    if not isinstance(row["observations"], list):
        raise ValidationError(f"{where}: observations must be a list")
    return [_label(row, "correct", REVIEW_LABELS, where)]


def validate_rows(name: str, rows: list[dict[str, Any]], tools: set[str]) -> set[str]:
    """Check one corpus; returns the tool names it uses that the product no longer has."""
    layer, floor = FILES[name]
    if len(rows) < floor:
        raise ValidationError(f"{name}: expected at least {floor} rows, found {len(rows)}")
    if not 0 < sum(bool(row.get("holdout")) for row in rows) < len(rows):
        raise ValidationError(f"{name}: requires both holdout and non-holdout rows")
    labels: Counter[str] = Counter()
    tags: Counter[str] = Counter()
    retired: set[str] = set()
    for number, row in enumerate(rows, 1):
        where = f"{name}:{number}"
        require(row, {"id", "layer", "user_request", "setup", "tags", "holdout"}, where)
        if row["layer"] != layer:
            raise ValidationError(f"{where}: expected layer {layer!r}")
        if not isinstance(row["id"], str) or not row["id"]:
            raise ValidationError(f"{where}: id must be a non-empty string")
        if not isinstance(row["tags"], list) or not row["tags"]:
            raise ValidationError(f"{where}: tags must be a non-empty list")
        tags.update(map(str, row["tags"]))
        labels.update(_labels_of(row, layer, where))
        for tool in action_tools(row):
            if not tool:
                raise ValidationError(f"{where}: action has no tool name")
            if tool in STALE_ALIASES:
                raise ValidationError(f"{where}: stale/non-production alias {tool!r}")
            # An `mcp__*` name is whatever a person configured, so no static catalogue can list it;
            # those rows test the classification by the name's shape.
            if tool not in tools and not tool.startswith("mcp__") and "unknown-tool" not in row["tags"]:
                retired.add(tool)
    every = GATE_LABELS if layer == "permission_gate" else REVIEW_LABELS
    if every - set(labels):
        raise ValidationError(f"{name}: missing labels {sorted(every - set(labels))}")
    if REQUIRED_TAGS[layer] - set(tags):
        raise ValidationError(f"{name}: missing required coverage tags {sorted(REQUIRED_TAGS[layer] - set(tags))}")
    return retired


def validate_all() -> dict[str, Any]:
    tools = production_tools()
    seen: set[str] = set()
    summary: dict[str, Any] = {}
    for name, (layer, _floor) in FILES.items():
        rows = load_jsonl(CORPUS_DIR / name)
        retired = validate_rows(name, rows, tools)
        for row in rows:
            if row["id"] in seen:
                raise ValidationError(f"duplicate id across corpora: {row['id']}")
            seen.add(row["id"])
        label_field = "expected_secure" if layer == "permission_gate" else "correct"
        summary[name] = {
            "rows": len(rows),
            "holdout": sum(bool(row.get("holdout")) for row in rows),
            "labels": dict(Counter(str(row[label_field]) for row in rows)),
            "tools": len({tool for row in rows for tool in action_tools(row)}),
            "tags": len({str(tag) for row in rows for tag in row["tags"]}),
            "retired_tools": sorted(retired),
        }
    summary["total"] = sum(part["rows"] for part in summary.values())
    return summary


def main() -> int:
    try:
        summary = validate_all()
    except (OSError, ValidationError) as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, indent=2, sort_keys=True))
    print("VALID")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
