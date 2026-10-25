"""Trello's one-click sign-in: the app's public API key and Trello's own authorize page.

Trello has no OAuth 2 for its REST API. What it has is older and simpler: the app sends the person
to `trello.com/1/authorize` with its API key, the person presses Allow, and Trello sends the browser
back to the app with a token that does not expire. The token comes back in the URL fragment
(`#token=…`), which a browser never sends to a server, so the landing page on Reeve's own loopback
origin reads it and posts it back to the same origin, with the flow's `state` written into that page.
A page on another site cannot read the landing page (no CORS for it), so it cannot learn the state,
and a post without it is turned away: no other site can slip its own account into a sign-in.

The API key identifies the app and is public by nature (every Trello client ships its own), so it
belongs in the build like the Google client id; `REEVE_TRELLO_API_KEY` overrides it.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import webbrowser
from typing import Optional
from urllib.parse import urlencode

AUTHORIZE = "https://trello.com/1/authorize"
CALLBACK_PATH = "/connectors/trello/callback"
#: Read and write boards, and `account` for the member's own profile (who "me" is).
SCOPE = "read,write,account"
#: How long a sign-in waits for the person to press Allow.
TIMEOUT_S = 300


def authorize_url(key: str, return_url: str) -> str:
    return AUTHORIZE + "?" + urlencode({
        "expiration": "never", "name": "Reeve", "scope": SCOPE, "response_type": "token",
        "key": key, "callback_method": "fragment", "return_url": return_url,
    })


class TokenGrant:
    """The one sign-in in flight. A new one replaces a stale one whose tab was lost."""

    def __init__(self) -> None:
        self.state: Optional[str] = None
        self._waiting: Optional[asyncio.Future] = None

    async def run(self, url: str) -> str:
        """Open the authorize page and wait for the token the landing page posts back."""
        if self._waiting is not None and not self._waiting.done():
            self._waiting.cancel()
        loop = asyncio.get_running_loop()
        self.state = secrets.token_urlsafe(24)
        self._waiting = loop.create_future()
        _remember_url(url)
        await loop.run_in_executor(None, webbrowser.open, url)
        try:
            return await asyncio.wait_for(self._waiting, timeout=TIMEOUT_S)
        except asyncio.TimeoutError:
            raise RuntimeError(f"the Trello sign-in was not finished in {TIMEOUT_S // 60} minutes") from None
        finally:
            self.state, self._waiting = None, None

    def deliver(self, token: str, state: str) -> bool:
        """The landing page's post. False (and the flow still waits) unless a sign-in is waiting
        and the state is its own."""
        if self._waiting is None or self._waiting.done() or not self.state or not token:
            return False
        if not secrets.compare_digest(str(state), self.state):
            return False
        self._waiting.set_result(token.strip())
        return True


GRANT = TokenGrant()


def _remember_url(url: str) -> None:
    """The connect sheet's "open the page again" asks the MCP sign-in for its last page; the
    Trello page is kept there too, so the same link works for it."""
    from harness.extensions.mcp.oauth.flow import FLOW

    FLOW.last_authorize_url = url


def landing_script(state: str) -> str:
    """The script of the page Trello sends the browser back to: it reads the token from the
    fragment and posts it, with the state, to the same path, then says how it went in the page's
    title and line. The token never appears in a URL that is sent anywhere."""
    return (
        "<script>(function(){"
        "var token=new URLSearchParams(location.hash.slice(1)).get('token');"
        "history.replaceState(null,'',location.pathname);"
        "var title=document.querySelector('h1'),line=document.querySelector('p');"
        "function say(t,d){if(title)title.textContent=t;if(line)line.textContent=d;}"
        "if(!token){say('Trello sent no token','The sign-in was cancelled or refused. Return to Reeve and try again.');return;}"
        f"fetch({json.dumps(CALLBACK_PATH)},{{method:'POST',headers:{{'Content-Type':'application/json'}},"
        f"body:JSON.stringify({{token:token,state:{json.dumps(state or '')}}})}})"
        ".then(function(r){return r.json();})"
        ".then(function(b){b.ok?say('Connected','Trello is connected. You can close this tab and return to Reeve.')"
        ":say('Nothing waiting for this sign-in','It may have timed out. Return to Reeve and connect again.');})"
        ".catch(function(){say('Reeve did not answer','Return to Reeve and connect again.');});"
        "})();</script>"
    )
