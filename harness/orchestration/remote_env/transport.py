"""The shared SSH transport behind the remote execution world.

Phase 1 built profiles and a real Test Connection; this module is the ONE place that
turns a profile into a running remote command. It reuses phase 1's exact posture — the
same ``ssh_argv`` (``BatchMode=yes`` when no password is in play,
``StrictHostKeyChecking=yes``, ``ConnectTimeout``, optional ``-p``/``-i``/
``UserKnownHostsFile``), an argv list and never a shell string, bounded output — and adds
the two things a world needs that a probe does not:

**Sentinel framing.** A remote command's stdout is wrapped between NUL-framed sentinel
markers carrying the exit status and the final ``$PWD``. The sentinel carries a random
per-session token, so command output cannot be mistaken for the frame, and the parser
reads the LAST marker so a trailer that never arrived (a command that ran ``exit``) is
reported honestly rather than guessed at.

**Typed failures.** Spawn-level failures (ssh missing, wall-clock timeout, connection
refused, auth refused, host key refused, unsupported platform) become typed exceptions
with the phase-1 message vocabulary. ``RemoteExecutor`` maps them to the same
``{"error": ...}`` strings our local shell returns, so no consumer learns a second
failure vocabulary.

**Password auth.** When the caller supplies a password (transient or remembered, resolved
by the session host), every call runs under ``askpass_environment``: a fresh one-shot
0600 helper/secret pair in the private state dir, passed as an environment overlay and
deleted in a ``finally``. The password is never an argv entry and never outlives the call.

The subprocess call stays behind the phase-1 runner seam (``runner(*, args, timeout,
input_text=None, env=None)``): tests inject ``harness.testing.fake_ssh.FakeSsh`` and never
spawn a real ssh. ``input_text`` is what makes binary-safe remote writes possible — base64
travels to the remote command's stdin instead of through a command line, so a written
file never has to fit in argv.
"""

from __future__ import annotations

import json
import subprocess
import uuid
from typing import Any, NamedTuple, Optional

from ..remote_environments import (
    DEFAULT_CONNECT_TIMEOUT_SECONDS,
    PROBE_TIMEOUT_SECONDS,
    Runner,
    askpass_environment,
    classify_connection_error,
    configured_known_hosts_file,
    inspect_command,
    parse_inspection,
    run_ssh,
    shell_quote,
    ssh_argv,
    ssh_destination,
)

#: Marks a remote run's frame; the random suffix makes command output colliding with
#: the marker practically impossible.
RUN_SENTINEL_PREFIX = "REEVE_REMOTE_RUN_V1"

#: Default budget for one remote command (matches the local shell's default).
DEFAULT_COMMAND_TIMEOUT_SECONDS = 120.0

#: The file-op protocol marker. It is a shell COMMENT, so the remote shell ignores it,
#: while the test double can dispatch on it without emulating a POSIX parser.
OP_MARKER_PREFIX = "# reeve-remote-op "

SSH_MISSING_MESSAGE = (
    "The system ssh client was not found. Install OpenSSH so `ssh` is on PATH, then "
    "try again."
)


# -- typed failures -------------------------------------------------------------------


class RemoteEnvError(RuntimeError):
    """Base for every typed remote-environment failure. The message IS the tool error."""

    kind = "remote"

    @property
    def tool_error(self) -> str:
        return str(self)


class RemoteAuthError(RemoteEnvError):
    kind = "auth"


class RemoteHostKeyError(RemoteEnvError):
    kind = "host_key"


class RemoteTimeoutError(RemoteEnvError):
    kind = "timeout"


class RemoteSshMissingError(RemoteEnvError):
    kind = "ssh_missing"


class RemoteUnsupportedError(RemoteEnvError):
    kind = "unsupported"


class RemoteProtocolError(RemoteEnvError):
    kind = "protocol"


class RemoteConnectionError(RemoteEnvError):
    kind = "connection"


class RemoteCommandError(RemoteEnvError):
    """The remote command ran and failed (non-zero exit, or no frame at all)."""

    kind = "command"

    def __init__(
        self, message: str, *, exit_code: Optional[int] = None, output: str = ""
    ) -> None:
        super().__init__(message)
        self.exit_code = exit_code
        self.output = output


_KIND_TYPES: dict[str, type[RemoteEnvError]] = {
    "auth": RemoteAuthError,
    "host_key": RemoteHostKeyError,
    "refused": RemoteConnectionError,
    "dns": RemoteConnectionError,
    "unreachable": RemoteConnectionError,
    "timeout": RemoteTimeoutError,
    "connection": RemoteConnectionError,
}


# -- framing --------------------------------------------------------------------------


class Frame(NamedTuple):
    exit_code: Optional[int]
    cwd: Optional[str]
    output: str
    found: bool


