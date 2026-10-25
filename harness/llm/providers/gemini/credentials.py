"""Where the Gemini key and endpoint come from.

Both resolve in the same order: the environment first, then the secret store's
`provider:gemini` profile. The store matters because the desktop sidecar does not inherit the
shell's environment. Vertex never asks here; its endpoint comes from a project and location.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from ..endpoints import normalize_base_url


def resolve_api_key(secrets: Any = None) -> Optional[str]:
    """`GEMINI_API_KEY`, then `GOOGLE_API_KEY` (the SDK's own name), then the stored key."""
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if key:
        return key
    if secrets is not None:
        return (secrets.get("provider:gemini") or {}).get("api_key") or None
    return None


def resolve_base_url(secrets: Any = None) -> Optional[str]:
    """A custom Generative Language endpoint, or None to leave the choice to the SDK (which
    keeps generativelanguage.googleapis.com)."""
    from_env = normalize_base_url(os.environ.get("GEMINI_BASE_URL"))
    if from_env:
        return from_env
    if secrets is not None:
        return normalize_base_url((secrets.get("provider:gemini") or {}).get("base_url"))
    return None
