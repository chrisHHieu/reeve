# -*- mode: python ; coding: utf-8 -*-
"""Entry for the frozen `reeve-cua` sidecar: the desktop-control MCP server.

Built only on Windows (see reeve-server.spec, `CUA`). Kept separate from
server_entry.py so the main server never needs the desktop stack importable to boot,
and so cua_server runs as a *module* here — its sys.path self-defense exists only for
"python cua_server.py" launches, where the script's own directory would shadow the pip
`mcp` SDK with Reeve's `harness/extensions/mcp` package.
"""

from harness.integrations.cua_server import mcp

if __name__ == "__main__":
    # stdout is the MCP stdio channel — keep it pristine.
    mcp.run()
