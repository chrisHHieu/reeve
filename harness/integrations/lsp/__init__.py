"""Language Server Protocol integration — `lsp/` is the client, servers, manager, tool.

The package is deliberately stdlib-only (`subprocess` + a background reader thread), the
same rule the rest of the harness follows: a language server is an EXTERNAL program the
user installs, so the transport must never cost a dependency of its own.
"""

from __future__ import annotations

from .client import LSPClient, LSPError
from .manager import LSPManager, default_manager
from .servers import ServerNotFound, resolve_server, supported_extensions
from .tool import lsp_tools

__all__ = [
    "LSPClient",
    "LSPError",
    "LSPManager",
    "ServerNotFound",
    "default_manager",
    "lsp_tools",
    "resolve_server",
    "supported_extensions",
]
