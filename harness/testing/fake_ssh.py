"""A tiny stand-in for the system `ssh`, injected through remote-environments' runner seam.

Tests must never spawn a real ssh (no network, no host keys, no ~/.ssh on CI). The store's
`runner(*, args, timeout, input_text=None)` seam is the one impure edge, so a `FakeSsh()` instance is passed
where `run_ssh` would go: it records the argv and answers with a canned probe frame (or a
canned failure), exactly like the real binary would on stdout/stderr.

    store = RemoteEnvironmentStore(runner=FakeSsh(platform="Darwin", arch="arm64"))
    store.test_connection(profile_id)  # -> {"ok": True, "platform": "darwin", ...}

Phase 2 adds ``FakeRemoteHost``: a small in-memory remote filesystem and shell that the
same fake can serve commands against. It understands the exact command strings the
remote world generates — the transport's sentinel frame, the ``# reeve-remote-op``
file-operation header, and a small POSIX shell vocabulary for executor commands — so
``RemoteWorld`` tests exercise real argv and real framing with no POSIX shell on the
controller and no host:

    fake = FakeSsh(host=FakeRemoteHost(files={...}))
    world = remote_world(profile, "/home/dev/project", runner=fake)

`last_args` / `last_destination` / `last_remote_command` / `last_input` make the
security posture and the stdin payload assertable from the call itself.
"""

from __future__ import annotations

import base64
import fnmatch
import json
import posixpath
import re
import shlex
from dataclasses import dataclass, field
from typing import Any, Optional

from harness.orchestration.remote_env.transport import OP_MARKER_PREFIX
from harness.orchestration.remote_environments import INSPECT_SENTINEL, PROBE_TIMEOUT_SECONDS, CommandResult

_FRAME_OPEN_RE = re.compile(r"printf '\\000([A-Za-z0-9_]+)\\000'\n")
_OPERATOR_RE = re.compile(r"\s*(&&|\|\||;)\s*")


