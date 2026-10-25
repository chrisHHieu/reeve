#!/usr/bin/env bash
# First-time setup of a checkout on macOS or Linux: the Python venv at .venv that running from
# source expects. The browser dev flow starts reeve-server from it, and the Electron shell starts
# it in a dev run (surfaces/gui/electron/paths.ts, serverBin).
#
#   bash packaging/setup_dev_env.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$ROOT/.venv"

python3 -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip
# The harness (engine, server, connectors) with the messaging extras and the dev tools. aisuite
# is an ordinary dependency, pinned to a git commit in pyproject.toml until its next release.
"$VENV/bin/pip" install --quiet -e "$ROOT[messaging,dev]"
# Stop here, loudly, if the install did not give an importable package.
"$VENV/bin/python" -c 'import aisuite, harness'

echo "Ready: $VENV"
echo "  server: $VENV/bin/reeve-server --cwd /path/to/your/project --port 8765"
