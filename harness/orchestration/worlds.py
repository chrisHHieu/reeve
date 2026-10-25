"""The execution world — the ONE object owning what the agent's mutable work runs
against (D5, the fs/shell capability seam).

Each capability splits into a Service Definition, interchangeable Service Providers,
and Consumers (`docs/capability-seams.md`), and keeps ONE "execution world" per session:
filesystem and process operations draw from the same world, so pointing the world at a
remote sandbox moves Bash, terminals, and file edits together with no provider forks
(a remote provider needs no fork of the local one).

reeve's seam is this module. A `World` answers three questions a tool may ask:
what file operations exist (`fs_tools` — the model-facing write/edit/read closures),
what runs commands (`executor`), and what holds persistent terminals (`terminals`).
`LocalWorld` is the shipped provider: it composes the implementations that have always
been here (aisuite's FileToolkit for version-guarded writes and patches, `LocalExecutor`
for the shell, `TerminalSessions`) — delegation, not rewrite, so behavior is
byte-identical and the toolkit's stale-write guards keep working under the same roots.

The consumer rule (what makes the seam real): tools, the catalog and the
engine ask their World — they never import a disk or process primitive themselves. A
future remote/container world implements this Protocol, and the catalog composes it
unchanged. Two contracts travel with the swap:

- **One world per session.** `executor` and `terminals` come from the same World as
  `fs_tools` — a shell whose `$REEVE_SCRATCH` disagrees with the file tools is two
  worlds wearing one name, and undo/spill/permissions would see a history neither
  world fully owns.
- **Policy stays outside.** The World answers "how do I touch things", never "may I".
  Reach lives in `boundary.py`, consent in `permissions.py`; a World is consulted only
  after both said yes.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional, Protocol, runtime_checkable

import aisuite as ai

from harness.tools.shell import Executor, LocalExecutor
from harness.tools.terminal import TerminalSessions

if TYPE_CHECKING:
    # Imported for the factory's annotation only: `remote_env` is a heavy, optional
    # module and the real import stays inside `remote_world()` (below).
    from .remote_env import RemoteWorld


@runtime_checkable
class World(Protocol):
    """What any execution world must provide. Paths are plain strings anchored at
    `root` in v1 — the honest concession is recorded here: paths and versions are branded
    tokens opaque so remote backends can use URIs; reeve's tools still manipulate
    paths as strings everywhere, so the seam is drawn at the OPERATION level (no tool
    touches a disk or process primitive directly) and the token work waits until a
    second world actually exists to name its own shapes."""

    #: The session's primary anchor — the relative-path base for `fs_tools` and the
    #: working directory of the shell. Present even where the concrete backend would
    #: call it something else (a workspace URI).
    root: Path

    def fs_tools(self, roots: Optional[list] = None) -> list:
        """The model-facing file tool closures (write_file, apply_patch, …), backed by
        this world's storage. `roots` (a shared, possibly-mutable RootDir list) extends
        the world's reach exactly as the catalog passes it today."""
        ...

    @property
    def executor(self) -> Optional[Executor]:
        """The shell runner (one process per command), or None where the world has no
        shell."""
        ...

    @property
    def terminals(self) -> Optional[Any]:
        """Persistent terminal sessions, or None where the world has no terminal."""
        ...


