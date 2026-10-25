"""The HTTP and WebSocket API the desktop app talks to: `create_app` over a `SessionManager`."""

from .app import create_app
from .manager import SessionManager

__all__ = ("SessionManager", "create_app")
