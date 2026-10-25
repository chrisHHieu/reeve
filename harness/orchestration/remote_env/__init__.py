"""The SSH remote execution world.

``RemoteSession`` speaks the transport (argv posture, sentinel framing, typed failures);
``RemoteExecutor`` implements the ``Executor`` protocol; ``RemoteWorld`` implements the
ADR-0003 ``World`` protocol with remote file closures and a terminal surface, and
``directories.py`` is the environment picker's one-round-trip directory listing. Construct
a world with ``harness.orchestration.worlds.remote_world(profile, workspace)``; phase 2b-1 wires it into
sessions (the engine build selects it for a session with an ``environment_id``) and routes
the read/search/git/observed tools through ``RemoteWorld.read_backend()``. The persistent
remote PTY remains a stated omission (phase 2a).
"""

from .directories import (
    MAX_DIRECTORY_ENTRIES,
    canonical_remote_directory,
    list_remote_directories,
    parse_directory_listing,
)
from .executor import RemoteExecutor
from .files import RemoteFileBackend, remote_file_tools
from .transport import (
    DEFAULT_COMMAND_TIMEOUT_SECONDS,
    OP_MARKER_PREFIX,
    RUN_SENTINEL_PREFIX,
    Frame,
    RemoteAuthError,
    RemoteCommandError,
    RemoteCommandResult,
    RemoteConnectionError,
    RemoteEnvError,
    RemoteHostKeyError,
    RemoteProtocolError,
    RemoteSession,
    RemoteSshMissingError,
    RemoteTimeoutError,
    RemoteUnsupportedError,
    frame_command,
    op_script,
    parse_frame,
)
from .world import RemoteTerminal, RemoteTerminals, RemoteWorld

__all__ = [
    "DEFAULT_COMMAND_TIMEOUT_SECONDS",
    "Frame",
    "MAX_DIRECTORY_ENTRIES",
    "OP_MARKER_PREFIX",
    "RUN_SENTINEL_PREFIX",
    "RemoteAuthError",
    "RemoteCommandError",
    "RemoteCommandResult",
    "RemoteConnectionError",
    "RemoteEnvError",
    "RemoteExecutor",
    "RemoteFileBackend",
    "RemoteHostKeyError",
    "RemoteProtocolError",
    "RemoteSession",
    "RemoteSshMissingError",
    "RemoteTerminal",
    "RemoteTerminals",
    "RemoteTimeoutError",
    "RemoteUnsupportedError",
    "RemoteWorld",
    "canonical_remote_directory",
    "frame_command",
    "list_remote_directories",
    "op_script",
    "parse_directory_listing",
    "parse_frame",
    "remote_file_tools",
]
