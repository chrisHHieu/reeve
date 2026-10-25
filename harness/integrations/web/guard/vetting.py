"""Checking one URL, and rewriting it so the connection goes to the address that was checked."""

from __future__ import annotations

import ipaddress
import socket
from typing import Optional
from urllib.parse import urlsplit, urlunsplit

from .ranges import blocked_reason

# A bound on a model-supplied URL. A longer address is not a page a person would paste, and every
# layer below (HTTP/2, proxies, logs) handles a bounded request line better.
MAX_URL_LENGTH = 2048


def vet(url: str) -> tuple[Optional[str], Optional[str]]:
    """`(refusal reason, address to pin the connection to)` for a URL.

    The reason is None when the URL may be fetched. The address is None for a literal-IP URL, which
    already names the connection target, and otherwise it is the first resolved answer. Pinning to it
    is sound because a refusal is returned when any answer lands in a blocked range, so a name with
    both a public and a private A record cannot slip through."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        return "url must start with http:// or https://", None
    if len(url) > MAX_URL_LENGTH:
        return f"url exceeds the maximum length of {MAX_URL_LENGTH}", None
    if parts.username or parts.password:
        # The model has no credentials to send, and a user:pass@ address is a request to leak
        # whatever a redirect or a proxy would forward.
        return "credentials in URLs are not allowed", None
    host = parts.hostname
    if not host:
        return "url has no host", None

    # A literal address needs no lookup.
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        reason = blocked_reason(literal)
        return (f"refusing to fetch {host}: {reason}" if reason else None), None

    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        answers = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        return f"could not resolve {host}: {exc}", None

    pin: Optional[str] = None
    for answer in answers:
        raw = answer[4][0]
        try:
            ip = ipaddress.ip_address(raw)
        except ValueError:
            continue
        # ::ffff:127.0.0.1 and its kind must be judged as the v4 address they carry.
        mapped = getattr(ip, "ipv4_mapped", None)
        if mapped is not None:
            ip = mapped
        reason = blocked_reason(ip)
        if reason:
            return f"refusing to fetch {host} ({ip}): {reason}", None
        if pin is None:
            pin = raw
    return None, pin


def check_url(url: str) -> Optional[str]:
    """None if the URL may be fetched, else a refusal reason a person can read.

    The host is resolved and rejected when any answer lands in a blocked range, so a name with both
    a public and a private A record cannot be used to slip through."""
    return vet(url)[0]


def pinned(url: str, ip: str) -> tuple[str, dict, dict]:
    """Rewrite `url` so the client connects to `ip` while presenting the original name.

    It returns `(request_url, headers, extensions)`. The URL carries the vetted address, so the
    client never resolves the name itself. `Host` carries the name, and any explicit port, for
    virtual hosting. `sni_hostname` keeps the TLS handshake, certificate verification included,
    against the name and not the address."""
    parts = urlsplit(url)
    host = parts.hostname
    address = f"[{ip}]" if ":" in ip else ip
    userinfo, _, _ = parts.netloc.rpartition("@")
    netloc = (f"{userinfo}@" if userinfo else "") + address
    host_header = host
    if parts.port is not None:
        netloc += f":{parts.port}"
        host_header += f":{parts.port}"
    request_url = urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
    extensions = {"sni_hostname": host} if parts.scheme == "https" else {}
    return request_url, {"Host": host_header}, extensions
