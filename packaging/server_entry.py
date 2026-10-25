"""The script PyInstaller freezes into the desktop app's sidecar.

`reeve-server` exists only as package metadata, and PyInstaller needs a real file to start from;
this one calls the same `main()`. A frozen executable cannot be run with `python -m`, so the
WhatsApp connector's helpers are reached through it as subcommands.
"""

import sys


def _whatsapp(args: list[str]) -> int:
    """`reeve-server whatsapp` runs the personal WhatsApp connector's session server, and
    `reeve-server whatsapp daemon …` its daemon."""
    if args[:1] != ["daemon"]:
        from harness.integrations.whatsapp.server import main as server_main

        return server_main()
    from harness.integrations.whatsapp.daemon import main as daemon_main

    return daemon_main(args[1:])


if __name__ == "__main__":
    if sys.argv[1:2] == ["whatsapp"]:
        raise SystemExit(_whatsapp(sys.argv[2:]))
    from harness.interface.server.run import main

    main()
