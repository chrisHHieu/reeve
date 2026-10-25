"""RevealMixin — show a workspace file in the OS file manager.

"Reveal in Explorer" for the tree and the recorded-change menu. Local and
non-executing: no shell, no file contents, one window on the user's desktop. The path is
confined to the session's workspace the same way a diff read is (`ReviewsMixin._change_root`
plus the escape guard), because a surface must not be able to name any path on the disk.

Cross-platform: `explorer /select,` on Windows (its exit code is not meaningful, so it is
never waited on), `open -R` on macOS, the enclosing folder via `xdg-open` elsewhere.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional

import sys

from harness.platform.spawn import spawn


class RevealMixin:
    def _reveal_command(self, absolute: str) -> Optional[list[str]]:
        """The platform's "show me this file" invocation, or None where there is none."""
        if sys.platform == "win32":
            return ["explorer", "/select," + absolute]
        if sys.platform == "darwin":
            return ["open", "-R", absolute]
        if shutil.which("xdg-open"):
            return ["xdg-open", str(Path(absolute).parent)]
        return None

    def reveal_path(self, session_id: str, path: str) -> dict[str, Any]:
        """Open the OS file manager at a workspace file; refuse anything outside it."""
        root = self._change_root(session_id)
        rel = (path or "").strip()
        if root is None or not rel:
            return {"ok": False, "error": "no file"}
        candidate = Path(rel)
        if candidate.is_absolute():
            absolute = candidate
        else:
            if rel[:1] in ("/", "\\") or re.match(r"^[A-Za-z]:", rel) or ".." in candidate.parts:
                return {"ok": False, "error": "invalid path"}
            absolute = root / candidate
        try:
            absolute = absolute.resolve()
            inside = absolute.is_relative_to(root.resolve())
        except (OSError, ValueError):
            return {"ok": False, "error": "invalid path"}
        if not inside or not absolute.exists():
            return {"ok": False, "error": "not in this workspace"}
        command = self._reveal_command(str(absolute))
        if command is None:
            return {"ok": False, "error": "no file manager on this system"}
        try:
            spawn(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as exc:
            return {"ok": False, "error": f"could not open it: {exc}"}
        return {"ok": True, "path": str(absolute)}
