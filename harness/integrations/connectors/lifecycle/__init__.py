"""Connecting, listing and disconnecting connectors."""

from ..catalog import patch_tool_settings
from ..descriptors import get_descriptor
from .connect import connect_connector
from .disconnect import disconnect_connector
from .experimental import experimental_enabled, set_experimental_enabled
from .listing import connector_list

__all__ = [
    "connect_connector",
    "connector_list",
    "disconnect_connector",
    "experimental_enabled",
    "set_experimental_enabled",
    "update_connector_tools",
]


def update_connector_tools(secrets, name: str, enabled: dict) -> dict:
    """Switch a connector's tools on or off."""
    if get_descriptor(name) is None:
        return {"ok": False, "error": "unknown connector"}
    return patch_tool_settings(secrets, name, enabled)
