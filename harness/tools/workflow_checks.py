"""What a `workflow` call must be before anything runs: its identity block and a script
that only coordinates. Split from `workflow.py` (size ceiling); the host checks both before
the child process starts, so a script that would misbehave never launches an agent.
"""

from __future__ import annotations

from typing import Any, Optional


def validate_meta(meta: Any) -> Optional[str]:
    """`None` when the identity block is usable, else the reason. Meta is DATA: the host
    never evaluates anything to obtain it."""
    if not isinstance(meta, dict):
        return "meta must be a JSON object"
    name = meta.get("name")
    if not isinstance(name, str) or not name.strip() or any(c.isspace() for c in name.strip()):
        return "meta.name must be a short kebab-case string"
    description = meta.get("description")
    if not isinstance(description, str) or not description.strip():
        return "meta.description must be a non-empty string"
    when_to_use = meta.get("whenToUse")
    if when_to_use is not None and not isinstance(when_to_use, str):
        return "meta.whenToUse must be a string"
    phases = meta.get("phases")
    if phases is not None:
        if not isinstance(phases, list):
            return "meta.phases must be an array"
        for entry in phases:
            if (
                not isinstance(entry, dict)
                or not isinstance(entry.get("title"), str)
                or not entry["title"].strip()
            ):
                return "each meta.phases entry needs a non-empty title"
            # Phase declarations may name the LLM target a phase is expected to use
            # (`{title, detail?, provider?, model?}`); they are documentation the plan
            # carries, so the check is the type, not the route.
            for key in ("detail", "provider", "model"):
                value = entry.get(key)
                if value is not None and not isinstance(value, str):
                    return f"meta.phases[].{key} must be a string"
    return None


#: Calls a coordinating script has no business making — the agents do the real work,
#: under the normal tool gates.
_BLOCKED_CALLS = frozenset({"open", "eval", "exec", "compile", "__import__", "input"})
#: Dunder attributes a script may read: a function's or class's name, for labels.
_ALLOWED_DUNDERS = frozenset({"__name__"})
#: Modules that would make a resumed run differ from the run it replays.
_PER_RUN_MODULES = frozenset({"time", "random", "datetime", "uuid", "secrets"})


def _import_refusal(node: Any) -> Optional[str]:
    """Why an import may not run in a workflow body, or None when it may."""
    import ast

    from .code_child_sandbox import WORKFLOW_IMPORTS

    if isinstance(node, ast.ImportFrom):
        names = [str(node.module or "")] if node.level == 0 else [""]
    else:
        names = [alias.name for alias in node.names]
    for name in names:
        root = name.split(".")[0]
        if root in WORKFLOW_IMPORTS:
            continue
        allowed = ", ".join(sorted(WORKFLOW_IMPORTS))
        if root in _PER_RUN_MODULES:
            return (
                f"import {name} is not available in a workflow script: a value that differs "
                "per run would make a resumed run replay the wrong results — pass the time, "
                f"date or seed in `args` instead (importable: {allowed})"
            )
        return (
            f"import {name or '(relative)'} is not available in a workflow script — a script "
            f"only coordinates; the agents do the file and command work (importable: {allowed})"
        )
    return None


def validate_script(script: str) -> Optional[str]:
    """`None` when the body only COORDINATES, else why it does not.

    The rule made local: a workflow script calls helpers and glues their results; it
    does not touch files, reach the machine, or run dynamic code — that is what `run_code`
    is for, and what the agents inside `agent()` are for. It may import the pure
    standard-library modules in `code_child_sandbox.WORKFLOW_IMPORTS` (models write
    `import json` / `import re` out of habit). Enforced before the child starts, so a
    script that would misbehave never launches an agent.
    """
    import ast

    try:
        tree = ast.parse(script)
    except SyntaxError as exc:
        return f"the workflow script does not parse: line {exc.lineno}: {exc.msg}"
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            refusal = _import_refusal(node)
            if refusal:
                return refusal
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in _BLOCKED_CALLS
        ):
            return (
                f"{node.func.id}() is not available in a workflow script — a script only "
                "coordinates; the agents do the file and command work"
            )
        if (
            isinstance(node, ast.Attribute)
            and node.attr.startswith("__")
            and node.attr not in _ALLOWED_DUNDERS
        ):
            return (
                f"attribute access to {node.attr!r} is not allowed in a workflow script "
                "— a script only coordinates"
            )
    return None
