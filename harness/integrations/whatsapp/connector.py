"""The WhatsApp (personal) connector: how it is described, launched and recorded.

It uses WhatsApp's linked-device protocol through an unofficial library, and WhatsApp can ban an
account it sees doing that. The descriptor's `risk_notice` says so in plain words, and pairing
waits for the person to acknowledge it. It was experimental until 2026-10-21; it now ships in the
desktop app, where the frozen sidecar runs its server and daemon as subcommands.

Connecting is pairing, which no form field can express: the Connectors page starts the daemon,
shows its QR code (or asks for the phone number and shows the code to type), and once the phone has
linked the device this module records the connection the way an MCP-served connector is recorded:
a profile in MCP mode, and a server entry the session plan fills in on every turn.
"""

from __future__ import annotations

import shutil
import sys
from typing import Any, Optional

NAME = "whatsapp_personal"

RISK_NOTICE = (
    "This links your own WhatsApp number as a device through an unofficial library, the way "
    "WhatsApp Web does, not through any API WhatsApp offers for personal accounts. WhatsApp can "
    "ban numbers it sees doing this, and such bans are often permanent. Link a number you could "
    "afford to lose. Your chats are kept on this computer and reach the model only when a tool "
    "reads them; every message the agent sends waits for your approval, and it writes only to "
    "people you already talk to."
)


def _frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def launch() -> Optional[dict[str, Any]]:
    """How a session's WhatsApp server starts. A packaged app's executable takes no `-m`, so there
    it is the sidecar's own `whatsapp` subcommand (packaging/server_entry.py)."""
    if _frozen():
        return {"command": sys.executable, "args": ["whatsapp"]}
    return {"command": sys.executable, "args": ["-m", "harness.integrations.whatsapp"]}


def daemon_command(parent_pid: int, nonce: str) -> list[str]:
    """`nonce` comes back in the daemon's `daemon.json`: how the server knows the daemon answering
    is the one it started. Not the pid: a venv's `python.exe` on Windows is a launcher that runs the
    interpreter as its child, so the pid the server holds is never the one the daemon writes."""
    entry = ["whatsapp"] if _frozen() else ["-m", "harness.integrations.whatsapp"]
    return [sys.executable, *entry, "daemon", "--parent", str(parent_pid), "--nonce", nonce]


def available() -> bool:
    """The library is an optional extra (`pip install .[whatsapp]`); the desktop build carries it."""
    try:
        import neonize  # noqa: F401
    except Exception:
        return False
    return True


def descriptor() -> Any:
    from harness.integrations.connectors.descriptors import ConnectorDescriptor

    return ConnectorDescriptor(
        name=NAME,
        title="WhatsApp (personal)",
        icon="◌",
        blurb="Read your own WhatsApp chats and reply to people you know, each message approved by you.",
        auth="pairing",
        two_way=False,
        brand_color="#25d366",
        logo="whatsapp",
        fields=[],
        instructions=[
            "Open WhatsApp on your phone: Settings ▸ Linked devices ▸ Link a device.",
            "Scan the QR code shown here, or choose to link with a phone number and type the code.",
            "Your recent chats arrive in the minutes after linking; the phone decides how far back.",
            "The device shows as \"Reeve\" in Linked devices, where you can remove it at any time.",
        ],
        available=available(),
        mcp_local=launch,
        mcp_approval_by_hints=True,
        risk_notice=RISK_NOTICE,
    )


def record_paired(secrets: Any, number: str) -> None:
    """The phone linked the device: the connector is connected, served by its local server."""
    from harness.extensions.mcp import config as mcp_config

    spec = launch()
    if spec is not None:
        mcp_config.put_global_server(NAME, {**spec, "requires_approval": True, "enabled": True})
    secrets.put(f"{NAME}:default", {"type": "none", "enabled": True, "mode": "mcp", "account": number})


def forget(folder: Any) -> None:
    """The account's folder: its session keys and every message kept. Unlinking removes both."""
    shutil.rmtree(folder, ignore_errors=True)
