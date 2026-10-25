"""Every MCP request carries a User-Agent — including the ones the SDK builds itself.

The MCP SDK constructs its OAuth discovery and registration calls as bare `httpx.Request`
objects and yields them through httpx's auth flow. Requests made that way do NOT pick up
the client's default headers: httpx applies those in `build_request`, which the SDK
bypasses. They leave with `host` and `mcp-protocol-version` and nothing else.

A WAF reads a UA-less request as a bot. TCBS's MCP server sits behind Cloudflare, and on
2026-09-07 that produced a failure with no useful surface at all:

    403 GET  /.well-known/oauth-protected-resource/mcp/tcinvest   (200 with ANY user-agent)
    403 GET  /.well-known/oauth-authorization-server
    403 POST /register        <- the fallback, because discovery "found no metadata"

The connect then died before a browser ever opened. The user saw a Connect button that did
nothing: no error, no tab, no log line they could reach. Four wrong diagnoses came before
the right one, because every hand-rolled probe used `client.get()`, which supplies a
default user-agent — so the probes could not reproduce the bug they were probing.

Injection happens at the TRANSPORT because that is the one place every request passes
through, whoever built it.
"""

from __future__ import annotations

import httpx
import pytest

from harness.extensions.mcp.client import MCP_USER_AGENT, UserAgentTransport as _UserAgentTransport, mcp_http_client as _mcp_http_client


def _capture(monkeypatch):
    """Record requests at the layer BELOW ours, so the real code under test runs."""
    seen: list[httpx.Request] = []

    async def fake(self, request):
        seen.append(request)
        return httpx.Response(200, json={}, request=request)

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", fake)
    return seen


@pytest.mark.asyncio
async def test_a_request_built_without_headers_still_leaves_with_a_user_agent(
    monkeypatch,
):
    """This is the exact shape the SDK sends: a raw Request, no client defaults."""
    seen = _capture(monkeypatch)
    request = httpx.Request(
        "GET",
        "https://example.test/.well-known/oauth-protected-resource",
        headers={"mcp-protocol-version": "2025-11-25"},
    )
    assert "user-agent" not in request.headers  # the bug, before we touch it

    await _UserAgentTransport().handle_async_request(request)

    assert seen and seen[0].headers["user-agent"] == MCP_USER_AGENT


@pytest.mark.asyncio
async def test_a_caller_supplied_user_agent_is_not_overwritten(monkeypatch):
    seen = _capture(monkeypatch)
    request = httpx.Request(
        "GET", "https://example.test/x", headers={"user-agent": "someone-else/9"}
    )
    await _UserAgentTransport().handle_async_request(request)
    assert seen[0].headers["user-agent"] == "someone-else/9"


@pytest.mark.asyncio
async def test_the_client_the_transport_layer_uses_carries_the_injection(monkeypatch):
    """`_mcp_http_client` is what `streamablehttp_client` calls; the guard is useless if
    the factory forgets to install the transport."""
    seen = _capture(monkeypatch)
    async with _mcp_http_client() as client:
        # `send` on a pre-built Request is precisely how the SDK's auth flow dispatches.
        await client.send(httpx.Request("GET", "https://example.test/register"))
    assert seen[0].headers["user-agent"] == MCP_USER_AGENT


@pytest.mark.asyncio
async def test_the_factory_keeps_the_settings_the_sdk_relies_on(monkeypatch):
    """It replaces `create_mcp_http_client`, so it must not quietly drop its behaviour:
    redirects followed, a timeout set, and caller headers and auth honoured."""
    _capture(monkeypatch)
    async with _mcp_http_client(headers={"x-test": "1"}) as client:
        assert client.follow_redirects is True
        assert client.timeout.read is not None
        assert client.headers["x-test"] == "1"

    sentinel = httpx.BasicAuth("u", "p")
    async with _mcp_http_client(auth=sentinel) as client:
        assert client.auth is sentinel


@pytest.mark.asyncio
async def test_the_streamable_transport_is_given_our_factory(monkeypatch):
    """The injection only reaches the wire if `_open_http` passes the factory through.

    Asserting on the source is deliberate: exercising `_serve` means standing up a real
    transport, and the thing that broke was one keyword argument going missing.
    """
    import inspect

    from harness.extensions.mcp.client import serve as mod

    source = inspect.getsource(mod.ServeMixin._open_http)
    assert "httpx_client_factory=mcp_http_client" in source, (
        "streamablehttp_client is building its own client again; the SDK's OAuth "
        "requests will go out with no user-agent and a WAF will refuse them"
    )
