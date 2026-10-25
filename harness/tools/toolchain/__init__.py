"""Finding, and optionally installing, the command-line tools the agent's skills drive.

Two problems that are kept apart on purpose:

- The user's own toolchain (aws, kubectl, terraform, gh, node) is only located (`locate.py`). The
  point is their installed, configured, credentialed copy.
- Tools a skill fundamentally is, such as the scanners behind the security bundles, can be installed
  and pinned (`catalog.py`, `install.py`).
"""

from . import catalog, locate, paths
from .catalog import MANAGED, Download, ManagedTool
from .install import install
from .locate import describe, have, installable, missing, resolve
from .paths import bin_dir, managed_dir, platform_key

__all__ = [
    "MANAGED",
    "Download",
    "ManagedTool",
    "bin_dir",
    "catalog",
    "describe",
    "have",
    "install",
    "installable",
    "locate",
    "managed_dir",
    "missing",
    "paths",
    "platform_key",
    "resolve",
]
