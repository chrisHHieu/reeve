"""Reeve's routed id → the id models.dev files the same model under.

Vertex and Bedrock put a ROUTING segment in the id (`vertex:gemini/gemini-3.7-flash`,
`vertex:openweight/meta/llama-4-…-maas`, `bedrock:claude/anthropic.claude-haiku-4-5-…`)
that tells the provider which wire to use. models.dev knows nothing of it: it files the
same models as `vertex:gemini-3.7-flash`, `vertex:meta/llama-4-…-maas`,
`bedrock:anthropic.claude-haiku-4-5-…`. Looked up by the routed id, every curated Vertex
row missed (6 of 6 on 2026-10-01), so the picker showed `Reasoning —` and `Cost —` for
models the catalogue prices and flags, and the spend estimate for a Vertex session was
`None` — "unknown", which a cost badge reads as nothing at all.

Only the routing segment is removed, and only an id that exists in the catalogue is
returned: no fuzzy matching, because a price attached to the wrong model is worse than
none. Claude on Vertex is the one place the catalogue id carries something Reeve's id does
not — a version suffix (`claude-sonnet-4-6@default`, `claude-haiku-4-5@20251001`): the
`@default` row is taken, else the newest dated one.
"""

from __future__ import annotations

from typing import Any, Optional

#: provider → the routing segments its client reads (`VertexProvider._split`,
#: `BedrockProvider._split`). Anything else in front of a `/` belongs to the model id.
_ROUTING = {
    "vertex": ("gemini", "claude", "openweight"),
    "bedrock": ("claude", "other"),
}


def catalogue_id(model: str, entries: dict[str, Any]) -> Optional[str]:
    """The key in `entries` for a routed model id, or None when the catalogue has no such model."""
    if model in entries:
        return model
    provider, _, bare = model.partition(":")
    segments = _ROUTING.get(provider)
    if not segments:
        return None
    family, slash, rest = bare.partition("/")
    if slash and family in segments and rest:
        bare = rest
    plain = f"{provider}:{bare}"
    if plain in entries:
        return plain
    if provider == "vertex" and bare.startswith("claude") and "@" not in bare:
        return _versioned(plain, entries)
    return None


def _versioned(plain: str, entries: dict[str, Any]) -> Optional[str]:
    if f"{plain}@default" in entries:
        return f"{plain}@default"
    dated = [key for key in entries if key.startswith(f"{plain}@")]
    return max(dated) if dated else None
