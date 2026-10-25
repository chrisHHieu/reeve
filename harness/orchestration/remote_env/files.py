"""The remote filesystem behind ``RemoteWorld.fs_tools``.

Every operation runs over the shared SSH transport; nothing touches the controller's
disk. The surface mirrors exactly what ``LocalWorld.fs_tools`` returns — aisuite's
``list_files``/``read_file``/``read_file_lines``/``search_files``/``write_file``/
``write_file``/``apply_unified_diff``/``apply_patch``/``replace_in_file`` names,
signatures, return shapes and refusal strings — so the catalog composition and the
contracts in ``harness/tools/files.py`` do not learn a second dialect.

The strategy, command by command:

- **Reads are binary-safe by construction.** ``base64 < <path>`` produces the bytes as
  text; the transport frame carries it back and the decoder strips the wrapping newlines
  GNU/BSD ``base64`` insert. The UTF-8 decode happens here, so a binary file refuses the
  same way the local toolkit refuses one.
- **Writes pipe base64 to the remote command's stdin** (the transport's ``input_text``),
  so file content never has to fit in an argv string and no quoting of the content is
  involved. The remote side decodes with ``base64 -d`` and falls back to BSD's ``-D``.
- **Listing/searching use POSIX tools only** — ``find`` and ``grep -rn`` (with
  ``--include``/``--exclude-dir``), not ripgrep: a remote host is not assumed to have
  anything installed beyond a POSIX userland.
- **Containment is resolved locally before any SSH call**, on POSIX path semantics (the
  controller can be Windows; remote paths are always ``/``-rooted). A path outside the
  configured roots raises the exact local refusals (``Path escapes allowed roots``,
  ``Path is in a read-only directory``), so a refusal costs no round trip.
- **Patches are applied in Python** against remote reads and writes: porting the pure
  hunk-matching logic is cheaper and more predictable than hoping the host has ``patch``
  or ``git``.

Each command carries the ``# reeve-remote-op`` comment understood by
``harness.testing.fake_ssh.FakeRemoteHost`` — inert to a real shell, and what lets the
tests exercise these exact command strings without a POSIX emulator.
"""

from __future__ import annotations

import base64
import binascii
import fnmatch
import posixpath
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import aisuite as ai
from aisuite.toolkits.files import DEFAULT_IGNORES

from ..remote_environments import shell_quote
from harness.tools.patch_envelope import patch_result, plan_patch
from .transport import RemoteCommandError, RemoteCommandResult, RemoteSession, op_script

MAX_READ_BYTES = 200_000
MAX_SEARCH_BYTES = 1_000_000


# -- command builders -----------------------------------------------------------------


def _q(value: str) -> str:
    return shell_quote(value)


def mkdir_script(path: str, mode: int = 0o700) -> str:
    """`mkdir -p` + `chmod`, mode exact — the scratch contract's first-use command."""
    quoted = _q(path)
    command = f"umask 077; mkdir -p {quoted} && chmod {mode:o} {quoted}"
    return op_script("mkdir", {"path": path, "mode": int(mode)}, command=command)


def _write_script(path: str) -> str:
    # `b=$(cat)` buffers stdin once so the BSD fallback can retry without losing it.
    quoted = _q(path)
    command = (
        "b=$(cat) || exit 1\n"
        f"printf '%s' \"$b\" | base64 -d > {quoted} 2>/dev/null || "
        f"printf '%s' \"$b\" | base64 -D > {quoted}"
    )
    return op_script("write", {"path": path}, command=command)


# -- path resolution ------------------------------------------------------------------


def _canonical(path: str) -> str:
    text = str(path or "").strip()
    if not text:
        raise ValueError("path is required")
    if "\0" in text:
        raise ValueError("path cannot contain NUL bytes")
    if not posixpath.isabs(text):
        raise ValueError("remote paths must be absolute")
    return posixpath.normpath(text)


# -- patch parsing/application (pure, ported from aisuite's FileToolkit) ---------------


@dataclass
class _Hunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: list[str]


@dataclass
class _FilePatch:
    old_path: str
    new_path: str
    hunks: list[_Hunk]


def _clean_diff_path(path: str) -> str:
    return path[2:] if path.startswith(("a/", "b/")) else path


def _parse_hunk_range(value: str) -> tuple[int, int]:
    if "," not in value:
        return int(value), 1
    start, count = value.split(",", 1)
    return int(start), int(count)


