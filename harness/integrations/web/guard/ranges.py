"""Which addresses a model-chosen URL may not reach."""

from __future__ import annotations

import ipaddress
from typing import Optional

# RFC 6598 shared address space. Python's `is_private` misses it, but it is carrier-grade NAT space
# and Tailscale hands out internal hosts there (100.64.0.0/10). A fetch to it is the same class of
# "reach the machine's network position" as RFC 1918.
_CGNAT = ipaddress.ip_network("100.64.0.0/10")


def blocked_reason(ip: ipaddress._BaseAddress) -> Optional[str]:
    """Why an address is off limits, or None when it is an ordinary public one."""
    if ip.is_loopback:
        return "loopback"
    if ip.is_link_local:
        return "link-local (includes the cloud metadata endpoint)"
    if ip.is_private:
        return "a private network"
    if ip.version == 4 and ip in _CGNAT:
        return "shared address space (CGNAT / RFC 6598)"
    if ip.is_multicast:
        return "multicast"
    if ip.is_reserved or ip.is_unspecified:
        return "a reserved range"
    return None
