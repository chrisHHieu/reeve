"""Tool-call arguments as the dict a provider wire wants."""

from __future__ import annotations

import json
from typing import Any


def parse_arguments(raw: Any) -> dict[str, Any]:
    """A dict passes through, a JSON object string is parsed, and anything else is kept whole
    under `_raw` rather than dropped."""
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {"_raw": raw}
    return parsed if isinstance(parsed, dict) else {"_raw": raw}