def _parse_hunk_header(line: str) -> tuple[int, int, int, int]:
    parts = line.split()
    if (
        len(parts) < 3
        or not parts[1].startswith("-")
        or not parts[2].startswith("+")
    ):
        raise ValueError(f"Invalid unified diff hunk header: {line}")
    old_start, old_count = _parse_hunk_range(parts[1][1:])
    new_start, new_count = _parse_hunk_range(parts[2][1:])
    return old_start, old_count, new_start, new_count


def _parse_diff_file_line(line: str, marker: str) -> str:
    value = line[len(marker):].strip()
    if "\t" in value:
        value = value.split("\t", 1)[0]
    if value == "/dev/null":
        return value
    return _clean_diff_path(value)


def _parse_unified_diff(diff: str) -> list[_FilePatch]:
    lines = diff.splitlines(keepends=True)
    patches: list[_FilePatch] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.startswith("--- "):
            index += 1
            continue
        old_path = _parse_diff_file_line(line, "---")
        index += 1
        if index >= len(lines) or not lines[index].startswith("+++ "):
            raise ValueError("Unified diff is missing +++ file header.")
        new_path = _parse_diff_file_line(lines[index], "+++")
        index += 1
        hunks: list[_Hunk] = []
        while index < len(lines):
            current = lines[index]
            if current.startswith("--- "):
                break
            if not current.startswith("@@ "):
                index += 1
                continue
            old_start, old_count, new_start, new_count = _parse_hunk_header(current)
            index += 1
            hunk_lines: list[str] = []
            while index < len(lines):
                current = lines[index]
                if current.startswith("@@ ") or current.startswith("--- "):
                    break
                if current.startswith("\\ No newline at end of file"):
                    index += 1
                    continue
                if not current or current[0] not in {" ", "+", "-"}:
                    raise ValueError(f"Invalid unified diff hunk line: {current}")
                hunk_lines.append(current)
                index += 1
            hunks.append(
                _Hunk(old_start, old_count, new_start, new_count, hunk_lines)
            )
        if not hunks:
            raise ValueError(f"Unified diff has no hunks for {new_path}.")
        patches.append(_FilePatch(old_path=old_path, new_path=new_path, hunks=hunks))
    if not patches:
        raise ValueError("No unified diff file patches found.")
    return patches


def _apply_hunks(old_lines: list[str], hunks: list[_Hunk], path: str) -> list[str]:
    new_lines: list[str] = []
    cursor = 0
    for hunk in hunks:
        start = max(hunk.old_start - 1, 0)
        if start < cursor:
            raise ValueError(f"Overlapping unified diff hunks for {path}.")
        new_lines.extend(old_lines[cursor:start])
        cursor = start
        old_seen = 0
        new_seen = 0
        for line in hunk.lines:
            prefix, content = line[0], line[1:]
            if prefix == " ":
                if cursor >= len(old_lines) or old_lines[cursor] != content:
                    raise ValueError(f"Unified diff context does not match {path}.")
                new_lines.append(old_lines[cursor])
                cursor += 1
                old_seen += 1
                new_seen += 1
            elif prefix == "-":
                if cursor >= len(old_lines) or old_lines[cursor] != content:
                    raise ValueError(f"Unified diff removal does not match {path}.")
                cursor += 1
                old_seen += 1
            elif prefix == "+":
                new_lines.append(content)
                new_seen += 1
        if old_seen != hunk.old_count or new_seen != hunk.new_count:
            raise ValueError(f"Unified diff hunk line counts do not match {path}.")
    new_lines.extend(old_lines[cursor:])
    return new_lines


# -- the backend ----------------------------------------------------------------------


