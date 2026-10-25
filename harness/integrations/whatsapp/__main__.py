"""`python -m harness.integrations.whatsapp` runs a session's MCP server;
`python -m harness.integrations.whatsapp daemon --parent <pid>` runs the connection."""

import sys

if sys.argv[1:2] == ["daemon"]:
    from .daemon import main

    raise SystemExit(main(sys.argv[2:]))

from .server import main  # noqa: E402

raise SystemExit(main())
