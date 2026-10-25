"""API-key auth for an MCP server: the connector's pasted key, sent as a bearer.

The simplest of the sign-in shapes, and the right one whenever a vendor accepts a plain
key. The others:

  - `oauth.py`       — the SDK drives everything: discovery, DCR, browser, refresh.
                       Atlassian, Notion, Postman, Upwork, TCBS.
  - `device_auth.py` — RFC 8628, for a vendor with no DCR *and* a token endpoint that
                       demands a secret a desktop app cannot keep. GitHub.
  - `pkce_auth.py`   — browser code+PKCE against the operator's client id. Google.

Firecrawl is the first server here (2026-09-07). Its MCP endpoint answers `initialize`
with 200 and no `www-authenticate` — anonymous is a supported tier — so the SDK's OAuth,
which only starts after a 401, would never fire: it would connect anonymously, report
success, and serve the 3-tool anonymous set instead of the 25 an account gets. Nothing
would look broken. Sending the key as a header is what makes the difference, and it is
what Firecrawl's own documentation calls API Key Mode.

The key lives in the connector's own profile, where the user typed it and where
`setup.py` reads it to decide the card is connected. It is deliberately NOT copied into
mcp.json, which is plain text and gets pasted between machines.
"""

from __future__ import annotations

from typing import Optional

from harness.safety.secrets import SecretStore


def api_key_header(connector: str, secrets: SecretStore) -> Optional[dict[str, str]]:
    """`Authorization: Bearer <key>`, or None when no key is stored.

    None must mean "do not connect". Connecting without the header is not a smaller
    failure than erroring — against a server like Firecrawl it succeeds, quietly, with a
    fraction of the tools.
    """
    key = str((secrets.get(f"{connector}:default") or {}).get("api_key") or "").strip()
    return {"Authorization": f"Bearer {key}"} if key else None