class RemoteFileBackend:
    """Remote file primitives + the aisuite-shaped operations over one world root."""

    def __init__(
        self,
        session: RemoteSession,
        root: str,
        *,
        roots: Optional[list] = None,
        max_read_bytes: int = MAX_READ_BYTES,
        max_search_bytes: int = MAX_SEARCH_BYTES,
        ignore: Optional[list[str]] = None,
    ) -> None:
        self.session = session
        self.root = _canonical(root)
        self._roots_ref = roots
        # aisuite's `files(roots=...)` anchors relative paths at roots[0] and ignores the
        # single `root` argument; mirror that so multi-root display and resolution match.
        if roots:
            self.root = self._roots()[0][0]
        self.max_read_bytes = max_read_bytes
        self.max_search_bytes = max_search_bytes
        self.ignore = set(DEFAULT_IGNORES if ignore is None else ignore)

    # -- roots ---------------------------------------------------------------------

    def _roots(self) -> list[tuple[str, bool]]:
        if not self._roots_ref:
            return [(self.root, True)]
        out: list[tuple[str, bool]] = []
        for item in self._roots_ref:
            if isinstance(item, dict):
                path, writable = item["path"], bool(item.get("writable", False))
            elif isinstance(item, (str, Path)):
                path, writable = item, False
            else:  # duck-typed RootDir-like
                path = getattr(item, "path")
                writable = bool(getattr(item, "writable", False))
            out.append((_canonical(str(path)), writable))
        return out or [(self.root, True)]

    @property
    def has_writable(self) -> bool:
        return any(writable for _, writable in self._roots())

    def _root_for(self, candidate: str) -> Optional[tuple[str, bool]]:
        for path, writable in self._roots():
            if candidate == path or candidate.startswith(path.rstrip("/") + "/"):
                return path, writable
        return None

    def _expand(self, path: Any) -> str:
        text = str(path or "").strip()
        if text == "~" or text.startswith("~/"):
            home = self.session.ensure_home()
            text = home if text == "~" else posixpath.join(home, text[2:])
        if "\0" in text:
            raise ValueError("path cannot contain NUL bytes")
        if not posixpath.isabs(text):
            text = posixpath.join(self.root, text)
        return posixpath.normpath(text)

    def _resolve(self, path: Any, *, for_write: bool = False) -> tuple[str, str]:
        candidate = self._expand(path)
        root = self._root_for(candidate)
        if root is None:
            raise PermissionError(f"Path escapes allowed roots: {path}")
        if for_write and not root[1]:
            raise PermissionError(f"Path is in a read-only directory: {path}")
        return candidate, root[0]

    def _relative(self, path: str) -> str:
        relative = posixpath.relpath(path, self.root)
        if relative == ".":
            return "."
        if relative.startswith(".."):
            return path
        return relative

    def _relative_parts(self, path: str) -> Optional[tuple[str, ...]]:
        relative = posixpath.relpath(path, self.root)
        if relative == "." or relative.startswith(".."):
            return None
        return tuple(part for part in relative.split("/") if part)

    def _ignored(self, path: str) -> bool:
        # `find` answers absolute paths; grep hits have already been made relative.
        if posixpath.isabs(path):
            parts = self._relative_parts(path)
            if parts is None:
                return True
        else:
            parts = tuple(part for part in path.split("/") if part)
            if not parts or ".." in parts:
                return True
        return any(part in self.ignore for part in parts)

    # -- transport -----------------------------------------------------------------

    def _run(
        self,
        script: str,
        *,
        input_text: Optional[str] = None,
        ok_codes: tuple[Optional[int], ...] = (0,),
        action: str = "",
    ) -> RemoteCommandResult:
        result = self.session.run(script, input_text=input_text)
        if result.exit_code in ok_codes:
            return result
        detail = (result.stderr or result.output or "").strip()
        detail = detail[:500] + ("…" if len(detail) > 500 else "")
        where = f" ({action})" if action else ""
        if result.exit_code is None:
            raise RemoteCommandError(
                f"the remote host did not report an exit status{where}: "
                f"{detail or 'the connection closed before the command finished'}",
                output=result.output,
            )
        raise RemoteCommandError(
            f"remote command failed{where} (exit {result.exit_code}): "
            f"{detail or 'no output'}",
            exit_code=result.exit_code,
            output=result.output,
        )

    # -- primitives ----------------------------------------------------------------

    def stat_kind(self, path: str) -> str:
        """`f`ile, `d`ir, `e`xists-other, or `n`othing — one POSIX probe."""
        quoted = _q(path)
        command = (
            f"if test -f {quoted}; then echo f; "
            f"elif test -d {quoted}; then echo d; "
            f"elif test -e {quoted}; then echo e; else echo n; fi"
        )
        output = self._run(op_script("stat", {"path": path}, command=command)).output
        kind = output.strip()[-1:] if output.strip() else "n"
        return kind if kind in "fden" else "n"

    def exists(self, path: str) -> bool:
        return self.stat_kind(path) != "n"

    # -- adapters the read-side tools and the observed-file bookkeeping share ----------
    # (`tools/files.py`'s windowed read, `tools/search.py`'s grep, `tools/observed.py`'s
    # read-before-edit checker). Named for what they answer, not how they answer it.

    def locate(self, path: Any) -> str:
        """The canonical remote target, with the backend's containment enforced.
        Raises the local refusal shapes (PermissionError / ValueError)."""
        target, _ = self._resolve(path)
        return target

    def display(self, path: Any) -> str:
        """The path as the local tools would show it: workspace-relative inside the
        primary root, absolute otherwise. Raises the local refusal shapes."""
        return self._relative(self.locate(path))

    def is_file(self, path: str) -> bool:
        return self.stat_kind(path) == "f"

    def is_dir(self, path: str) -> bool:
        return self.stat_kind(path) == "d"

    def digest(self, path: str) -> Optional[str]:
        """The file's SHA-256, or None when it cannot be one (missing, not a file,
        unreadable, or over `observed.MAX_DIGEST_BYTES`). One stat plus one read."""
        from harness.tools.observed import MAX_DIGEST_BYTES

        try:
            if self.stat_kind(path) != "f":
                return None
            data = self.read_bytes(path)
        except Exception:
            return None
        if len(data) > MAX_DIGEST_BYTES:
            return None
        import hashlib

        return hashlib.sha256(data).hexdigest()

    def read_bytes(self, path: str) -> bytes:
        command = f"base64 < {_q(path)}"
        output = self._run(
            op_script("read", {"path": path}, command=command), action=f"read {path}"
        ).output
        payload = "".join(output.split())
        if not payload:
            return b""
        try:
            return base64.b64decode(payload)
        except (binascii.Error, ValueError) as exc:
            raise ValueError(f"could not decode {path}: {exc}") from None

    def write_bytes(self, path: str, data: bytes) -> None:
        payload = base64.b64encode(data).decode("ascii")
        self._run(_write_script(path), input_text=payload, action=f"write {path}")

    def make_dir(self, path: str, mode: int = 0o700) -> None:
        self._run(mkdir_script(path, mode), action=f"mkdir {path}")

    def make_parent(self, path: str) -> None:
        parent = posixpath.dirname(path)
        if parent and parent != "/":
            self._run(
                mkdir_script(parent, 0o755), action=f"mkdir {parent}"
            )

    def remove_file(self, path: str) -> None:
        self._run(
            op_script("remove", {"path": path}, command=f"rm -f {_q(path)}"),
            action=f"rm {path}",
        )

    def find_files(
        self,
        path: str,
        pattern: str = "*",
        *,
        recursive: bool = True,
        max_results: int = 100,
    ) -> list[str]:
        command = f"find {_q(path)} -mindepth 1"
        if not recursive:
            command += " -maxdepth 1"
        command += " -type f"
        if pattern and pattern != "*":
            command += f" -name {_q(pattern)}"
        command += f" | head -n {max(1, int(max_results))}"
        output = self._run(
            op_script(
                "find",
                {
                    "path": path,
                    "pattern": pattern,
                    "recursive": bool(recursive),
                    "max_results": int(max_results),
                },
                command=command,
            ),
            action=f"find {path}",
        ).output
        return [line for line in output.splitlines() if line.strip()]

    def grep(
        self,
        query: str,
        path: str,
        *,
        pattern: str = "*",
        max_results: int = 50,
        regex: bool = False,
        ignore_case: bool = False,
        includes: Optional[list[str]] = None,
    ) -> list[dict[str, Any]]:
        # `-F` (fixed string) is the aisuite `search_files` contract; `-E` is what our
        # `grep` tool promises (ripgrep regex, POSIX ERE remotely — one dialect wherever
        # a POSIX userland exists). BSD and GNU grep both take `-E`.
        flag = "-E" if regex else "-F"
        command = f"grep -rn --binary-files=without-match {flag}"
        if ignore_case:
            command += " -i"
        if includes:
            for include in includes:
                command += f" --include={_q(include)}"
        elif pattern and pattern != "*":
            command += f" --include={_q(pattern)}"
        for ignored in sorted(self.ignore):
            command += f" --exclude-dir={_q(ignored)}"
        command += f" -- {_q(query)} {_q(path)} | head -n {max(1, int(max_results))}"
        result = self._run(
            op_script(
                "grep",
                {
                    "query": query,
                    "path": path,
                    "pattern": pattern,
                    "max_results": int(max_results),
                    "regex": bool(regex),
                    "ignore_case": bool(ignore_case),
                    "includes": list(includes or []),
                },
                command=command,
            ),
            ok_codes=(0, 1),
            action=f"grep {query!r}",
        )
        matches: list[dict[str, Any]] = []
        for line in result.output.splitlines():
            file_name, sep, rest = line.partition(":")
            if not sep:
                continue
            number, sep2, text = rest.partition(":")
            if not sep2 or not number.strip().isdigit():
                continue
            matches.append(
                {
                    "path": self._relative(posixpath.normpath(file_name)),
                    "line": int(number.strip()),
                    "text": text,
                }
            )
            if len(matches) >= max_results:
                break
        return matches

    # -- the aisuite-shaped operations ----------------------------------------------

    def list_files(
        self,
        path: str = ".",
        pattern: str = "*",
        recursive: bool = True,
        max_results: int = 100,
    ) -> list[str]:
        limit = max_results if isinstance(max_results, int) and max_results > 0 else 100
        base, _ = self._resolve(path)
        kind = self.stat_kind(base)
        if kind == "n":
            raise ValueError(f"Path does not exist: {path}")
        if kind != "d":
            raise ValueError(f"Path is not a directory: {path}")
        results: list[str] = []
        for name in self.find_files(
            base, pattern=pattern, recursive=bool(recursive), max_results=limit
        ):
            if self._ignored(name):
                continue
            results.append(self._relative(name))
            if len(results) >= limit:
                break
        return sorted(results)

    def read_file(self, path: str) -> str:
        target, _ = self._resolve(path)
        kind = self.stat_kind(target)
        if kind == "n":
            raise ValueError(f"File does not exist: {path}")
        if kind != "f":
            raise ValueError(f"Path is not a file: {path}")
        data = self.read_bytes(target)
        if len(data) > self.max_read_bytes:
            raise ValueError(
                f"File exceeds max_read_bytes ({self.max_read_bytes}): {path}"
            )
        return data.decode("utf-8")

    def read_file_lines(
        self,
        path: str,
        start_line: int = 1,
        max_lines: int = 100,
    ) -> dict[str, Any]:
        if start_line < 1:
            raise ValueError("start_line must be >= 1")
        if max_lines < 1:
            raise ValueError("max_lines must be >= 1")
        content = self.read_file(path)
        lines = content.splitlines()
        start_index = start_line - 1
        selected = lines[start_index : start_index + max_lines]
        end_line = start_line + len(selected) - 1 if selected else start_line - 1
        target, _ = self._resolve_file(path)
        return {
            "path": self._relative(target),
            "start_line": start_line,
            "end_line": end_line,
            "total_lines": len(lines),
            "content": "\n".join(selected),
        }

    def search_files(
        self,
        query: str,
        path: str = ".",
        pattern: str = "*",
        max_results: int = 50,
        *,
        regex: bool = False,
        ignore_case: bool = False,
        includes: Optional[list[str]] = None,
    ) -> list[dict[str, Any]]:
        """`includes` (a `grep` type's globs) narrows the files on the host; a `pattern` glob
        given with them is applied here as well, since grep's `--include`s are an OR."""
        limit = max_results if isinstance(max_results, int) and max_results > 0 else 50
        base, _ = self._resolve(path)
        kind = self.stat_kind(base)
        if kind == "n":
            raise ValueError(f"Path does not exist: {path}")
        if kind != "d":
            raise ValueError(f"Path is not a directory: {path}")
        hits = self.grep(
            query, base, pattern=pattern, max_results=limit, regex=regex,
            ignore_case=ignore_case, includes=includes,
        )
        if includes and pattern and pattern != "*":
            hits = [hit for hit in hits if fnmatch.fnmatch(posixpath.basename(hit["path"]), pattern)]
        return [hit for hit in hits if not self._ignored(hit["path"])][:limit]

    def write_file(self, path: str, content: str, overwrite: bool = True) -> str:
        if not self.has_writable:
            raise PermissionError("write_file is disabled for this file toolkit.")
        target, _ = self._resolve(path, for_write=True)
        kind = self.stat_kind(target)
        if kind == "d":
            raise ValueError(f"Path is a directory: {path}")
        if kind in ("f", "e") and not overwrite:
            raise FileExistsError(f"File already exists: {path}")
        self.make_parent(target)
        self.write_bytes(target, str(content).encode("utf-8"))
        return self._relative(target)

    def replace_in_file(
        self,
        path: str,
        old: str,
        new: str,
        expected_replacements: int = 1,
    ) -> dict[str, Any]:
        if not self.has_writable:
            raise PermissionError("replace_in_file is disabled for this file toolkit.")
        if not old:
            raise ValueError("old must be a non-empty string.")
        if expected_replacements < 1:
            raise ValueError("expected_replacements must be >= 1")
        target, _ = self._resolve_file(path, for_write=True)
        content = self._read_text(target, path)
        count = content.count(old)
        if count != expected_replacements:
            raise ValueError(
                f"Expected {expected_replacements} replacement(s), found {count}: {path}"
            )
        updated = content.replace(old, new, expected_replacements)
        self.write_bytes(target, updated.encode("utf-8"))
        return {
            "path": self._relative(target),
            "replacements": count,
            "chars_before": len(content),
            "chars_after": len(updated),
        }

    def apply_unified_diff(self, diff: str) -> dict[str, Any]:
        if not self.has_writable:
            raise PermissionError(
                "apply_unified_diff is disabled for this file toolkit."
            )
        patches = _parse_unified_diff(diff)
        changed_files: list[str] = []
        added_files: list[str] = []
        deleted_files: list[str] = []
        hunk_count = 0
        for patch in patches:
            old_is_dev_null = patch.old_path == "/dev/null"
            new_is_dev_null = patch.new_path == "/dev/null"
            target_name = patch.old_path if new_is_dev_null else patch.new_path
            target, _ = self._resolve(target_name, for_write=True)
            old_lines: list[str] = []
            if not old_is_dev_null:
                self._require_file(target, target_name)
                old_lines = self._read_text(target, target_name).splitlines(
                    keepends=True
                )
            new_lines = _apply_hunks(old_lines, patch.hunks, target_name)
            hunk_count += len(patch.hunks)
            if new_is_dev_null:
                self.remove_file(target)
                deleted_files.append(self._relative(target))
                continue
            self.make_parent(target)
            self.write_bytes(target, "".join(new_lines).encode("utf-8"))
            relative = self._relative(target)
            changed_files.append(relative)
            if old_is_dev_null:
                added_files.append(relative)
        return {
            "changed_files": changed_files,
            "added_files": added_files,
            "deleted_files": deleted_files,
            "file_count": len(patches),
            "hunk_count": hunk_count,
        }

    def apply_patch(self, patch: str) -> dict[str, Any]:
        """The envelope through the shared core (`tools/patch_envelope`): the
        same parser and matcher as a local session, every file planned before any remote
        write, each file written back with its own line endings and BOM."""
        if not self.has_writable:
            raise PermissionError("apply_patch is disabled for this file toolkit.")
        crlf_files: set[str] = set()
        bom_files: set[str] = set()

        def read(path: str) -> str:
            target, _ = self._resolve_file(path, for_write=True)
            text = self._read_text(target, path)
            if text.startswith("\ufeff"):
                bom_files.add(target)
                text = text[1:]
            if text.count("\r\n") > text.count("\n") - text.count("\r\n"):
                crlf_files.add(target)
            return text.replace("\r\n", "\n")

        def exists(path: str) -> bool:
            return self.stat_kind(self._resolve(path, for_write=True)[0]) != "n"

        planned = plan_patch(patch, read, exists)
        for item in planned:
            op = item.op
            source, _ = self._resolve(op.path, for_write=True)
            if item.text is None:
                self.remove_file(source)
                continue
            destination = self._resolve(op.move_to, for_write=True)[0] if op.move_to else source
            text = item.text.replace("\n", "\r\n") if source in crlf_files else item.text
            text = "\ufeff" + text if source in bom_files else text
            self.make_parent(destination)
            self.write_bytes(destination, text.encode("utf-8"))
            if op.move_to and destination != source:
                self.remove_file(source)
        return patch_result(
            planned, lambda path: self._relative(self._resolve(path, for_write=True)[0])
        )

    # -- internals -----------------------------------------------------------------

    def _require_file(self, target: str, display: str) -> None:
        kind = self.stat_kind(target)
        if kind == "n":
            raise ValueError(f"File does not exist: {display}")
        if kind != "f":
            raise ValueError(f"Path is not a file: {display}")

    def _resolve_file(
        self, path: str, *, for_write: bool = False
    ) -> tuple[str, str]:
        target, root = self._resolve(path, for_write=for_write)
        kind = self.stat_kind(target)
        if kind == "n":
            raise ValueError(f"File does not exist: {path}")
        if kind != "f":
            raise ValueError(f"Path is not a file: {path}")
        return target, root

    def _read_text(self, target: str, display: str) -> str:
        data = self.read_bytes(target)
        if len(data) > self.max_read_bytes:
            raise ValueError(
                f"File exceeds max_read_bytes ({self.max_read_bytes}): {display}"
            )
        return data.decode("utf-8")


