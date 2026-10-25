"""Where the OpenAI key comes from."""

from __future__ import annotations

import os
from typing import Any, Optional


def resolve_api_key(secrets: Any = None) -> Optional[str]:
    """`OPENAI_API_KEY` from the environment, else the secret store's `provider:openai`
    profile. The store is what lets the desktop sidecar, which does not inherit the shell
    environment, use a key entered in Settings. The key only configures the SDK client; it
    never enters the model's context."""
    key = os.environ.get("OPENAI_API_KEY")
    if key:
        return key
    if secrets is not None:
        return (secrets.get("provider:openai") or {}).get("api_key") or None
    return None
