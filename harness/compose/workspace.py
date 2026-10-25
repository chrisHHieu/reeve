"""The ground a session stands on: its workspace, directories, side stores and execution world."""

from __future__ import annotations

import logging
import posixpath
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from harness.config.roots import RootDir, normalize_roots
from harness.modes.agents import Agent
from harness.orchestration.worlds import LocalWorld, remote_world
from harness.tools.intents import IntentStore
from harness.tools.shell_env import ShellEnv, session_contributor
from harness.tools.spill import SpillStore
from harness.tools.undo import UndoStore

logger = logging.getLogger(__name__)


def resolve_workspace(
    workspace: Optional[str | Path], environment: Optional[dict[str, Any]], agent: Agent
) -> tuple[bool, str, Optional[Path]]:
    """`(remote, remote_root, ws)`.

    The SSH world (phase 2b) is selected by `environment`, and `workspace` is then the
    remote POSIX anchor. It never goes through local `Path` semantics: resolving a remote path on a
    Windows controller would invent `D:\\home\\developer\\…` and point instruction, config and memory
    reads at the wrong machine."""
    remote = environment is not None
    remote_root = ""
    if remote:
        remote_root = str(workspace or "").strip()
        if not remote_root.startswith("/"):
            raise ValueError("a remote workspace path must be absolute (start with /)")
        ws = None
    else:
        ws = Path(workspace).expanduser().resolve() if workspace else None
    if agent.requires_folder and ws is None and not remote:
        raise ValueError(f"agent '{agent.name}' requires a workspace")
    return remote, remote_root, ws


def _remote_roots(remote_root: str, granted: Optional[list]) -> list[dict[str, Any]]:
    """A remote session's roots are plain POSIX dicts, because `RemoteFileBackend` resolves them with
    posixpath and no local `RootDir` may touch them (it would resolve on the controller). Folders
    granted on the session (by hand, or by an allowed call that reached them), tagged `remote: true` when they landed, merge in after
    the workspace anchor. The backend and the boundary read the same shared list."""
    found = [{"path": remote_root, "writable": True, "label": "workspace"}]
    for entry in granted or []:
        if not isinstance(entry, dict):
            continue
        path = str(entry.get("path") or "").strip()
        if not path.startswith("/") or any(bad in path for bad in ("\0", "\n", "\r")):
            continue
        resolved = posixpath.normpath(path)
        if any(row["path"] == resolved for row in found):
            continue
        found.append(
            {
                "path": resolved,
                "writable": bool(entry.get("writable", False)),
                "label": str(entry.get("label") or "") or posixpath.basename(resolved) or resolved,
                **({"reached": True} if entry.get("reached") else {}),
            }
        )
    return found


def build_root_list(remote: bool, remote_root: str, roots: Optional[list], ws: Optional[Path]) -> list:
    """The session's directories as one shared, changeable list. It flows to the file tools, the
    permission engine and the context injector, so adding or removing a folder is seen by all.
    Explicit `roots` (an orphan session: scratch plus added folders) win, and otherwise the
    single workspace is the only writable root."""
    if remote:
        return _remote_roots(remote_root, roots)
    if roots:
        return normalize_roots(roots)
    if ws is not None:
        return [RootDir(path=ws, writable=True)]
    return []


def attach_memory_root(auto_memory: Optional[Any], remote: bool, root_list: list) -> Optional[Path]:
    """Add the auto-memory folder to the roots as a managed root.

    The ordinary file tools then read and write it, and the permission engine lets those writes run
    without an ask. It is for local sessions with a root only. A remote world's file tools resolve on
    the other host, and a rootless session would make the memory folder its primary root, so both
    keep the `auto_memory` tool. No directory about the person joins a session: what holds
    in every project is their own instructions file, which no tool writes."""
    if auto_memory is None or remote or not root_list:
        return None
    try:
        auto_memory.upgrade()  # the one-time move to this file format, with a backup
        memory_root = auto_memory.ensure().resolve()
    except OSError as exc:
        logger.warning("auto memory directory unavailable (%s); using the tool", exc)
        return None
    root_list.append(RootDir(path=memory_root, writable=True, label="memory", managed=True))
    return memory_root


