"""The connector registry: look descriptors up, add one, and work out the MCP endpoint in use.

The model (`Field`, `ConnectorDescriptor`, `ValidationResult`, `MCPVariant`) and `client_env` are
re-exported here because this is where the rest of the code, and the experimental package, imports
them from.
"""

from __future__ import annotations

from typing import Optional

from .descriptor.model import ConnectorDescriptor, Field, MCPVariant, ValidationResult
from .descriptor.oauth_clients import client_env
from .descriptor_data import DESCRIPTORS

__all__ = [
    "ConnectorDescriptor",
    "DESCRIPTORS",
    "Field",
    "MCPVariant",
    "ValidationResult",
    "client_env",
    "get_descriptor",
    "list_descriptors",
    "mcp_url_for",
    "mcp_variant_for",
    "register_descriptor",
]

_BY_NAME = {descriptor.name: descriptor for descriptor in DESCRIPTORS}


def register_descriptor(descriptor: ConnectorDescriptor) -> None:
    """One more connector, for the experimental package and for tests; the name finds it at once."""
    _BY_NAME[descriptor.name] = descriptor
    DESCRIPTORS.append(descriptor)


# Experimental connectors are a package of their own so that release builds can leave the code out
# entirely (`packaging/reeve-server.spec`). Without the package this does nothing.
def _experimental() -> list[ConnectorDescriptor]:
    try:
        from .experimental import EXPERIMENTAL_DESCRIPTORS
    except ImportError:  # a release build, which leaves the package out
        return []
    return EXPERIMENTAL_DESCRIPTORS


for _descriptor in _experimental():
    _descriptor.experimental = True  # set here, not taken on the author's word
    register_descriptor(_descriptor)


def list_descriptors() -> list[ConnectorDescriptor]:
    """Every connector, in catalog order; a copy, so changing it changes nothing."""
    return [*DESCRIPTORS]


def get_descriptor(name: str) -> Optional[ConnectorDescriptor]:
    return _BY_NAME.get(name) if name else None


def mcp_variant_for(descriptor: ConnectorDescriptor, profile: Optional[dict]) -> str:
    """The endpoint this connection chose, or the default. Never an unknown key: a stored value
    that no longer exists (a variant the vendor retired, a profile edited by hand) falls back
    instead of producing an empty URL and a connector that fails to connect with nothing on screen
    to say why."""
    if not descriptor.mcp_variants:
        return ""
    keys = {variant.key for variant in descriptor.mcp_variants}
    chosen = str((profile or {}).get("mcp_variant") or "")
    if chosen in keys:
        return chosen
    if descriptor.mcp_variant_default in keys:
        return descriptor.mcp_variant_default
    return descriptor.mcp_variants[0].key


def mcp_url_for(descriptor: ConnectorDescriptor, profile: Optional[dict] = None) -> str:
    """The MCP endpoint a connection uses: the one answer for every caller.

    Whatever seeds a server entry, and the per-session plan that writes the URL back onto it, goes
    through here. The plan overwrites `server.url` with the descriptor's on every session build, so
    that an endpoint the vendor moves reaches installs that connected before the move. That is why
    the chosen variant lives in the profile and not in `mcp.json`: a URL written into `mcp.json`
    would be erased on the next turn, while a variant key in the profile survives it and still
    lets the descriptor decide what URL the key means.
    """
    key = mcp_variant_for(descriptor, profile)
    for variant in descriptor.mcp_variants:
        if key and variant.key == key:
            return variant.url
    return descriptor.mcp_url
