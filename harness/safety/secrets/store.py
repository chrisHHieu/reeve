"""The secret store: connector and MCP credentials in one file.

Secrets never enter the model's context, a prompt or a trace. Profiles are keyed `connector` or
`connector:account`. A value may be the literal or a `${ENV_VAR}` reference, resolved at read time
from the process environment or `.env` beside the store. Today the backing is a private JSON file
behind this interface, which is what callers depend on, so a keychain or an encrypted file can
replace it without touching them.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Optional

from harness.config import known_secrets

from .locations import state_dir
from .private_files import atomic_private_write

_REFERENCE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

# Profiles nothing reads any more. A key left under one is a live credential on disk with no
# setting that shows it and no button that clears it, so it is removed when the store is next
# opened. `decision:key` was the decision layer's own.
RETIRED_PROFILES = ("decision:key",)


def load_dotenv(path: Path) -> dict[str, str]:
    """`KEY=value` lines of a `.env` file; comments and blank lines are skipped, quotes stripped."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def client_env(key: str) -> str:
    """An OAuth client id or secret the operator supplied, or "" if there is none.

    The process environment is read first, then the state folder's `.env`, the same file `${VAR}`
    references resolve against. No client id or secret ships in the source tree: a value committed
    there is in every clone and every scan of it, so a one-click sign-in works where its operator
    put one here, and the manual path works everywhere else.
    """
    if not key:
        return ""
    live = (os.environ.get(key, "") or "").strip()
    if live:
        return live
    try:
        return (load_dotenv(state_dir() / ".env").get(key, "") or "").strip()
    except Exception:
        return ""


class SecretStore:
    """Reads resolve `${VAR}` references, and `status` never carries a value."""

    def __init__(self, path: Optional[str | Path] = None) -> None:
        self.path = Path(path).expanduser() if path else state_dir() / "secrets.json"
        self._dotenv_path = self.path.parent / ".env"
        self._lock = threading.Lock()

    def get(self, profile: str) -> Optional[dict[str, Any]]:
        """The profile with its references resolved, or None when there is no such profile."""
        data = self._read().get(profile)
        if data is None:
            return None
        resolved = self.resolve(data)
        known_secrets.remember(resolved)  # never written into a log or shown to a model
        return resolved

    def resolve(self, value: Any) -> Any:
        """Resolve `${VAR}` references anywhere in `value` from the environment and the `.env`."""
        dotenv = load_dotenv(self._dotenv_path)

        def swap(match: re.Match) -> str:
            name = match.group(1)
            return os.environ.get(name) or dotenv.get(name) or match.group(0)

        def walk(item: Any) -> Any:
            if isinstance(item, str):
                return _REFERENCE.sub(swap, item)
            if isinstance(item, dict):
                return {key: walk(inner) for key, inner in item.items()}
            if isinstance(item, list):
                return [walk(inner) for inner in item]
            return item

        return walk(value)

    def status(self) -> list[dict[str, Any]]:
        """What each profile is, without any of its secret values."""
        rows: list[dict[str, Any]] = []
        for profile, data in self._read().items():
            data = data if isinstance(data, dict) else {}
            expires = data.get("expires")
            rows.append(
                {
                    "profile": profile,
                    "type": data.get("type"),
                    "account": data.get("account_id"),
                    "expired": bool(isinstance(expires, (int, float)) and expires < time.time()),
                }
            )
        return rows

    def put(self, profile: str, data: dict[str, Any]) -> None:
        known_secrets.remember(data)
        with self._lock:
            held = self._read()
            held[profile] = data
            self._write(held)

    def delete(self, profile: str) -> bool:
        with self._lock:
            held = self._read()
            if profile not in held:
                return False
            del held[profile]
            self._write(held)
            return True

    def _read(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _write(self, held: dict[str, Any]) -> None:
        atomic_private_write(self.path, json.dumps(held, indent=2))


def drop_retired_profiles(store: SecretStore) -> list[str]:
    """Delete the retired profiles still in `store`, and return which were removed.

    It is best effort: a store that cannot be read or written keeps what it has and the next start
    tries again. It never raises, because a cleanup must not cost a start."""
    removed: list[str] = []
    for profile in RETIRED_PROFILES:
        try:
            if store.delete(profile):
                removed.append(profile)
        except Exception:
            continue
    return removed
