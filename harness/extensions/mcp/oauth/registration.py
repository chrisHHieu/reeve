"""Deciding when a stored client registration can no longer be used."""

from __future__ import annotations

import logging
from typing import Any

from harness.safety.secrets import SecretStore

from .profile import profile_name, redirect_uri

logger = logging.getLogger(__name__)

# What goes when a registration is discarded. The tokens and the discovered metadata are bound to the
# registration that is leaving, and keeping either would only fail later and less legibly.
_BOUND_TO_REGISTRATION = ("client_info", "tokens", "tokens_issued_at", "oauth_metadata")


def redirect_is_stale(profile: dict[str, Any]) -> bool:
    """Whether the stored registration names a redirect that can no longer be served.

    The redirect is `http://127.0.0.1:<sidecar port>/mcp/oauth/callback` and the port differs
    between runs, so a client registered earlier points at a port nothing listens on. Registering
    again is free, and failing at the vendor's authorize page is not. A registration with no
    recorded redirect is left alone, since absence is not proof."""
    stored = (profile.get("client_info") or {}).get("redirect_uris") or []
    if not stored:
        return False
    return redirect_uri() not in [str(uri) for uri in stored]


def invalidate_stale_registration(server_name: str, server_url: str, secrets: SecretStore) -> bool:
    """Drop a DCR registration that belongs to a different endpoint. True when it did.

    A dynamic client id is issued by one authorization server for one resource. Point the same server
    entry at a new URL (an endpoint version bump, a region move) and the SDK reuses the cached id
    against a server that never issued it. The vendor answers `invalid_request / failed to retrieve
    client`, and its error page says only "we couldn't identify the app requesting access". Nothing
    names the cache, and no amount of retrying clears it. That happened on Atlassian's v1 to v2 move
    (2026-09-05), which their own migration note warns about. Registering again is free and
    automatic, so a mismatch is discarded.

    An old profile carries no stamp, and absence is ambiguous. With no tokens there is nothing to
    lose, so it counts as stale and registers again. With tokens, a working connection is not broken
    on a guess, and the stamp is written for next time instead."""
    key = profile_name(server_name)
    profile = secrets.get(key) or {}
    if not profile.get("client_info"):
        return False
    stamped = profile.get("registered_for")
    # A recorded endpoint that no longer matches is decided first, and on its own.
    #
    # It once shared a branch with the redirect check below, which hid it. That check answers by
    # setting `stamped = None`, meaning "unrecorded", and an unrecorded profile with tokens is
    # deliberately left alone. So an endpoint change arriving on a restarted app (a new sidecar port,
    # hence a stale redirect, which is the ordinary case) was swallowed, and the tokens minted for
    # the old endpoint were re-stamped as the new one's. The Postman variant switch (/minimal to
    # /mcp) found it on 2026-09-08, where changing endpoints is ordinary for a person and not a
    # one-time vendor migration.
    moved = stamped is not None and stamped != server_url
    if not moved:
        # A registration is equally dead when the redirect no longer matches. The sidecar's port
        # changes between runs and the redirect is built from it, so a client registered in an
        # earlier run names a port nothing listens on now. The vendor then refuses at the authorize
        # step. Atlassian says "The app's callback URL is invalid. Contact the app developer", a
        # dead end, since the developer cannot fix a port on someone else's machine (jira registered
        # for :65531 and monday for :50093, both restarted onto :8765).
        #
        # A live connection stays safe: the refresh grant carries no redirect, and the
        # `stamped is None and tokens` branch below returns before anything is discarded.
        if redirect_is_stale(profile):
            stamped = None  # as dead as a wrong endpoint, so fall through
        elif stamped == server_url:
            return False
        if stamped is None and profile.get("tokens"):
            secrets.put(key, {**profile, "registered_for": server_url})
            return False
    logger.info(
        "mcp %s: discarding a client registration issued for %s (now %s)",
        server_name,
        stamped or "an unrecorded endpoint",
        server_url,
    )
    kept = {k: v for k, v in profile.items() if k not in _BOUND_TO_REGISTRATION}
    secrets.put(key, {**kept, "registered_for": server_url})
    return True