def frame_command(command: str, sentinel: str) -> str:
    """Wrap ``command`` in the sentinel frame, executed by the remote POSIX shell.

    The frame is three statements in one ``sh -c`` script: print the opening marker,
    run the command, capture its status, print the closing marker with status and
    ``$PWD``. Each marker is NUL-delimited so no line-oriented parsing is involved.
    """
    script = (
        f"printf '\\000{sentinel}\\000'\n"
        f"{command}\n"
        f"__reeve_rc=$?\n"
        f"printf '\\000{sentinel}\\000%s\\000%s\\000' \"$__reeve_rc\" \"$PWD\"\n"
    )
    return f"sh -c {shell_quote(script)}"


def parse_frame(stdout: str, sentinel: str) -> Frame:
    """Pull ``(exit_code, cwd, output)`` out of framed stdout.

    The closing marker is found with ``rfind`` so command output that happens to contain
    the opening token cannot truncate the body. No closing marker means the command never
    reached the trailer (it closed the shell): ``found=False`` and the exit status stays
    unknown rather than being invented.
    """
    token = f"\x00{sentinel}\x00"
    begin = stdout.find(token)
    if begin < 0:
        return Frame(None, None, stdout, False)
    tail = stdout.rfind(token)
    if tail == begin:
        return Frame(None, None, stdout[begin + len(token):], False)
    body = stdout[begin + len(token):tail]
    fields = stdout[tail + len(token):].split("\x00")
    exit_code: Optional[int] = None
    if fields and fields[0].strip().lstrip("-").isdigit():
        exit_code = int(fields[0].strip())
    cwd = fields[1].strip() if len(fields) > 1 else None
    if cwd and not cwd.startswith("/"):
        cwd = None
    return Frame(exit_code, cwd, body, True)


def op_script(op: str, args: dict[str, Any], *, command: str) -> str:
    """Prefix a remote file operation with a machine-readable comment.

    The comment is inert to the remote shell; it exists so ``FakeRemoteHost`` can serve
    the exact same command strings a real host would receive, without shipping a POSIX
    shell emulator. The shell command below it is the real implementation.
    """
    header = OP_MARKER_PREFIX + json.dumps({"op": op, "args": args}, sort_keys=True)
    return f"{header}\n{command}"


class RemoteCommandResult(NamedTuple):
    command: str
    exit_code: Optional[int]
    output: str
    stderr: str
    cwd: Optional[str]
    returncode: int
    frame_found: bool


# -- the session ----------------------------------------------------------------------