class LocalWorld:
    """The world made of this machine: aisuite's version-guarded file toolkit, the
    persistent local shell, and local terminals — the exact objects the agent builder
    assembled by hand before the seam existed, now composed in one place."""

    def __init__(
        self,
        root: str | Path,
        *,
        spill: Optional[Any] = None,
        shell_env: Optional[Any] = None,
        executor: Optional[Executor] = None,
        terminals: Optional[Any] = None,
        path_append: Optional[list[str]] = None,
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        #: Folders appended to the shell's PATH (enabled plugins' `bin/`).
        self._path_append = list(path_append or [])
        self._executor = executor
        self._terminals = terminals
        # Built on demand, matching today's construction order: the executor asks the
        # shell_env per command, and both are None where the session has no workspace.
        self._spill = spill
        self._shell_env = shell_env

    @property
    def executor(self) -> Optional[Executor]:
        if self._executor is None:
            self._executor = LocalExecutor(
                cwd=self.root,
                spill=self._spill,
                shell_env=self._shell_env,
                path_append=self._path_append,
            )
        return self._executor

    @property
    def spill(self) -> Optional[Any]:
        """The session's spill store (None when the world was built without one). Read by
        the prune tier's sink so a large locator-less tool result keeps a reading path."""
        return self._spill

    @property
    def terminals(self) -> Optional[Any]:
        if self._terminals is None:
            # The SAME managed environment run_shell asks: a terminal where
            # `$REEVE_SCRATCH` is missing would be a second shell with different
            # facts in it (agent.py kept these from splitting for the same reason).
            self._terminals = TerminalSessions(
                cwd=self.root,
                env_provider=(
                    self._shell_env.resolve if self._shell_env is not None else None
                ),
            )
        return self._terminals

    def close_terminals(self) -> None:
        """Dispose this world's terminals with their owner. Only if one was ever built —
        reading the property here would create a manager just to shut it down."""
        if self._terminals is not None:
            self._terminals.close_all()

    def fs_tools(self, roots: Optional[list] = None) -> list:
        """The aisuite file toolkit over this world's storage — the same call, with the
        same kwargs, the catalog made inline before the seam: `roots` (the session's
        shared, mutable list) when multi-root, single-root+write otherwise. Delegation
        keeps the toolkit's stale-write guards and patch machinery exactly as they are."""
        kwargs = (
            {"roots": roots} if roots else {"root": str(self.root), "allow_write": True}
        )
        primary = _primary_path(roots, self.root)
        made = primary is not None and not primary.exists()
        tools = list(ai.toolkits.files(**kwargs))
        if made:
            # The toolkit makes its primary folder when it is built; it only resolves paths
            # against it. A session's scratch folder is named at build and made by its first turn
            # (`TurnEntryMixin._make_scratch`), so the folder made here goes back while empty:
            # made here, every conversation opened and never sent left one behind (2026-10-18).
            try:
                primary.rmdir()
            except OSError:
                pass
        return tools


# -- the construction seam -------------------------------------------------------------
# `agent.build_engine` selects the world: a session bound to a remote profile constructs
# `remote_world(profile, workspace, verify=True)`; an unbound one constructs LocalWorld
# exactly as before. `world_for` stays the local defaulting helper (byte-identical
# behavior for callers that predate the binding), and `remote_world` is the explicit SSH
# constructor. GUI enable and the persistent remote PTY are phase 2b-2.


def world_for(
    workspace: Optional[str | Path],
    *,
    spill: Optional[Any] = None,
    shell_env: Optional[Any] = None,
    executor: Optional[Executor] = None,
    terminals: Optional[Any] = None,
) -> Optional[LocalWorld]:
    """Build the LOCAL world (or None with no workspace) — the default for every caller
    that predates session binding, byte-identical to the object agent.py used to build
    inline. A bound session is constructed explicitly with ``remote_world`` by
    ``agent.build_engine`` (phase 2b-1)."""
    if workspace is None:
        return None
    return LocalWorld(
        root=workspace,
        spill=spill,
        shell_env=shell_env,
        executor=executor,
        terminals=terminals,
    )


def remote_world(
    profile: dict[str, Any],
    workspace: str | Path,
    *,
    runner: Optional[Any] = None,
    session_id: str = "",
    verify: bool = True,
    spill: Optional[Any] = None,
    known_hosts_file: Optional[str] = None,
    connect_timeout: Optional[float] = None,
    password: Optional[str] = None,
) -> RemoteWorld:
    """Construct a ``RemoteWorld`` for one SSH profile and one remote workspace.

    ``verify=True`` runs phase 1's compatibility probe before the world exists: a real
    remote session must not start against a host whose platform/arch/HOME is unknown,
    and the probe's typed failures (auth, host key, timeout, ssh missing, unsupported)
    surface before any tool runs. Tests may pass ``verify=False`` with a fake runner.

    ``password`` (transient or the profile's remembered secret, resolved by the caller)
    switches every ssh call to the askpass handshake — one 0600 helper per call, removed
    in a ``finally``; it never rides argv and is never written to the profile.
    """
    from .remote_env import RemoteSession, RemoteWorld
    from .remote_environments import DEFAULT_CONNECT_TIMEOUT_SECONDS

    session = RemoteSession(
        profile,
        runner=runner,
        known_hosts_file=known_hosts_file,
        connect_timeout=(
            connect_timeout
            if connect_timeout is not None
            else DEFAULT_CONNECT_TIMEOUT_SECONDS
        ),
        password=password,
    )
    if verify:
        session.inspect()
    return RemoteWorld(session, str(workspace), spill=spill, session_id=session_id)


def _primary_path(roots: Optional[list], root: Any) -> Optional[Path]:
    """The folder the file toolkit makes when it is built: the first root, else the world's."""
    first = (roots or [None])[0]
    raw = first.get("path") if isinstance(first, dict) else getattr(first, "path", first)
    raw = raw if raw is not None else root
    try:
        return Path(str(raw)).expanduser().resolve() if raw else None
    except (OSError, RuntimeError):
        return None
