"""Interactive browser sign-in flow for Claude subscription OAuth with PKCE.

Runs on an explicit user action (e.g. Settings button), binding loopback port 53692
and opening the browser to https://claude.ai/oauth/authorize.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import secrets as pysecrets
from typing import Any, Optional
from urllib.parse import parse_qs, urlencode, urlsplit

from .constants import (
    AUTHORIZE_URL,
    CALLBACK_PATH,
    CALLBACK_PORT,
    client_id,
    FLOW_TIMEOUT_SECONDS,
    PORT_BUSY_ERROR,
    REDIRECT_URI,
    SCOPE,
    AnthropicSubscriptionAuthError,
)
from .tokens import AnthropicSubscriptionTokenStore, exchange_code

logger = logging.getLogger(__name__)

last_authorize_url: Optional[str] = None
_active_server: Optional[asyncio.AbstractServer] = None

_PAGE = """<!doctype html><meta charset="utf-8"><title>Reeve</title>
<body style="font-family: system-ui; margin: 4rem auto; max-width: 28rem; text-align: center;">
<h2>{title}</h2><p>{body}</p></body>"""


def create_pkce() -> tuple[str, str]:
    """Return `(verifier, S256 challenge)` as defined in RFC 7636."""
    verifier = pysecrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def build_authorize_url(state: str, challenge: str) -> str:
    params = {
        "response_type": "code",
        "client_id": client_id(),
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPE,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "code": "true",
    }
    return AUTHORIZE_URL + "?" + urlencode(params)


def _page(status: str, title: str, body: str) -> bytes:
    html = _PAGE.format(title=title, body=body).encode("utf-8")
    head = (
        f"HTTP/1.1 {status}\r\nContent-Type: text/html; charset=utf-8\r\n"
        f"Content-Length: {len(html)}\r\nConnection: close\r\n\r\n"
    )
    return head.encode("ascii") + html


def answer_redirect(request_line: bytes, expected_state: str) -> tuple[bytes, Optional[str], Optional[Exception]]:
    """Inspect request line for callback code and state."""
    words = request_line.decode("ascii", errors="replace").split()
    target = urlsplit(words[1] if len(words) > 1 else "/")
    if target.path != CALLBACK_PATH:
        return _page("404 Not Found", "Not found", ""), None, None
    query = parse_qs(target.query)

    def first(name: str) -> str:
        return (query.get(name) or [""])[0]

    error, code, state = first("error"), first("code"), first("state")
    if error:
        reply = _page(
            "400 Bad Request",
            "Sign-in failed",
            "The service reported an error. Return to Reeve and try again.",
        )
        return reply, None, AnthropicSubscriptionAuthError(f"Sign-in failed — service returned: {error}")
    if not code or not pysecrets.compare_digest(state, expected_state):
        reply = _page(
            "400 Bad Request",
            "Nothing waiting for this sign-in",
            "The sign-in may have timed out. Return to Reeve and start it again.",
        )
        return reply, None, None
    return _page("200 OK", "Signed in", "You can close this tab and return to Reeve."), code, None


async def _start_callback_server(expected_state: str) -> tuple[asyncio.AbstractServer, "asyncio.Future[str]"]:
    settled: asyncio.Future[str] = asyncio.get_running_loop().create_future()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request_line = await reader.readline()
            while await reader.readline() not in (b"\r\n", b"\n", b""):
                pass
            reply, code, failure = answer_redirect(request_line, expected_state)
            writer.write(reply)
            if not settled.done():
                if failure is not None:
                    settled.set_exception(failure)
                elif code is not None:
                    settled.set_result(code)
        except Exception as exc:
            if not settled.done():
                settled.set_exception(exc)
        finally:
            try:
                await writer.drain()
                writer.close()
            except Exception:
                pass

    try:
        server = await asyncio.start_server(handle, "127.0.0.1", CALLBACK_PORT)
    except OSError as exc:
        raise AnthropicSubscriptionAuthError(PORT_BUSY_ERROR) from exc
    return server, settled


async def sign_in(secrets: Any, open_browser: bool = True) -> dict[str, Any]:
    """Execute the interactive browser sign-in flow."""
    global last_authorize_url, _active_server
    if _active_server is not None:
        try:
            _active_server.close()
            await _active_server.wait_closed()
        except Exception:
            pass
        _active_server = None

    state = pysecrets.token_urlsafe(32)
    verifier, challenge = create_pkce()
    authorize_url = build_authorize_url(state, challenge)
    last_authorize_url = authorize_url

    server, code_future = await _start_callback_server(state)
    _active_server = server

    if open_browser:
        import webbrowser

        try:
            webbrowser.open(authorize_url)
        except Exception as exc:
            logger.warning("Could not open browser automatically: %s", exc)

    try:
        code = await asyncio.wait_for(code_future, timeout=FLOW_TIMEOUT_SECONDS)
    finally:
        server.close()
        await server.wait_closed()
        _active_server = None

    tokens = await asyncio.to_thread(exchange_code, code, verifier, state)
    store = AnthropicSubscriptionTokenStore(secrets)
    store.save(tokens)
    return {"ok": True, "account": store.account_label()}