class RemoteSession:
    """One configured SSH destination: argv posture, framed runs, typed failures.

    ``session_id`` is NOT part of the transport contract (it names scratch state, which
    the executor owns); the session holds only the destination and the crypto posture.
    """

    def __init__(
        self,
        profile: dict[str, Any],
        *,
        runner: Optional[Runner] = None,
        known_hosts_file: Optional[str] = None,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT_SECONDS,
        command_timeout: float = DEFAULT_COMMAND_TIMEOUT_SECONDS,
        ssh_path: Optional[str] = None,
        password: Optional[str] = None,
    ) -> None:
        self.profile = dict(profile or {})
        self.destination = ssh_destination(self.profile)
        if not self.destination:
            raise ValueError("SSH host is required.")
        self._runner: Runner = runner or run_ssh
        # `None` means "use the configured env value if any" (phase 1's rule); a caller
        # that passes a path forces UserKnownHostsFile exactly as test_connection does.
        self._known_hosts_file = (
            known_hosts_file
            if known_hosts_file is not None
            else configured_known_hosts_file()
        )
        self._connect_timeout = connect_timeout
        self._ssh_path = ssh_path
        # The transient/remembered password for this session's ssh calls. It rides the
        # askpass environment of each call and is never an argv entry; None keeps the
        # phase-1 `BatchMode=yes` posture byte-identical.
        self._password = password or None
        self.command_timeout = command_timeout
        self.sentinel = f"{RUN_SENTINEL_PREFIX}_{uuid.uuid4().hex}"
        # Filled by inspect(); the executor's scratch path and `~` expansion need HOME.
        self.home: Optional[str] = None
        self.platform: Optional[str] = None
        self.arch: Optional[str] = None

    # -- argv ----------------------------------------------------------------------
    def argv(self, remote_command: str) -> list[str]:
        """The exact argv for one remote command — one place to audit the posture."""
        return [
            *ssh_argv(
                self.profile,
                known_hosts_file=self._known_hosts_file,
                connect_timeout=self._connect_timeout,
                ssh_path=self._ssh_path,
                password=self._password is not None,
            ),
            self.destination,
            remote_command,
        ]

    def _call(
        self, args: list[str], timeout: float, input_text: Optional[str]
    ):
        # The runner seam predates phase 2 and may only accept (args, timeout); only a
        # caller that actually has stdin data or an askpass overlay asks for the extra
        # keywords. A password call gets a fresh one-shot helper, deleted in its finally.
        if self._password is None:
            if input_text is None:
                return self._runner(args=args, timeout=timeout)
            return self._runner(args=args, timeout=timeout, input_text=input_text)
        with askpass_environment(self._password) as env:
            if input_text is None:
                return self._runner(args=args, timeout=timeout, env=env)
            return self._runner(
                args=args, timeout=timeout, input_text=input_text, env=env
            )

    def _timeout_message(self, seconds: float) -> str:
        return (
            f"Timed out after {int(seconds)}s waiting for "
            f"{self.destination or 'the host'}. Check the address, port, and network, "
            "then try again."
        )

    def _connection_failure(self, stderr: str, stdout: str, returncode: int) -> RemoteEnvError:
        kind, message = classify_connection_error(
            self.profile, stderr, stdout, returncode, password=self._password is not None
        )
        return _KIND_TYPES.get(kind, RemoteConnectionError)(message)

    # -- runs ----------------------------------------------------------------------
    def run(
        self,
        command: str,
        *,
        timeout: Optional[float] = None,
        input_text: Optional[str] = None,
    ) -> RemoteCommandResult:
        """Run one command over SSH and return the framed result.

        Raises a typed ``RemoteEnvError`` for spawn/connection failures; a command that
        ran returns its own exit status (including non-zero) as data.
        """
        budget = (
            float(timeout)
            if isinstance(timeout, (int, float)) and timeout > 0
            else self.command_timeout
        )
        argv = self.argv(frame_command(command, self.sentinel))
        try:
            result = self._call(argv, budget, input_text)
        except subprocess.TimeoutExpired:
            raise RemoteTimeoutError(self._timeout_message(budget)) from None
        except FileNotFoundError:
            raise RemoteSshMissingError(SSH_MISSING_MESSAGE) from None
        except OSError as exc:
            raise RemoteConnectionError(f"Could not start ssh: {exc}") from None

        returncode = int(getattr(result, "returncode", 1) or 0)
        stdout = str(getattr(result, "stdout", "") or "")
        stderr = str(getattr(result, "stderr", "") or "")
        frame = parse_frame(stdout, self.sentinel)
        if not frame.found and returncode != 0:
            # ssh itself failed before the remote command could report anything.
            raise self._connection_failure(stderr, stdout, returncode)
        return RemoteCommandResult(
            command=command,
            exit_code=frame.exit_code,
            output=frame.output,
            stderr=stderr,
            cwd=frame.cwd,
            returncode=returncode,
            frame_found=frame.found,
        )

    def run_text(self, command: str, *, timeout: Optional[float] = None) -> str:
        """Run a command that must succeed, returning its output or raising."""
        result = self.run(command, timeout=timeout)
        if result.exit_code != 0:
            detail = (result.stderr or result.output or "").strip()
            detail = detail[:500] + ("…" if len(detail) > 500 else "")
            raise RemoteCommandError(
                f"remote command failed (exit {result.exit_code}): {detail or command}",
                exit_code=result.exit_code,
                output=result.output,
            )
        return result.output

    # -- the phase-1 compatibility probe --------------------------------------------
    def inspect(self, *, timeout: float = PROBE_TIMEOUT_SECONDS) -> dict[str, Any]:
        """Run phase 1's probe and require the same supported target.

        Raises the typed error for each failure mode instead of returning a dict: a
        world must not be built against a host whose compatibility is unknown.
        """
        argv = self.argv(inspect_command())
        try:
            result = self._call(argv, timeout, None)
        except subprocess.TimeoutExpired:
            raise RemoteTimeoutError(self._timeout_message(timeout)) from None
        except FileNotFoundError:
            raise RemoteSshMissingError(SSH_MISSING_MESSAGE) from None
        except OSError as exc:
            raise RemoteConnectionError(f"Could not start ssh: {exc}") from None
        returncode = int(getattr(result, "returncode", 1) or 0)
        stdout = str(getattr(result, "stdout", "") or "")
        stderr = str(getattr(result, "stderr", "") or "")
        if returncode != 0:
            raise self._connection_failure(stderr, stdout, returncode)
        parsed = parse_inspection(stdout)
        if not parsed.get("ok"):
            error = str(parsed.get("error") or "The SSH probe failed.")
            if "unsupported" in error.lower():
                raise RemoteUnsupportedError(error)
            raise RemoteProtocolError(error)
        self.home = str(parsed["home"])
        self.platform = str(parsed["platform"])
        self.arch = str(parsed["arch"])
        return parsed

    def ensure_home(self) -> str:
        """The remote HOME, probing once if ``inspect`` has not run."""
        if self.home:
            return self.home
        output = self.run('printf %s "$HOME"').output
        home = output.strip()
        if not home.startswith("/") or "\n" in home:
            raise RemoteProtocolError(
                "The SSH host returned an invalid HOME directory; expected an absolute "
                "POSIX path."
            )
        self.home = home
        return home


__all__ = [
    "DEFAULT_COMMAND_TIMEOUT_SECONDS",
    "Frame",
    "OP_MARKER_PREFIX",
    "RUN_SENTINEL_PREFIX",
    "RemoteAuthError",
    "RemoteCommandError",
    "RemoteCommandResult",
    "RemoteConnectionError",
    "RemoteEnvError",
    "RemoteHostKeyError",
    "RemoteProtocolError",
    "RemoteSession",
    "RemoteSshMissingError",
    "RemoteTimeoutError",
    "RemoteUnsupportedError",
    "frame_command",
    "op_script",
    "parse_frame",
]
