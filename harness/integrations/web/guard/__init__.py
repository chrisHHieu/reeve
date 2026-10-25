"""An address guard for URLs the model chooses.

`web_fetch` and `browser_open_url` take a URL straight from the model, and the model's input is
untrusted by design. It reads web pages, email and Slack messages, all of which are documented as
"data, not instructions". A page that talks the agent into fetching `http://169.254.169.254/` or
`http://127.0.0.1:11434/` turns a read-only research tool into a probe of the machine's own network
position, and `web_fetch` has `requires_approval=False`, so no prompt ever appears.

This blocks the ranges that are reachable only because Reeve runs on the person's machine: loopback,
RFC 1918 and other private space, link-local (which covers the cloud metadata endpoint at
169.254.169.254), and the reserved and multicast blocks (`ranges.py`).

Every hop is checked and not just the first. With `follow_redirects=True` a public URL could 302
straight to loopback, which is the standard way this filter is bypassed. The URL itself is bounded
too: more than 2048 characters, and embedded credentials (`user:pass@host`), are refused before any
request (`vetting.py`).

DNS rebinding is closed by pinning at the connection level. `get_checked` rewrites each hop so the
client connects to the exact address that passed the check, with the name in `Host` and SNI so
virtual hosting and certificate verification still see the name. A record with a near-zero TTL that
flips to 127.0.0.1 between the check and the connect therefore changes nothing, because the client
never resolves the name itself. `get_checked(stream=True)` keeps that pinning while delivering the
body unbuffered, so callers can enforce their own byte caps. `check_url` on its own, used as
`browser_open_url`'s pre-check, still carries the resolve-twice gap, because the browser owns its
connections and cannot be pinned from here.
"""

from __future__ import annotations

from . import redirects
from .redirects import MAX_REDIRECTS, get_checked, same_site
from .vetting import MAX_URL_LENGTH, check_url

__all__ = ["MAX_REDIRECTS", "MAX_URL_LENGTH", "check_url", "get_checked", "same_site"]
