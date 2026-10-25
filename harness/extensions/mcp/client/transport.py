"""The HTTP client every MCP request goes through."""

from __future__ import annotations

from typing import Any, Optional

import httpx

# Sent on every outbound MCP request, so Reeve identifies itself as any HTTP client does.
MCP_USER_AGENT = "reeve-mcp/1.0"


class UserAgentTransport(httpx.AsyncHTTPTransport):
    """Guarantee a User-Agent on every request, the SDK's OAuth ones included.

    The MCP SDK builds its OAuth discovery and registration calls as bare `httpx.Request` objects
    and yields them through httpx's auth flow. Requests made that way do not pick up the client's
    default headers, because httpx applies those in `build_request`, which the SDK bypasses. They
    leave with `host` and `mcp-protocol-version` and nothing else, so with no User-Agent at all.

    A WAF reads a request with none as a bot. TCBS's server sits behind Cloudflare and answers 403
    with a challenge page (verified 2026-09-07: the same URL returns 200 the moment any user agent
    is present, and 403 again with none). The SDK then reads the failed discovery as "no metadata",
    falls back to `<origin>/register`, gets a second 403, and the connect dies before a browser ever
    opens, so the person sees a Connect button that does nothing.

    Injecting at the transport is what makes this reliable, because every request passes through
    here whether the SDK built it or we did."""

    async def handle_async_request(self, request: Any) -> Any:
        request.headers.setdefault("user-agent", MCP_USER_AGENT)
        return await super().handle_async_request(request)


def mcp_http_client(
    headers: Optional[dict[str, str]] = None, timeout: Any = None, auth: Any = None
) -> "httpx.AsyncClient":
    """`create_mcp_http_client` with the transport above. The signature must match the SDK's
    `McpHttpClientFactory`, which `streamablehttp_client` calls positionally."""
    options: dict[str, Any] = {"follow_redirects": True, "transport": UserAgentTransport()}
    if headers is not None:
        options["headers"] = headers
    options["timeout"] = timeout if timeout is not None else httpx.Timeout(30.0)
    if auth is not None:
        options["auth"] = auth
    return httpx.AsyncClient(**options)