def _unquote_word(text: str) -> str:
    """Reverse POSIX single/double quoting for one word (the frame's ``sh -c '<script>'``)."""
    text = text.strip()
    out: list[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char == "'":
            index += 1
            while index < len(text) and text[index] != "'":
                out.append(text[index])
                index += 1
            index += 1
        elif char == '"':
            index += 1
            while index < len(text) and text[index] != '"':
                if text[index] == "\\" and index + 1 < len(text):
                    out.append(text[index + 1])
                    index += 2
                else:
                    out.append(text[index])
                    index += 1
            index += 1
        else:
            out.append(char)
            index += 1
    return "".join(out)


def _unwrap_sh(command: str) -> str:
    if command.startswith("sh -c "):
        return _unquote_word(command[len("sh -c ") :])
    return command


def _split_frame(script: str) -> tuple[Optional[str], str]:
    match = _FRAME_OPEN_RE.match(script)
    if match is None:
        return None, script
    sentinel = match.group(1)
    rest = script[match.end() :]
    trailer = (
        "__reeve_rc=$?\n"
        f"printf '\\000{sentinel}\\000%s\\000%s\\000' \"$__reeve_rc\" \"$PWD\"\n"
    )
    index = rest.rfind(trailer)
    return sentinel, (rest[:index] if index >= 0 else rest).strip("\n")


@dataclass
class FakeRemoteHost:
    """A small remote host: an in-memory POSIX-ish filesystem plus a command vocabulary.

    Supported commands (enough for the remote world and its tests): ``pwd``, ``echo``,
    ``printf``, ``true``/``false``, ``cd``, ``export``/``VAR=value``, ``mkdir -p``,
    ``chmod``, ``rm``, ``touch``, ``cat``, ``test``/``[``, and a small ``git -C <repo>
    <status|diff|log>`` vocabulary that answers from ``git`` (keyed by the subcommand),
    so remote git metadata can be proven without a real repo. Everything else answers
    exit 127 like a shell would, so an unsupported command fails loudly rather than
    silently.
    """

    home: str = "/home/dev"
    platform: str = "Linux"
    arch: str = "x86_64"
    cwd: Optional[str] = None
    files: dict[str, bytes] = field(default_factory=dict)
    directories: set[str] = field(default_factory=set)
    modes: dict[str, int] = field(default_factory=dict)
    env: dict[str, str] = field(default_factory=dict)
    #: Canned `git status` / `git diff` / `git log` stdout, keyed by the subcommand.
    #: The repo is "real" only when the test planted `<repo>/.git`; otherwise git
    #: answers 128 like a real one would outside a repository.
    git: dict[str, str] = field(default_factory=dict)
    #: Directories whose listing answers "Permission denied" (the fake's permission
    #: model): the environment picker's refusal path needs a host that refuses.
    denied: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        if self.cwd is None:
            self.cwd = self.home
        for directory in [self.home, self.cwd, *list(self.directories), "/"]:
            self._ensure_dir(directory)

    # -- test fixtures -------------------------------------------------------------

    def write(self, path: str, content: str | bytes) -> None:
        data = content.encode("utf-8") if isinstance(content, str) else bytes(content)
        self._ensure_dir(posixpath.dirname(path))
        self.files[path] = data

    def read(self, path: str) -> bytes:
        return self.files[path]

    def read_text(self, path: str) -> str:
        return self.files[path].decode("utf-8")

    # -- filesystem internals ------------------------------------------------------

    def _ensure_dir(self, path: str) -> None:
        normalized = posixpath.normpath(path)
        parts = [part for part in normalized.split("/") if part]
        current = "/"
        for part in parts:
            current = posixpath.join(current, part)
            self.directories.add(current)

    def _is_dir(self, path: str) -> bool:
        return posixpath.normpath(path) in self.directories

    def _is_file(self, path: str) -> bool:
        return posixpath.normpath(path) in self.files

    def _absolute(self, value: str) -> str:
        value = self._expand(value)
        if not posixpath.isabs(value):
            value = posixpath.join(self.cwd or self.home, value)
        return posixpath.normpath(value)

    def _expand(self, value: str) -> str:
        def replace(match: re.Match[str]) -> str:
            name = match.group(1) or match.group(2)
            if name == "PWD":
                return self.cwd or ""
            if name == "HOME":
                return self.home
            return self.env.get(name, "")

        return re.sub(r"\$\{(\w+)\}|\$(\w+)", replace, value)

    # -- execution -----------------------------------------------------------------

    def execute(self, command: str, stdin: str = "") -> tuple[int, str, str]:
        script = _unwrap_sh(command)
        sentinel, body = _split_frame(script)
        rc, out, err = self._run_body(body, stdin)
        if sentinel is None:
            return rc, out, err
        return (
            rc,
            f"\x00{sentinel}\x00{out}\x00{sentinel}\x00{rc}\x00{self.cwd}\x00",
            err,
        )

    def _run_body(self, body: str, stdin: str) -> tuple[int, str, str]:
        body = body.strip("\n")
        if body.startswith(OP_MARKER_PREFIX):
            return self._run_op(body, stdin)
        return self._run_shell(body, stdin)

    def _run_shell(self, body: str, stdin: str) -> tuple[int, str, str]:
        rc = 0
        outs: list[str] = []
        errs: list[str] = []
        for line in body.split("\n"):
            if not line.strip():
                continue
            rc, out, err = self._run_line(line.strip(), stdin)
            outs.append(out)
            errs.append(err)
        return rc, "".join(outs), "".join(errs)

    def _run_line(self, line: str, stdin: str) -> tuple[int, str, str]:
        parts = _OPERATOR_RE.split(line)
        result: tuple[int, str, str] = (0, "", "")
        should_run = True
        for index, part in enumerate(parts):
            if index % 2 == 0:
                if should_run:
                    result = self._exec_statement(part, stdin)
            else:
                if part == "&&":
                    should_run = result[0] == 0
                elif part == "||":
                    should_run = result[0] != 0
                else:
                    should_run = True
        return result

    def _args(self, text: str) -> list[str]:
        try:
            return shlex.split(text)
        except ValueError:
            return text.split()

    def _exec_statement(self, statement: str, stdin: str) -> tuple[int, str, str]:
        text = statement.strip()
        if not text:
            return 0, "", ""
        text = text.replace("2>/dev/null", "").strip()
        if not text:
            return 0, "", ""

        if text.startswith("umask"):
            return 0, "", ""
        if text.startswith("cd "):
            target = self._absolute(_unquote_word(text[3:].strip()))
            if not self._is_dir(target):
                return 1, "", f"cd: {target}: No such file or directory"
            self.cwd = target
            return 0, "", ""
        if text.startswith("export "):
            name, equals, value = text[len("export ") :].partition("=")
            if equals:
                self.env[name.strip()] = self._expand(
                    _unquote_word(value.strip()) if value.strip().startswith(("'", '"')) else value.strip()
                )
            return 0, "", ""
        if re.match(r"^[A-Za-z_]\w*=", text):
            name, _, value = text.partition("=")
            stripped = value.strip()
            if stripped.startswith(("'", '"')):
                stripped = _unquote_word(stripped)
            self.env[name] = self._expand(stripped)
            return 0, "", ""
        if text == "pwd":
            return 0, (self.cwd or "") + "\n", ""
        if text == "true":
            return 0, "", ""
        if text == "false":
            return 1, "", ""
        if text.startswith("mkdir "):
            for argument in self._args(text[len("mkdir ") :]):
                if argument in ("-p", "--"):
                    continue
                self._ensure_dir(self._absolute(argument))
            return 0, "", ""
        if text.startswith("chmod "):
            arguments = self._args(text[len("chmod ") :])
            if len(arguments) >= 2:
                target = self._absolute(arguments[-1])
                try:
                    self.modes[target] = int(arguments[0], 8)
                except ValueError:
                    return 1, "", f"chmod: invalid mode: {arguments[0]}"
            return 0, "", ""
        if text.startswith("rm "):
            for argument in self._args(text[len("rm ") :]):
                if argument.startswith("-"):
                    continue
                target = self._absolute(argument)
                self.files.pop(target, None)
                self.directories.discard(target)
            return 0, "", ""
        if text.startswith("touch "):
            for argument in self._args(text[len("touch ") :]):
                target = self._absolute(argument)
                self._ensure_dir(posixpath.dirname(target))
                self.files.setdefault(target, b"")
            return 0, "", ""
        if text.startswith("cat "):
            target = self._absolute(_unquote_word(text[len("cat ") :]))
            if target not in self.files:
                return 1, "", f"cat: {target}: No such file or directory"
            return 0, self.files[target].decode("utf-8", "replace"), ""
        if text == "echo":
            return 0, "\n", ""
        if text.startswith("echo "):
            expanded = self._expand(text[len("echo ") :])
            words = self._args(expanded)
            return 0, " ".join(words) + "\n", ""
        if text.startswith("printf "):
            expanded = self._expand(text[len("printf ") :])
            parts = self._args(expanded)
            if not parts:
                return 0, "", ""
            fmt, values = parts[0], parts[1:]
            rendered: list[str] = []
            value_index = 0
            index = 0
            while index < len(fmt):
                if fmt[index] == "%" and index + 1 < len(fmt):
                    spec = fmt[index + 1]
                    if spec == "s":
                        rendered.append(values[value_index] if value_index < len(values) else "")
                        value_index += 1
                        index += 2
                        continue
                    if spec == "%":
                        rendered.append("%")
                        index += 2
                        continue
                rendered.append(fmt[index])
                index += 1
            return 0, "".join(rendered), ""
        if text.startswith("git ") or text.startswith("'git' ") or text.startswith('"git" '):
            return self._run_git(text)
        if text.startswith("test ") or text.startswith("[ "):
            body = text[len("test ") :] if text.startswith("test ") else text[1:]
            arguments = self._args(body)
            if arguments and arguments[-1] == "]":
                arguments = arguments[:-1]
            if len(arguments) >= 2 and arguments[0] in ("-f", "-d", "-e"):
                flag, value = arguments[0], arguments[1]
                target = self._absolute(value)
                verdict = {
                    "-f": self._is_file(target),
                    "-d": self._is_dir(target),
                    "-e": self._is_file(target) or self._is_dir(target),
                }[flag]
                return (0 if verdict else 1), "", ""
            if arguments and arguments[0] == "-n":
                return (0 if arguments[1:] else 1), "", ""
            return (0 if arguments else 1), "", ""
        return 127, "", f"sh: {text}: command not found"

    # -- git -----------------------------------------------------------------------

    def _run_git(self, text: str) -> tuple[int, str, str]:
        """`git -C <repo> <sub>` against the canned ``git`` table (read-only subset)."""
        args = self._args(text)
        repo = self.cwd or self.home
        sub = ""
        if len(args) >= 4 and args[1] == "-C":
            repo, sub = args[2], args[3]
        elif len(args) >= 2:
            sub = args[1]
        repo = self._absolute(repo)
        if not self._is_dir(repo) or not self._is_dir(posixpath.join(repo, ".git")):
            return 128, "", f"fatal: not a git repository (or any parent): {repo}"
        return 0, self.git.get(sub, ""), ""

    # -- the file-op protocol ------------------------------------------------------

    def _run_op(self, body: str, stdin: str) -> tuple[int, str, str]:
        header, _, _command = body.partition("\n")
        try:
            spec = json.loads(header[len(OP_MARKER_PREFIX) :])
        except (ValueError, IndexError):
            return 127, "", "fake-ssh: malformed remote op"
        op = spec.get("op")
        args = spec.get("args") or {}

        if op == "stat":
            path = posixpath.normpath(str(args.get("path")))
            if self._is_file(path):
                return 0, "f\n", ""
            if self._is_dir(path):
                return 0, "d\n", ""
            if path in self.files or path in self.directories:
                return 0, "e\n", ""
            return 0, "n\n", ""
        if op == "read":
            path = posixpath.normpath(str(args.get("path")))
            if path not in self.files:
                return 1, "", f"base64: {path}: No such file or directory"
            payload = base64.b64encode(self.files[path]).decode("ascii")
            return 0, payload + "\n", ""
        if op == "write":
            path = posixpath.normpath(str(args.get("path")))
            payload = "".join((stdin or "").split())
            try:
                data = base64.b64decode(payload)
            except (ValueError, TypeError) as exc:
                return 1, "", f"base64: invalid input: {exc}"
            self._ensure_dir(posixpath.dirname(path))
            self.files[path] = data
            return 0, "", ""
        if op == "mkdir":
            path = posixpath.normpath(str(args.get("path")))
            self._ensure_dir(path)
            mode = int(args.get("mode", 0o700))
            self.modes[path] = mode
            return 0, "", ""
        if op == "remove":
            path = posixpath.normpath(str(args.get("path")))
            self.files.pop(path, None)
            self.directories.discard(path)
            return 0, "", ""
        if op == "find":
            return self._op_find(args)
        if op == "grep":
            return self._op_grep(args)
        if op == "listdir":
            return self._op_listdir(args)
        return 127, "", f"fake-ssh: unknown remote op {op!r}"

    def _op_find(self, args: dict[str, Any]) -> tuple[int, str, str]:
        base = posixpath.normpath(str(args.get("path")))
        pattern = str(args.get("pattern") or "*")
        recursive = bool(args.get("recursive", True))
        limit = int(args.get("max_results", 100))
        prefix = base.rstrip("/") + "/"
        found: list[str] = []
        for path in sorted(self.files):
            if not path.startswith(prefix):
                continue
            relative = path[len(prefix) :]
            if not recursive and "/" in relative:
                continue
            if pattern and pattern != "*" and not fnmatch.fnmatch(
                posixpath.basename(path), pattern
            ):
                continue
            found.append(path)
            if len(found) >= limit:
                break
        return 0, ("\n".join(found) + "\n") if found else "", ""

    def _op_listdir(self, args: dict[str, Any]) -> tuple[int, str, str]:
        """The picker's listing: resolved base + one immediate subdirectory per line."""
        requested = str(args.get("path") or "")
        target = self._absolute(requested)
        if target in {self._absolute(path) for path in self.denied}:
            return 1, "", f"cd: {requested}: Permission denied"
        if not self._is_dir(target):
            return 2, "", f"cd: {requested}: No such file or directory"
        limit = max(1, int(args.get("limit", 500)))
        prefix = target.rstrip("/") + "/"
        entries = sorted(
            path
            for path in self.directories
            if path.startswith(prefix) and "/" not in path[len(prefix):]
        )
        body = target + "\n" + "".join(path + "\n" for path in entries[: limit + 1])
        return 0, body, ""

    def _op_grep(self, args: dict[str, Any]) -> tuple[int, str, str]:
        base = posixpath.normpath(str(args.get("path")))
        query = str(args.get("query") or "")
        pattern = str(args.get("pattern") or "*")
        includes = [str(g) for g in args.get("includes") or []] or ([pattern] if pattern != "*" else [])
        fold = bool(args.get("ignore_case"))
        limit = int(args.get("max_results", 50))
        prefix = base.rstrip("/") + "/"
        hits: list[str] = []
        for path in sorted(self.files):
            if not (path == base or path.startswith(prefix)):
                continue
            if path != base and includes and not any(
                fnmatch.fnmatch(posixpath.basename(path), g) for g in includes
            ):
                continue
            try:
                text = self.files[path].decode("utf-8")
            except UnicodeDecodeError:
                continue
            for number, line in enumerate(text.splitlines(), start=1):
                if (query.lower() in line.lower()) if fold else (query in line):
                    hits.append(f"{path}:{number}:{line}")
                    if len(hits) >= limit:
                        return 0, "\n".join(hits) + "\n", ""
        if not hits:
            return 1, "", ""
        return 0, "\n".join(hits) + "\n", ""


@dataclass
class FakeSsh:
    platform: str = "Linux"
    arch: str = "x86_64"
    home: str = "/home/dev"
    returncode: int = 0
    stderr: str = ""
    stdout: Optional[str] = None
    #: Raise this from the call instead of returning (e.g. FileNotFoundError, TimeoutExpired).
    error: Optional[BaseException] = None
    calls: list[list[str]] = field(default_factory=list)
    inputs: list[Optional[str]] = field(default_factory=list)
    #: The environment overlay each call was spawned with (askpass handshakes ride it).
    envs: list[Optional[dict[str, str]]] = field(default_factory=list)
    #: When set, non-probe commands execute against this in-memory host.
    host: Optional[FakeRemoteHost] = None

    def __call__(
        self,
        *,
        args: list[str],
        timeout: float = PROBE_TIMEOUT_SECONDS,
        input_text: Optional[str] = None,
        env: Optional[dict[str, str]] = None,
    ) -> CommandResult:
        self.calls.append(list(args))
        self.inputs.append(input_text)
        self.envs.append(dict(env) if env is not None else None)
        if self.error is not None:
            raise self.error
        command = args[-1] if args else ""
        if "uname -s" in command:
            if self.returncode != 0:
                return CommandResult(self.returncode, self.stdout or "", self.stderr)
            stdout = (
                self.stdout
                if self.stdout is not None
                else f"\0{INSPECT_SENTINEL}\0"
                f"{self.host.platform if self.host else self.platform}\0"
                f"{self.host.arch if self.host else self.arch}\0"
                f"{self.host.home if self.host else self.home}\0"
            )
            return CommandResult(0, stdout, self.stderr)
        if self.returncode != 0:
            return CommandResult(self.returncode, self.stdout or "", self.stderr)
        if self.host is not None:
            rc, out, err = self.host.execute(command, input_text or "")
            return CommandResult(rc, out, err)
        stdout = (
            self.stdout
            if self.stdout is not None
            else f"\0{INSPECT_SENTINEL}\0{self.platform}\0{self.arch}\0{self.home}\0"
        )
        return CommandResult(0, stdout, self.stderr)

    @property
    def last_args(self) -> list[str]:
        return self.calls[-1] if self.calls else []

    @property
    def last_destination(self) -> str:
        # argv tail is [...options..., destination, remote command]
        return self.last_args[-2] if len(self.last_args) >= 2 else ""

    @property
    def last_remote_command(self) -> str:
        return self.last_args[-1] if self.last_args else ""

    @property
    def last_input(self) -> Optional[str]:
        return self.inputs[-1] if self.inputs else None

    @property
    def last_env(self) -> Optional[dict[str, str]]:
        return self.envs[-1] if self.envs else None

    # -- canned failures ---------------------------------------------------------
    @classmethod
    def auth_failure(cls) -> "FakeSsh":
        """Key auth refused — the no-password posture's failure."""
        return cls(returncode=255, stderr="Permission denied (publickey).")

    @classmethod
    def host_key_failure(cls) -> "FakeSsh":
        """Unknown or changed host key — StrictHostKeyChecking=yes refused it."""
        return cls(returncode=255, stderr="Host key verification failed.")

    @classmethod
    def unsupported(cls) -> "FakeSsh":
        """A reachable host running something we do not support (Windows/FreeBSD)."""
        return cls(platform="FreeBSD", arch="x86_64")


__all__ = ["FakeRemoteHost", "FakeSsh"]
