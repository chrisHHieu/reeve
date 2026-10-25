"""The tools' door into a memory directory.

A memory directory is a writable root: the model saves a memory with `write_file`, edits one
with `replace_in_file`, removes one with `apply_patch`. Nothing stood between those tools and the
directory, so whatever was written was a memory.

This is an `execute` listener and not a `check`, because what has to be judged is the file that
results, and three tools produce it three ways (a whole file, a replacement inside one, a patch
envelope). So the call runs, the file it left is read and judged (`memory.admission`), and a
file that is not admitted is put back as it was: the model gets a refusal naming the rule and
what to add, and the directory holds nothing it did not hold before.

A call that cannot say where it writes — a shell command, a `run_code` program, a connector's
tool — is judged by the directory itself: what the call changed in it is found by
comparing the directory before and after. It used to pass untouched, so a `cat > note.md` in a
session with full access wrote a memory no gate had seen (15 shell writes into memory
directories in the ten days before the gate). Only a call the engine runs alone is compared
(`alone`), so the change found is that call's; a change another door already judged is left as
it is (`memory.admission.settled`). A background job that writes after its call returned is not
seen.

It fails open on its own errors, unlike a permission gate: the write was already allowed, and a
crash here must not cost a call or leave a half-restored file.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

from harness.context.memory.admission import Admission, settled
from harness.context.memory.auto_memory import keep_version
from harness.context.memory.gate import PROGRESS_NAME, Refusal, progress_advice
from .pipeline import Block, _blocked_result

logger = logging.getLogger(__name__)


def _contents(root: Path, known: dict[Path, tuple[tuple[int, int], bytes]]) -> tuple[dict[Path, bytes], set[Path]]:
    """Every file and folder under `root` but the harness's own (a name starting with `.`:
    the version history, the evidence record, locks and temporary files).

    `known` keeps what was read, by modification time and size: the largest real directory
    (252 notes) took 17 ms to read whole and 0.6 ms to list, so a call pays for a listing and
    for the files that changed."""
    files: dict[Path, bytes] = {}
    folders: set[Path] = set()
    if not root.is_dir():
        return files, folders
    pending = [root]
    while pending:
        with os.scandir(pending.pop()) as entries:
            for entry in entries:
                if entry.name.startswith("."):
                    continue
                path = Path(entry.path)
                if entry.is_dir():
                    folders.add(path)
                    pending.append(path)
                elif entry.is_file():
                    stat = entry.stat()
                    key = (stat.st_mtime_ns, stat.st_size)
                    hit = known.get(path)
                    if hit is None or hit[0] != key:
                        hit = (key, path.read_bytes())
                        known[path] = hit
                    files[path] = hit[1]
    return files, folders


def _advised(result: Any, changed: Sequence[tuple[Path, Any]]) -> Any:
    """`result` with what a saved progress log near its cap is told (`gate.progress_advice`)."""
    notes = [
        progress_advice(path.read_text(encoding="utf-8", errors="replace"))
        for path, _ in changed
        if path.name == PROGRESS_NAME and path.is_file()
    ]
    note = " ".join(note for note in notes if note)
    if not note:
        return result
    value = getattr(result, "value", None)
    if isinstance(value, str):
        return result.with_value(f"{value}\n\n{note}")
    if isinstance(value, dict):
        warning = value.get("warning")
        return result.with_value({**value, "warning": f"{warning} {note}" if warning else note})
    return result


def memory_admission(
    doors: Sequence[Admission],
    locate: Callable[[str, dict], tuple[list, bool]],
    resolve: Callable[[str], Any],
    alone: Callable[[Any], bool] = lambda execution: False,
) -> Any:
    """`doors` are the memory directories and their evidence; `locate` is `write_paths`;
    `resolve` turns a path a tool was given into the file it means; `alone` says whether the
    engine runs a call with nothing beside it. A `read_file` of a memory is counted
    (`Admission.read`), which is how the store learns what is used."""
    homes = [(Path(door.root).resolve(), door) for door in doors]
    known: dict[Path, tuple[tuple[int, int], bytes]] = {}

    def _targets(execution: Any) -> tuple[list[tuple[Path, Admission]], bool]:
        """The memory files a call names, and whether it names where it writes at all."""
        paths, located = locate(execution.name, execution.arguments or {})
        out: list[tuple[Path, Admission]] = []
        for raw in paths if located else ():
            try:
                path = Path(resolve(str(raw))).resolve()
            except Exception:  # noqa: BLE001 — a path that cannot be resolved is not ours
                continue
            for root, door in homes:
                if root in path.parents and ".history" not in path.parts:
                    out.append((path, door))
        return out, bool(located)

    def _verdict(path: Path, door: Admission, was: Optional[bytes]) -> Optional[Refusal]:
        if path.parent != Path(door.root).resolve() or path.suffix.lower() != ".md":
            return Refusal(
                "memory-kind",
                f"{path.name} is not a memory file. This directory holds memories only: one "
                "`.md` file per fact, directly inside it.",
            )
        return door.verdict(
            path.name,
            path.read_text(encoding="utf-8", errors="replace"),
            (was or b"").decode("utf-8", "replace"),
        )

    def _restore(before: dict[Path, Optional[bytes]]) -> None:
        for path, was in before.items():
            try:
                if was is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_bytes(was)
            except OSError:
                logger.warning("memory admission could not put %s back", path.name, exc_info=True)

    def _judge(changed: list[tuple[Path, Admission]], before: dict[Path, Optional[bytes]]) -> Optional[Refusal]:
        """Judge each changed file; on the first refusal put every one back and say why.
        Admitted files have their evidence recorded; a deleted one loses its record."""
        for path, door in changed:
            refusal = _verdict(path, door, before.get(path)) if path.is_file() else None
            if refusal is not None:
                _restore(before)
                return Refusal(refusal.rule, f"{path.name} was not saved: {refusal.reason}")
        for path, door in changed:
            if path.is_file():
                if before.get(path) is not None:
                    keep_version(path, before[path])  # superseded, not erased
                door.record(path.name, path.read_text(encoding="utf-8", errors="replace"))
            else:
                door.forget(path.name)
        return None

    def _count_read(execution: Any) -> None:
        raw = (execution.arguments or {}).get("path")
        if execution.name != "read_file" or not raw:
            return
        path = Path(resolve(str(raw))).resolve()
        for root, door in homes:
            if path.parent == root and path.suffix.lower() == ".md" and path.is_file():
                door.read(path.name)

    def _after(before: dict[Path, tuple[dict[Path, bytes], set[Path]]]) -> Optional[Refusal]:
        """What a call that cannot say where it writes changed, judged; None when admitted."""
        changed: list[tuple[Path, Admission]] = []
        was: dict[Path, Optional[bytes]] = {}
        new_folders: list[Path] = []
        for root, door in homes:
            files, folders = before[root]
            now, now_folders = _contents(root, known)
            for path in sorted(set(files) | set(now)):
                if files.get(path) == now.get(path):
                    continue
                if path in now and settled(path, path.read_text(encoding="utf-8", errors="replace")):
                    continue  # another door's, already judged
                changed.append((path, door))
                was[path] = files.get(path)
            new_folders += sorted(now_folders - folders)
        refusal = _judge(changed, was) if changed else None
        if refusal is not None:
            for folder in new_folders:
                shutil.rmtree(folder, ignore_errors=True)
        return refusal

    async def listener(execution: Any, nxt: Any) -> Any:
        try:
            targets, located = _targets(execution)
            before = None if located or not alone(execution) else {root: _contents(root, known) for root, _ in homes}
        except Exception:  # noqa: BLE001 — see the module note: this door fails open
            targets, before = [], None
        if before is not None:
            result = await nxt()
            try:
                refusal = _after(before)
            except Exception:  # noqa: BLE001
                logger.warning("memory admission failed open for %s", execution.name, exc_info=True)
                return result
            if refusal is None:
                return result
            return _blocked_result(
                Block(
                    f"the call ran, but what it wrote in the memory directory was undone: {refusal.reason}",
                    rule=refusal.rule,
                )
            )
        if not targets:
            result = await nxt()
            try:
                if getattr(result, "ok", False):
                    _count_read(execution)
            except Exception:  # noqa: BLE001 — counting a read must not cost the read
                pass
            return result
        was = {path: (path.read_bytes() if path.is_file() else None) for path, _ in targets}
        result = await nxt()
        if not getattr(result, "ok", False):
            return result
        try:
            changed = [
                (path, door) for path, door in targets
                if (path.read_bytes() if path.is_file() else None) != was[path]
            ]
            refusal = _judge(changed, was)
            if refusal is not None:
                return _blocked_result(Block(refusal.reason, rule=refusal.rule))
            return _advised(result, changed)
        except Exception:  # noqa: BLE001
            logger.warning("memory admission failed open for %s", execution.name, exc_info=True)
        return result

    return listener


__all__ = ["memory_admission"]
