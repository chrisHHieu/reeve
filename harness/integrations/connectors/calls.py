"""The HTTP call every connector tool makes to a vendor's API.

Tools call `calls.request(...)` through the module, so a test can stand in for the network by
replacing that one name. The vendor endpoints are written into the tools, so they skip the
address guard and its DNS lookup; a URL the model supplies is the exception and must pass
`check_addresses=True`.
"""

from __future__ import annotations

from typing import Any

from ..web.guard import get_checked


def _send_again_if_token_died(response, client, method, url, *, headers, params, json, auth):
    """Send the call again, once, with a renewed Google token when a 401 says the one it carried
    is dead. Any other response, and any 401 that is not a Google bearer this process issued,
    is returned as it came."""
    if response.status_code != 401 or not headers:
        return response
    scheme, _, token = str(headers.get("Authorization") or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        return response
    from .google_auth import renew_after_401
    from .microsoft_auth import renew_after_401 as renew_microsoft

    # Each ledger knows only the tokens its own sign-in handed out, so asking both is safe.
    renewed = renew_after_401(token) or renew_microsoft(token)
    if not renewed:
        return response
    return client.request(
        method, url, headers={**headers, "Authorization": f"Bearer {renewed}"},
        params=params, json=json, auth=auth,
    )


def _body(response) -> Any:
    """The response body: JSON when it is declared and parses, else text, else "".

    An empty body is legal on a 204 and common on a 401 or 403, and vendors send it with
    `content-type: application/json` anyway (Redmine does both). Parsing that raises, and the
    exception used to escape to the handler and replace the status line with "Expecting value:
    line 1 column 1". The status line is the part that carries the meaning.
    """
    if not response.content.strip():
        return ""
    if "json" in response.headers.get("content-type", "").lower():
        try:
            return response.json()
        except ValueError:
            return response.text
    return response.text


def request(
    method: str,
    url: str,
    *,
    headers=None,
    params=None,
    json=None,
    auth=None,
    check_addresses: bool = False,
) -> dict[str, Any]:
    """`{"ok": True, "data": …}` or `{"error": …}`; it does not raise.

    `check_addresses` is for URLs the model supplied. It turns off automatic redirects and walks
    the chain through the address guard, so a public URL cannot redirect into loopback or the
    cloud metadata endpoint.
    """
    try:
        import httpx

        with httpx.Client(timeout=30.0, follow_redirects=not check_addresses) as client:
            if check_addresses:
                if method.upper() != "GET":
                    return {"error": "address-checked requests must be GET"}
                try:
                    response = get_checked(client, url)
                except PermissionError as exc:
                    return {"error": str(exc)}
            else:
                response = client.request(method, url, headers=headers, params=params, json=json, auth=auth)
                response = _send_again_if_token_died(
                    response, client, method, url, headers=headers, params=params, json=json, auth=auth
                )
            data = _body(response)
            if response.status_code >= 400:
                return {"error": f"HTTP {response.status_code}", "details": data}
            return {"ok": True, "data": data}
    except Exception as exc:
        return {"error": str(exc)}
