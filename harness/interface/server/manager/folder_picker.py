"""FolderPickerMixin: the operating system's folder dialog, opened from the sidecar."""

from __future__ import annotations

import subprocess
import sys
from typing import Any

from harness.platform.spawn import run as spawn_run

_PROMPT = "Give Reeve access to a folder"

# A WinForms folder dialog through PowerShell needs no extra dependency. `-STA` is required: in
# the default MTA apartment the dialog silently fails.
_WINDOWS_SCRIPT = (
    "Add-Type -AssemblyName System.Windows.Forms; "
    "$f = New-Object System.Windows.Forms.FolderBrowserDialog; "
    f"$f.Description = '{_PROMPT}'; "
    "if ($f.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) "
    "{ [Console]::Out.Write($f.SelectedPath) }"
)


def _picker_command() -> list[str]:
    if sys.platform == "darwin":
        return [
            "osascript",
            "-e",
            'tell application "System Events" to activate',
            "-e",
            f'POSIX path of (choose folder with prompt "{_PROMPT}")',
        ]
    if sys.platform == "win32":
        return ["powershell.exe", "-NoProfile", "-STA", "-Command", _WINDOWS_SCRIPT]
    # Elsewhere zenity when it is present; otherwise the GUI's paste-a-path input remains.
    return ["zenity", "--file-selection", "--directory"]


class FolderPickerMixin:
    def pick_native_folder(self) -> dict[str, Any]:
        """Open the OS folder picker FROM THE SIDECAR. A browser GUI cannot get absolute paths
        from a web file dialog, but the sidecar is local and can (the desktop shell uses Tauri's
        own picker instead). It blocks until the person picks or cancels, so callers run it off
        the loop."""
        try:
            out = spawn_run(_picker_command(), capture_output=True, text=True, timeout=300)
        except (OSError, subprocess.TimeoutExpired):
            return {"ok": False, "error": "no native folder picker available"}
        path = (out.stdout or "").strip()
        if out.returncode != 0 or not path:
            return {"ok": False, "canceled": True}
        return {"ok": True, "path": path}