@dataclass
class SideStores:
    """Local-only stores beside the roots. On a remote session they would write into a local
    spelling of a remote path (the controller's disk, under `D:\\home\\…` on Windows) and hand the
    model a locator that `read_file` resolves on the wrong machine, so they are absent there. A
    truncated remote command simply truncates, and undo and intents do not apply."""

    spill: Optional[SpillStore]
    plan_path: Optional[Path]
    undo: Optional[UndoStore]
    intents: Optional[IntentStore]


def open_side_stores(remote: bool, root_list: list, ws: Optional[Path], session_id: Optional[str]) -> SideStores:
    if remote:
        return SideStores(None, None, None, None)
    from harness.tools.plan_file import plan_path_for

    return SideStores(
        # Where an oversized tool output goes, so a truncation costs a read and not a re-run. It
        # lives inside a root on purpose: reading outside the session's roots falls out of every
        # session grant, so a spill parked elsewhere would raise an approval prompt on the way back
        # in, which is no recovery path (harness/tools/spill.py).
        spill=SpillStore.under(root_list),
        # The session's working plan file (`update_plan`), in the same managed `.reeve/` area the
        # spill store uses: inside a root, so the model can read it back with `read_file`, and out of
        # the person's tracked files. None on a rootless session, where the tool is not registered
        # and not registered to always refuse.
        plan_path=plan_path_for(root_list, session_id),
        # Before-images for every located write (tools/undo.py). Approval asks a person to predict
        # what an action will do, and undo asks them to recognise what it did, the only one of the
        # two that someone who does not write software can actually do. `base` is the relative-path
        # anchor and not where snapshots are stored: the file tools take `notes.txt` relative to the
        # workspace, so undo has to read it the same way.
        undo=UndoStore.under(root_list, base=ws),
        # Item 2.2: a durable note that an irreversible call started, so a restart asks and does
        # not silently repeat it (tools/intents.py). `resume()` re-sent a message in two of three
        # scenarios before this.
        intents=IntentStore.under(root_list),
    )


def open_world(
    *,
    remote: bool,
    remote_root: str,
    ws: Optional[Path],
    root_list: list,
    spill: Optional[SpillStore],
    session_id: Optional[str],
    environment: Optional[dict[str, Any]],
    environment_runner: Optional[Any],
    environment_password: Optional[str],
) -> tuple[Optional[Any], ShellEnv]:
    """The execution world, one object owning the session's fs tools, shell and terminals (worlds.py),
    and the managed shell variables.

    `ShellEnv` (tools/shell_env.py) is built first because the executor asks it per command. It is
    given `root_list` by reference, since that list is changed in place when the person grants a
    folder mid-session and a copy would publish forever the grants the session started with. The
    remote executor publishes its own `REEVE_*` contract from the remote HOME.

    A bound session builds the SSH world here, and its compatibility probe runs with `verify=True`,
    so an unreachable or unsupported host fails the session start with its typed error."""
    shell_env = ShellEnv()
    if not remote:
        shell_env.register(session_contributor(workspace=ws, roots=root_list, session_id=session_id or ""))
    if remote:
        world = remote_world(
            environment or {},
            remote_root,
            runner=environment_runner,
            session_id=session_id or "",
            verify=True,
            password=environment_password,
        )
    elif ws is not None:
        from harness.extensions.plugins import resolve_plugins

        plugin_bins = [str(path) for path in resolve_plugins(workspace=ws).bin_dirs]
        world = LocalWorld(root=ws, spill=spill, shell_env=shell_env, path_append=plugin_bins)
    else:
        world = None
    return world, shell_env


def make_edit_exempt_roots(root_list: list, ws: Optional[Path]):
    """A function giving the session's scratch folder when the work happens elsewhere. What is
    written there is not what a check judges (work_edits.is_work_edit). A session that works in its
    scratch folder, with no project folder, has none, so nothing is exempt for it."""
    import os

    def exempt() -> list[str]:
        return [
            str(root.path)
            for root in root_list
            if getattr(root, "label", "") == "scratch"
            and ws is not None
            and os.path.normcase(os.path.realpath(str(root.path))) != os.path.normcase(os.path.realpath(str(ws)))
        ]

    return exempt
