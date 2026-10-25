"""Atomic writers for the model-facing file tools.

`write_file`/`replace_in_file` used to come from aisuite's toolkit, which calls
`Path.write_text` directly. Two silent losses followed from that: a crash mid-write leaves
a truncated file (no temp+rename), and a file's own conventions are dropped (a CRLF file
comes back as LF, a UTF-8 BOM disappears) — the kind of damage `undo` can only fix once
somebody notices.

The recipe: temp in the SAME directory, `O_EXCL`, fsync, copy the original mode,
`os.replace`. The text side is a BOM + dominant line ending round-trip. Both are small, and the
toolkit had no place to put either.

Local disk only, on purpose: a remote world keeps its own closures, which write on the
remote host. Replacing those here would write to the wrong machine.
"""

from __future__ import annotations

import os
import secrets
import shutil
import time
from pathlib import Path
from typing import Any, Optional

import aisuite as ai

from harness.config.roots import allowed_dirs, containing_root
from .contract import attach_contract
from .edit_matchers import (
    _literal_matches,
    apply_matches,
    closest_region,
    describe,
    find_matches,
    line_of,
    match_counts,
    replacement_for,
)
from .files.access import escape_refusal as _escapes
from .files.contracts import BORROWED_CONTRACTS as _BORROWED_CONTRACTS
from .files.schemas import BORROWED_SCHEMAS as _BORROWED_SCHEMAS
from .patch_envelope import patch_result, plan_patch


# -- our own writers -----------------------------------------------------------------
#
# `write_file`/`replace_in_file` used to come from aisuite's toolkit, which calls
# `Path.write_text` directly. Two silent losses followed from that: a crash mid-write
# leaves a truncated file (no temp+rename), and a file's own conventions are dropped (a
# CRLF file comes back as LF, a UTF-8 BOM disappears) — the kind of damage `undo` can
# only fix once somebody notices.
#
# The recipe: temp in the SAME directory, `O_EXCL`, fsync, copy the original mode,
# `os.replace`. The text side is a BOM + dominant line ending round-trip. Both are small and
# both are things the toolkit had no place to put.
#
# Local disk only, on purpose: a remote world keeps its own closures, which write on the
# remote host. Replacing those here would write to the wrong machine.


def _text_conventions(target: Path) -> "tuple[str, bool, bool]":
    """(text, had_bom, crlf) for an existing text file; ("", False, False) when new.

    Decoded strictly: a file that is not UTF-8 text is not something this tool should
    rewrite with replacement characters, and the caller reports it as an error instead.
    """
    if not target.is_file():
        return "", False, False
    raw = target.read_bytes()
    bom = raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig" if bom else "utf-8")
    crlf = text.count("\r\n") > text.count("\n") - text.count("\r\n")
    return text, bom, crlf


def _encode_like(text: str, *, bom: bool, crlf: bool) -> bytes:
    """Bytes for `text` in the file's own convention (LF-normalised first)."""
    normalized = text.replace("\r\n", "\n")
    if crlf:
        normalized = normalized.replace("\n", "\r\n")
    data = normalized.encode("utf-8")
    return (b"\xef\xbb\xbf" + data) if bom else data


#: How long a rename waits out, step by step, a file another program holds open on Windows.
_REPLACE_WAITS_S = (0.05, 0.1, 0.15, 0.25, 0.45)
_WINDOWS = os.name == "nt"


def _replace(tmp: Path, target: Path) -> None:
    """`os.replace`, waiting out a brief hold on Windows. An indexer, a virus scan or a viewer that
    has the target open makes the rename fail for a moment with "Access is denied" (WinError 5) or
    a sharing violation: a real session's edit of DESIGN.md failed so (2026-10-19). Elsewhere a
    PermissionError is the file's own and final."""
    for wait in (*_REPLACE_WAITS_S, None):
        try:
            os.replace(tmp, target)
            return
        except PermissionError:
            if not _WINDOWS or wait is None:
                raise
            time.sleep(wait)


