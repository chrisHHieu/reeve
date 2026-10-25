"""MCPMixin: the MCP side of the session manager, in five parts.

- `mcp_plan`: which servers a session is entitled to, and the signature of its tool set
- `mcp_arming`: keeping the live tools in line with that plan at each turn boundary
- `mcp_servers`: the server list, its configuration and connecting one server
- `mcp_connect`: the one-click sign-in flows of connectors that sit on a vendor's server
- `mcp_browser`: the backend of the built-in browser server
"""

from __future__ import annotations

from .mcp_arming import McpArmingMixin
from .mcp_browser import McpBrowserMixin
from .mcp_connect import McpConnectMixin
from .mcp_plan import McpPlanMixin
from .mcp_servers import McpServersMixin


class MCPMixin(McpPlanMixin, McpArmingMixin, McpServersMixin, McpConnectMixin, McpBrowserMixin):
    pass
