# -*- mode: python ; coding: utf-8 -*-
"""Entry for the frozen `reeve-code-child` sidecar: the code-mode child protocol.

`run_code` executes model-written programs in a separate process whose interpreter must
NOT be assumed to be `sys.executable`: inside the packaged app that path is
`reeve-server.exe`, whose argparse rejects `-u -m harness.tools.code_child` and dies
before the program's first line (measured in production — session 3c33bb70's stderr
tail). This sibling exe, built over the same COLLECT as the server, is what
`CodeProgram.child_command()` targets when frozen; the JSON-lines protocol on
stdin/stdout is identical to the `python -m` dev path.
"""

from harness.tools.code_child import main

if __name__ == "__main__":
    # stdin is the protocol channel, stdout the reply side — keep both pristine.
    main()
