"""How the discovery bridge's results read to the model, and their bounds.

Split from `discovery.py` (size ceiling), which keeps the tools and the intake unwrap. The text is
model-facing, so a bound here is a bound on the request that follows.
"""

from __future__ import annotations

import json
from typing import Any

from .discovery_catalog import SEARCH_PARAMETERS_CHARS, _clip
from .discovery_schemas import DEFAULT_RESULTS, MAX_RESULTS

#: How many names an empty-query listing returns. The catalog is tens of entries on a
#: loaded install; this keeps the listing from ever becoming the payload it replaced.
MAX_LISTED = 50
#: Rendered budgets.
MAX_RENDER_CHARS = 4_000
MAX_DESCRIBE_CHARS = 4_000
#: One-line description in search results.
DESC_CHARS = 140


def capped(limit: Any) -> int:
    """A search's result count: `limit` within 1..MAX_RESULTS, the default when it is not a number."""
    try:
        return max(1, min(int(limit), MAX_RESULTS))
    except (TypeError, ValueError):
        return DEFAULT_RESULTS


def render_search(arguments: dict[str, Any], result: Any) -> str:
    if isinstance(result, dict) and result.get("error"):
        return f"Error: {result['error']}"
    matches = (result or {}).get("matches") if isinstance(result, dict) else None
    lines = []
    for m in matches or []:
        lines.append(f"- {m['name']}: {m['description']}")
        if m.get("parameters") is not None:
            lines.append(f"  parameters: {json.dumps(m['parameters'], ensure_ascii=False, default=str)}")
    body = "\n".join(lines) if lines else "No deferred tool matched."
    hint = (result or {}).get("hint", "") if isinstance(result, dict) else ""
    # One line per match, its parameters on the next; the hint, which names what was
    # loaded, is never the part a bound cuts. The parameters have their own budget.
    return f"{body[: MAX_RENDER_CHARS + SEARCH_PARAMETERS_CHARS]}\n{hint}".strip()


def render_describe(arguments: dict[str, Any], result: Any) -> str:
    if isinstance(result, dict) and result.get("error"):
        similar = (result or {}).get("similar") or []
        tail = f" Closest names: {', '.join(similar)}." if similar else ""
        return f"Error: {result['error']}.{tail}"
    name = (result or {}).get("name", "")
    description = (result or {}).get("description", "")
    parameters = json.dumps(
        (result or {}).get("parameters") or {}, ensure_ascii=False, default=str
    )
    hint = (result or {}).get("hint", "")
    return _clip(f"{name}: {description}\nparameters: {parameters}\n{hint}".strip(), MAX_DESCRIBE_CHARS)


def render_call_error(arguments: dict[str, Any], result: Any) -> str:
    error = (result or {}).get("error") if isinstance(result, dict) else result
    return f"Error: {error}"
