"""The opt-in for experimental connectors, the ones used at the person's own risk."""

from __future__ import annotations

from typing import Any

from harness.safety.secrets import SecretStore

_KEY = "experimental:settings"


def experimental_enabled(secrets: SecretStore) -> bool:
    return bool((secrets.get(_KEY) or {}).get("enabled"))


def set_experimental_enabled(secrets: SecretStore, value: bool) -> dict[str, Any]:
    secrets.put(_KEY, {"enabled": bool(value)})
    return {"ok": True, "enabled": bool(value)}
