"""Where the Anthropic key and endpoint come from.

Both resolve in the same order: the environment first, then the secret store's
`provider:anthropic` profile. The store matters because the desktop sidecar does not inherit
the shell's environment, so a value entered in Settings has to work on its own.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from ..endpoints import normalize_base_url


def resolve_api_key(secrets: Any = None) -> Optional[str]:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key
    if secrets is not None:
        return (secrets.get("provider:anthropic") or {}).get("api_key") or None
    return None


def resolve_base_url(secrets: Any = None) -> Optional[str]:
    """A custom Messages-API endpoint, or None to leave the choice to the SDK (which keeps
    `api.anthropic.com` and its own `ANTHROPIC_BASE_URL` handling)."""
    from_env = normalize_base_url(os.environ.get("ANTHROPIC_BASE_URL"))
    if from_env:
        return from_env
    if secrets is not None:
        return normalize_base_url((secrets.get("provider:anthropic") or {}).get("base_url"))
    return None
