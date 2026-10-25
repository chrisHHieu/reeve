"""A GET that checks and pins every hop of a redirect chain."""

from __future__ import annotations

from urllib.parse import urljoin, urlsplit

from .vetting import pinned, vet

MAX_REDIRECTS = 5
_REDIRECT_STATUSES = (301, 302, 303, 307, 308)


def same_site(a: str, b: str) -> bool:
    """Whether two URLs are the same site for following a redirect: the same host, with a leading
    `www.` ignored (`example.com` and `www.example.com`), and scheme and port not compared, so an
    `http` to `https` upgrade is the same site."""

    def host_of(url: str) -> str:
        host = (urlsplit(url).hostname or "").lower()
        return host[4:] if host.startswith("www.") else host

    return host_of(a) == host_of(b)


def _send(client, url: str, pin: str | None, stream: bool):
    """One request, to the vetted address when there is one."""
    if pin is None:
        if stream:
            return client.send(client.build_request("GET", url), stream=True)
        return client.get(url)
    request_url, headers, extensions = pinned(url, pin)
    if stream:
        return client.send(
            client.build_request("GET", request_url, headers=headers, extensions=extensions), stream=True
        )
    return client.get(request_url, headers=headers, extensions=extensions)


def get_checked(
    client,
    url: str,
    *,
    max_redirects: int = MAX_REDIRECTS,
    stream: bool = False,
    same_host_only: bool = False,
):
    """GET `url`, validating and pinning the address before every hop.

    `client` must be built with `follow_redirects=False`. Redirects are walked here so each Location
    is checked. Every hop connects to the exact address that passed its check (see `pinned`), so a
    rebinding name cannot swap targets between check and connect. The final response comes back with
    the final logical URL, the name and not the pinned address, in `resp.extensions["logical_url"]`
    for callers that display it. It raises `PermissionError` when a hop is refused and
    `RuntimeError` when the redirect budget runs out.

    `stream=True` sends each request without buffering its body. The caller then reads a bounded
    prefix and must close the response it gets back. Redirect hops are closed here before the next
    one, so only the final response is the caller's to release.

    `same_host_only=True` stops at a redirect to a different site and returns that 3xx response
    unfollowed, with the absolute target in `resp.extensions["cross_host_redirect"]`. Consent to fetch
    is given per domain, so content from a host the person was never asked about is not read on the
    strength of a Location header. The caller reports the target, and the model asks for it, and the
    person is asked, as a request of its own."""
    seen = url
    for _ in range(max_redirects + 1):
        reason, pin = vet(seen)
        if reason:
            raise PermissionError(reason)
        resp = _send(client, seen, pin, stream)
        extensions = getattr(resp, "extensions", None)
        if resp.status_code not in _REDIRECT_STATUSES:
            if isinstance(extensions, dict):
                extensions["logical_url"] = seen
            return resp
        location = resp.headers.get("location")
        if not location:
            return resp
        # Resolved against the logical URL and not `resp.url`, which names the pinned address. A
        # relative Location has to stay on the original host.
        target = urljoin(seen, location)
        if same_host_only and not same_site(seen, target):
            if isinstance(extensions, dict):
                extensions["logical_url"] = seen
                extensions["cross_host_redirect"] = target
            return resp  # the caller closes it, as for any final response
        if stream:
            resp.close()
        seen = target
    raise RuntimeError(f"too many redirects (>{max_redirects})")
