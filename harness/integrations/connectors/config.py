"""Which messaging platforms are on, and who may write to the agent through them.

Each platform's token is in the secret store (`<platform>:default`); this module decides whether
it is on and who is let in. The allow-list guards what comes in: an empty list lets nobody in (the
person adds their own id first), and `allow_all` lets everyone in.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

from harness.safety.secrets import SecretStore

from .messaging import SessionSource

PLATFORMS = ("telegram", "slack")
_YES = ("1", "true", "yes")


@dataclass
class ConnectorSettings:
    platform: str
    enabled: bool = False
    allowed_users: set[str] = field(default_factory=set)
    allow_all: bool = False


def is_authorized(settings: ConnectorSettings, source: SessionSource) -> bool:
    """Whether the sender of a message may reach the agent: everyone with `allow_all`, otherwise
    only an id on the list (a message with no sender id never)."""
    return settings.allow_all or (bool(source.user_id) and source.user_id in settings.allowed_users)


def _ids(value: Optional[str]) -> set[str]:
    """The ids of a comma-separated list."""
    return {part.strip() for part in (value or "").split(",") if part.strip()}


def _settings_for(platform: str, profile: dict) -> ConnectorSettings:
    env = platform.upper()
    return ConnectorSettings(
        platform=platform,
        enabled=bool(profile.get("bot_token")) and profile.get("enabled", True),
        allowed_users=set(profile.get("allowed_users") or ()) | _ids(os.environ.get(f"{env}_ALLOWED_USERS")),
        allow_all=bool(profile.get("allow_all")) or os.environ.get(f"{env}_ALLOW_ALL_USERS", "").lower() in _YES,
    )


def load_settings(secrets: Optional[SecretStore] = None) -> dict[str, ConnectorSettings]:
    """Every platform's settings, from its profile and the environment.

    On when it has a token and the profile has not switched it off. Who is let in comes from the
    profile and from `<PLATFORM>_ALLOWED_USERS` / `<PLATFORM>_ALLOW_ALL_USERS`: the environment's
    ids are added to the profile's, and either one can let everyone in."""
    secrets = secrets or SecretStore()
    return {platform: _settings_for(platform, secrets.get(f"{platform}:default") or {}) for platform in PLATFORMS}
