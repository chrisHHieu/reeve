"""The interactive sign-in: open the browser, then wait for the loopback callback.

There is a single slot. A sign-in is driven by the person, so running two at once means nothing, and
a new one cancels a stale one whose browser tab was lost.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import webbrowser
from typing import Optional
from urllib.parse import parse_qs, urlsplit

from .errors import InteractiveAuthRequired

logger = logging.getLogger(__name__)

# How long a connect waits for the person to finish signing in in the browser.
FLOW_TIMEOUT_SECONDS = 300

_NEEDS_SIGN_IN = "sign-in required — reconnect this server from its page"


class SignInFlow:
    def __init__(self) -> None:
        self.pending: Optional[asyncio.Future] = None
        # The last authorize URL the person was sent to. It is surfaced over REST so the GUI can
        # offer a "reopen sign-in page" link when the browser popup was lost.
        self.last_authorize_url: Optional[str] = None
        # The `state` in the current authorize URL. The SDK re-checks the returned state itself
        # (mcp.client.auth.oauth2, compare_digest), so this is not the CSRF guard. It is a loopback
        # gate. Without it any local caller could hit /mcp/oauth/callback with a bogus code and use
        # up the single pending future, aborting the person's real sign-in, which would then find
        # nothing pending. Matching the state here turns that stray callback away and leaves the
        # flow waiting for the genuine one.
        self.expected_state: Optional[str] = None

    def deliver(self, code: str, state: Optional[str]) -> bool:
        """Resolve the waiting flow from the loopback route. False when none is waiting.

        A callback whose `state` does not match is ignored without using up the pending future, so a
        stray or forged local request cannot abort a live sign-in. Only the browser redirect that
        carries the SDK's own state resolves it."""
        if self.pending is None or self.pending.done():
            return False
        # Enforced only when a state was captured for this flow. A flow whose authorize URL had none
        # falls back to accepting any callback, as before.
        if self.expected_state is not None and (
            state is None or not secrets.compare_digest(state, self.expected_state)
        ):
            return False
        waiting, self.pending = self.pending, None
        waiting.set_result((code, state))
        return True

    async def open_browser(self, url: str) -> None:
        self.last_authorize_url = url
        self.expected_state = _state_of(url)
        logger.info("mcp oauth: opening browser for sign-in")
        await asyncio.get_running_loop().run_in_executor(None, webbrowser.open, url)

    async def refuse_browser(self, url: str) -> None:
        """The redirect handler for a non-interactive context. It never opens a browser, but it keeps
        the URL so the GUI's "reopen sign-in page" still works after the refusal."""
        self.last_authorize_url = url
        raise InteractiveAuthRequired(_NEEDS_SIGN_IN)

    async def refuse_callback(self) -> tuple[str, Optional[str]]:
        raise InteractiveAuthRequired(_NEEDS_SIGN_IN)

    async def wait_for_callback(self) -> tuple[str, Optional[str]]:
        if self.pending is not None and not self.pending.done():
            self.pending.cancel()  # a stale flow lost its browser tab, and the new one wins
        self.pending = asyncio.get_running_loop().create_future()
        try:
            return await asyncio.wait_for(self.pending, timeout=FLOW_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            raise RuntimeError(
                "sign-in timed out — the browser window was not completed in "
                f"{FLOW_TIMEOUT_SECONDS // 60} minutes"
            )
        finally:
            self.pending = None
            self.expected_state = None  # this flow's state must not gate the next one


def _state_of(url: str) -> Optional[str]:
    """The `state` query parameter of an authorize URL, or None when it has none."""
    values = parse_qs(urlsplit(url).query).get("state")
    return values[0] if values else None


FLOW = SignInFlow()