def _atomic_write(target: Path, data: bytes) -> None:
    """Write `data` to `target` atomically, preserving the file's mode.

    A temp file in the same directory keeps the rename on one filesystem; `O_EXCL` means
    we never adopt a leftover; fsync means a power loss leaves either the old file or the
    new one, never a half one. The mode copy is what keeps an executable's bit.
    """
    tmp = target.with_name(f".{target.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        if target.exists():
            shutil.copymode(target, tmp)
        _replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def file_writers(
    workspace: str, roots: Optional[list] = None, backend: Optional[Any] = None
) -> list:
    """`write_file` + `replace_in_file` with atomic writes and convention round-trips.

    With `backend` (a remote world) the list is EMPTY: those sessions keep the world's own
    closures, which write over SSH. Returning ours there would silently write to the
    controller's disk while the session believes it is editing the remote host.
    """
    if backend is not None:
        return []
    root = Path(workspace).resolve()

    def _resolve(path: str) -> "tuple[Path, Path]":
        target = (root / path).resolve()
        allowed = allowed_dirs(root, roots)
        home = containing_root(target, allowed)
        if home is None:
            # PermissionError, not ValueError: this is a policy refusal, the same class the
            # toolkit raised, and callers (and tests) match on it.
            raise PermissionError(_escapes(allowed))
        return target, home

    def write_file(path: str, content: str, overwrite: bool = True) -> str:
        """Write a text file, atomically and in the file's own convention.

        Overwrites by default. An existing file keeps its BOM and its line endings; a new
        file is UTF-8 with LF. The write lands whole or not at all.
        """
        target, home = _resolve(path)
        if target.exists() and target.is_dir():
            raise ValueError(f"Path is a directory: {path}")
        if target.exists() and not overwrite:
            raise FileExistsError(f"File already exists: {path}")
        target.parent.mkdir(parents=True, exist_ok=True)
        _text, bom, crlf = _text_conventions(target)
        _atomic_write(target, _encode_like(str(content), bom=bom, crlf=crlf))
        # `as_posix()` for the primary root, absolute otherwise: the toolkit's own
        # convention, kept so the swap is invisible to anything that reads the path back
        # (a model echoing it into the next call, a card rendering it).
        try:
            return target.relative_to(root).as_posix()
        except ValueError:
            return str(target)

    def replace_in_file(
        path: str,
        old: str,
        new: str,
        expected_replacements: int = 1,
    ) -> dict[str, Any]:
        """Replace an exact fragment, atomically, with line endings normalised for matching.

        `old` is matched against the file with CRLF folded to LF, so a CRLF file is
        editable with the text as the model saw it in `read_file` (which normalises too);
        the file is written back with the convention it already had.
        """
        if not old:
            raise ValueError("old must be a non-empty string.")
        if expected_replacements < 1:
            raise ValueError("expected_replacements must be >= 1")
        target, home = _resolve(path)
        if not target.is_file():
            raise ValueError(f"not a file: {path}")
        text, bom, crlf = _text_conventions(target)
        normalized = text.replace("\r\n", "\n")
        needle = str(old).replace("\r\n", "\n")
        matches, via = find_matches(normalized, needle, expected=expected_replacements)
        if not matches:
            counts = match_counts(normalized, needle)
            detail = ", ".join(
                f"{name}: {count}" for name, count in counts.items() if count
            )
            found = [
                line_of(normalized, m.start)
                for m in _literal_matches(normalized, needle, "exact")
            ]
            # Say WHERE (2026-09-25 audit, W9): several matches name their lines and the
            # two ways out; none names the closest text and its first differing line.
            if found:
                where = (
                    f" — at lines {', '.join(map(str, found[:10]))}. Include more "
                    "surrounding lines in `old` to pick one, or set expected_replacements "
                    f"to {len(found)} to change them all."
                )
            else:
                near = closest_region(normalized, needle)
                where = (
                    f" — {near}. Copy `old` from a fresh read of those lines."
                    if near
                    else " — nothing in the file resembles `old`; re-read the file."
                )
            raise ValueError(
                f"Expected {expected_replacements} replacement(s), found "
                f"{counts['exact']}: {path}"
                + (f" (other readings — {detail})" if detail else "")
                + where
            )
        updated = apply_matches(
            normalized, matches, replacement_for(str(new).replace("\r\n", "\n"), via)
        )
        _atomic_write(target, _encode_like(updated, bom=bom, crlf=crlf))
        try:
            label = target.relative_to(root).as_posix()
        except ValueError:
            label = str(target)
        result: dict[str, Any] = {
            "path": label,
            "replacements": len(matches),
            "chars_before": len(normalized),
            "chars_after": len(updated),
        }
        # Say WHICH rung matched when it was not the exact one: an edit that landed by
        # fuzzy means is one the model should be able to reason about afterwards.
        note = describe(via)
        if note:
            result["matched_via"] = note
        return result

    def apply_patch(patch: str) -> dict[str, Any]:
        """Apply a Codex `apply_patch` envelope: every file planned first, then written.

        The envelope is parsed and applied by `patch_envelope` (one matcher with
        `replace_in_file`), against each file with CRLF folded to LF; nothing is written
        until every file applies, and each file is written back atomically in its own
        convention (BOM, line endings) — the toolkit's writer turned an LF file into CRLF
        on Windows, failed a first-line hunk under a BOM, and left earlier files edited when
        a later one missed.
        """
        conventions: dict[Path, tuple[bool, bool]] = {}

        def _file(path: str) -> Path:
            target, _home = _resolve(path)
            if not target.is_file():
                raise ValueError(f"File does not exist: {path}")
            return target

        def read(path: str) -> str:
            target = _file(path)
            text, bom, crlf = _text_conventions(target)
            conventions[target] = (bom, crlf)
            return text.replace("\r\n", "\n")

        def exists(path: str) -> bool:
            return _resolve(path)[0].exists()

        planned = plan_patch(patch, read, exists)
        for item in planned:
            op = item.op
            source, _home = _resolve(op.path)
            if item.text is None:
                source.unlink()
                continue
            target = _resolve(op.move_to)[0] if op.move_to else source
            bom, crlf = conventions.get(source, (False, False))
            target.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write(target, _encode_like(item.text, bom=bom, crlf=crlf))
            if op.move_to and target != source:
                source.unlink()

        def label(path: str) -> str:
            target = _resolve(path)[0]
            try:
                return target.relative_to(root).as_posix()
            except ValueError:
                return str(target)

        return patch_result(planned, label)

    writers = ((write_file, "write_file"), (replace_in_file, "replace_in_file"), (apply_patch, "apply_patch"))
    for function, name in writers:
        function.__name__ = name
        function.__doc__ = _BORROWED_SCHEMAS[name]["function"]["description"]
        function.__aisuite_tool_metadata__ = ai.ToolMetadata(
            name=name,
            category="filesystem",
            risk_level="medium",
            capabilities=[
                {"write_file": "write_file", "apply_patch": "apply_patch"}.get(name, "edit_file")
            ],
            requires_approval=True,
        )
        function.__tool_schema__ = _BORROWED_SCHEMAS[name]
        attach_contract(function, _BORROWED_CONTRACTS[name])
    return [write_file, replace_in_file, apply_patch]


