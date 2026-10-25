"""Blocks a force-push (test fixture): exit 2 is Claude Code's "block"."""
import json
import sys

payload = json.load(sys.stdin)
if "push --force" in str(payload.get("tool_input", {}).get("command", "")):
    print("force-push is not allowed on release branches", file=sys.stderr)
    sys.exit(2)