# -- the model-facing tool list --------------------------------------------------------


def remote_file_tools(backend: RemoteFileBackend) -> list:
    """The aisuite-shaped fs tool closures over a ``RemoteFileBackend``.

    Names, signatures and metadata mirror ``LocalWorld.fs_tools`` exactly (writes are
    exposed only when some root is writable, as the local toolkit does), so the catalog
    composition and the borrowed contracts keep working unchanged.
    """

    def list_files(
        path: str = ".", pattern: str = "*", recursive: bool = True, max_results: int = 100
    ) -> list[str]:
        """List files under the configured root."""
        return backend.list_files(
            path=path, pattern=pattern, recursive=recursive, max_results=max_results
        )

    def read_file(path: str) -> str:
        """Read a UTF-8 text file under the configured root."""
        return backend.read_file(path=path)

    def read_file_lines(
        path: str, start_line: int = 1, max_lines: int = 100
    ) -> dict[str, Any]:
        """Read a line range from a UTF-8 text file under the configured root."""
        return backend.read_file_lines(
            path=path, start_line=start_line, max_lines=max_lines
        )

    def search_files(
        query: str, path: str = ".", pattern: str = "*", max_results: int = 50
    ) -> list[dict[str, Any]]:
        """Search UTF-8 text files under the configured root."""
        return backend.search_files(
            query=query, path=path, pattern=pattern, max_results=max_results
        )

    def _wrap(fn, *, risk: str, capabilities: list[str], approval: bool = False):
        return ai.tool(
            fn,
            metadata=ai.ToolMetadata(
                category="filesystem",
                risk_level=risk,
                capabilities=list(capabilities),
                requires_approval=approval,
            ),
        )

    tools = [
        _wrap(list_files, risk="low", capabilities=["list_files"]),
        _wrap(read_file, risk="low", capabilities=["read_file"]),
        _wrap(read_file_lines, risk="low", capabilities=["read_file_lines"]),
        _wrap(search_files, risk="low", capabilities=["search_files"]),
    ]
    if not backend.has_writable:
        return tools

    def write_file(path: str, content: str, overwrite: bool = True) -> str:
        """Write a UTF-8 text file under the configured root."""
        return backend.write_file(path=path, content=content, overwrite=overwrite)

    def apply_unified_diff(diff: str) -> dict[str, Any]:
        """Apply a unified diff patch under the configured root."""
        return backend.apply_unified_diff(diff=diff)

    def apply_patch(patch: str) -> dict[str, Any]:
        """Apply a Codex-style patch envelope under the configured root."""
        return backend.apply_patch(patch=patch)

    def replace_in_file(
        path: str, old: str, new: str, expected_replacements: int = 1
    ) -> dict[str, Any]:
        """Replace an exact text fragment in a UTF-8 file under the configured root."""
        return backend.replace_in_file(
            path=path,
            old=old,
            new=new,
            expected_replacements=expected_replacements,
        )

    tools.extend(
        [
            _wrap(
                write_file,
                risk="medium",
                capabilities=["write_file"],
                approval=True,
            ),
            _wrap(
                apply_unified_diff,
                risk="medium",
                capabilities=["apply_patch"],
                approval=True,
            ),
            _wrap(
                apply_patch,
                risk="medium",
                capabilities=["apply_patch"],
                approval=True,
            ),
            _wrap(
                replace_in_file,
                risk="medium",
                capabilities=["edit_file"],
                approval=True,
            ),
        ]
    )
    return tools


__all__ = [
    "MAX_READ_BYTES",
    "MAX_SEARCH_BYTES",
    "RemoteFileBackend",
    "mkdir_script",
    "remote_file_tools",
]
