"""Allowed network destinations."""

from __future__ import annotations

from urllib.parse import urlsplit


def host_of(url_or_domain: str) -> str:
    """The lowercase host of a URL, or a bare domain as it is; "" when nothing is usable. It takes
    `https://docs.python.org/x` and `docs.python.org` alike."""
    text = (url_or_domain or "").strip().lower()
    if not text:
        return ""
    if "://" in text:
        return urlsplit(text).hostname or ""
    return urlsplit("//" + text).hostname or text


def session_domain(url_or_domain: str) -> str:
    """The host to remember for "always allow this domain". A leading `www.` is dropped, since
    `bbc.com` and `www.bbc.com` are one site to anyone, and the suffix match already treats the
    second as a subdomain of the first. Spelling only: never the registrable domain or anything
    broader, which would widen the grant without saying so."""
    host = host_of(url_or_domain)
    return host[4:] if host.startswith("www.") else host


def domain_allowed(url: str, configured: list[str], granted: set[str]) -> bool:
    """True when the URL's host is an allowed destination: an exact match, or a subdomain of one
    (`docs.python.org` matches `python.org`; `evil-python.org` never does). Domains granted during
    the session count, since that is consent given once."""
    host = host_of(url)
    if not host:
        return False
    allowed = {domain for domain in (host_of(entry) for entry in configured) if domain} | granted
    return any(host == domain or host.endswith("." + domain) for domain in allowed)
