"""SSH connection profiles and the honest connection test (phase 1 of remote environments).

Phase 1 is deliberately only the management layer: profiles are LOCAL configuration, and
"Test Connection" runs the real system `ssh` as a probe. Nothing here starts a session or
runs a tool on the host — a save, or a passing test, never implies remote execution (the
`RemoteWorld` that does is phase 2; the seam it plugs into is the execution world).

Profiles live in ``<state dir>/remote-environments.json`` (0600, atomic temp+replace via
the SecretStore writer). A profile holds only a destination and the PATH of an identity
file: **no passwords and no private keys are ever stored in it**. ``~`` in the identity
path is expanded locally, at invocation time.

Password auth (password-auth update) works through an askpass helper, never
through argv and never through the profile file: ``askpass_environment`` writes a tiny
helper plus its secret file (0600 / user-ACL, in the private state dir), sets
``SSH_ASKPASS`` + ``SSH_ASKPASS_REQUIRE=force`` (and a ``DISPLAY`` placeholder for older
builds) in the child's environment only, and deletes both files in a ``finally``. When a
password is in play ``BatchMode=yes`` is omitted, because that option is exactly what
refuses the prompt askpass answers. A password the user chose to remember lives in the
OS credential store (``harness.config.credentials``), and the profile keeps only
``remembered: true``.

``test_connection`` spawns the system ``ssh`` with an argv list (never a shell) carrying
``StrictHostKeyChecking=yes`` (plus ``BatchMode=yes`` when no password is supplied), and
runs a sentinel-framed probe (``uname -s``, ``uname -m``, ``$HOME``) that must report
Linux/macOS on x64/arm64 with an absolute HOME. The subprocess call is the one impure
edge, so it sits behind a runner seam — ``runner(*, args, timeout, input_text=None,
env=None) -> CommandResult`` — which tests replace with the fake in
``harness/testing/fake_ssh.py`` and never touch the network (phase 2's remote file
writes feed base64 through ``input_text``).

The on-disk shape is ``{"version": 1, "profiles": [...]}``; the phase 2
plan is to reach that behaviour through the execution-world seam, not to copy another tool's
transport. A torn or foreign profiles file degrades to "no profiles" — a broken settings
file must never cost a session.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import subprocess
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, NamedTuple, Optional

from harness.config.credentials import CredentialStore, CredentialStoreUnavailable, credential_target, default_credential_store
from harness.safety.secrets import state_dir, write_private_text
from harness.platform.spawn import run as spawn_run

PROFILES_FILE_NAME = "remote-environments.json"
PROFILE_FILE_VERSION = 1
DEFAULT_SSH_PORT = 22
DEFAULT_CONNECT_TIMEOUT_SECONDS = 10
#: Wall-clock ceiling for the whole probe. `ConnectTimeout` only bounds the TCP connect;
#: a host that accepts and then stalls must still not hang the settings page.
PROBE_TIMEOUT_SECONDS = 30
INSPECT_SENTINEL = "REEVE_REMOTE_INSPECT_V1"
MAX_ERROR_CHARS = 2000
MAX_NAME_CHARS = 120
MAX_HOST_CHARS = 255
#: Prefix of the one-shot askpass helper + secret files in the private state dir.
ASKPASS_HELPER_PREFIX = ".askpass-"

_USER_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_UNSAFE_TEXT_RE = re.compile(r"[\0\r\n]")
_WHITESPACE_RE = re.compile(r"\s")
_SUPPORTED_PLATFORMS = {"linux", "darwin"}
_SUPPORTED_ARCHS = {"x64", "arm64"}


class CommandResult(NamedTuple):
    """What a runner returns: the system ssh's exit code and captured streams."""

    returncode: int
    stdout: str
    stderr: str


Runner = Callable[..., CommandResult]


# -- configuration -------------------------------------------------------------------


def profiles_path() -> Path:
    """`<state dir>/remote-environments.json` — the one canonical location."""
    return state_dir() / PROFILES_FILE_NAME


def ssh_binary() -> str:
    """The system ssh to invoke; `REEVE_SSH_PATH` overrides the PATH lookup."""
    return (os.environ.get("REEVE_SSH_PATH") or "ssh").strip() or "ssh"


def configured_known_hosts_file() -> Optional[str]:
    """Optional `REEVE_SSH_KNOWN_HOSTS_FILE`; never invented, only passed through."""
    value = (os.environ.get("REEVE_SSH_KNOWN_HOSTS_FILE") or "").strip()
    return value or None


def run_ssh(
    *,
    args: list[str],
    timeout: float,
    input_text: Optional[str] = None,
    env: Optional[dict[str, str]] = None,
) -> CommandResult:
    """The runner seam: spawn the system ssh with an argv list and NO shell.

    Spawn-level failures (missing binary, timeout) are raised and translated by
    ``test_connection``; a non-zero exit is returned so the caller can classify it.
    ``input_text`` feeds the remote command's stdin (phase 2's binary-safe file writes
    pipe base64 through it); when absent stdin is closed so no remote command can read
    the controller's terminal. ``env`` is an OVERLAY on the inherited environment — the
    askpass handshake rides it, so the password never becomes process-global state.
    """
    kwargs: dict[str, Any] = {}
    if input_text is None:
        kwargs["stdin"] = subprocess.DEVNULL
    if env:
        kwargs["env"] = {**os.environ, **env}
    proc = spawn_run(
        args,
        input=input_text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        shell=False,  # explicit: argv is a list and must stay one
        **kwargs,
    )
    return CommandResult(
        returncode=int(proc.returncode or 0),
        stdout=proc.stdout or "",
        stderr=proc.stderr or "",
    )


@contextlib.contextmanager
def askpass_environment(
    password: str, *, directory: Optional[str | Path] = None
) -> Iterator[dict[str, str]]:
    """Write a one-shot askpass helper and yield ssh's environment overlay.

    ssh reads a password by executing ``$SSH_ASKPASS`` and reading its stdout; the
    password itself never appears in any argv (the helper's argv is just the prompt) and
    never in a shell string. ``SSH_ASKPASS_REQUIRE=force`` makes modern builds use the
    helper even with a terminal attached, and a non-empty ``DISPLAY`` keeps older builds
    on the same path when our child has no tty. Both files are 0600/owner-ACL in the
    private state dir and are removed in a ``finally``, however the ssh call ends.

    The helper only points AT the secret file rather than containing the secret, so a
    stale helper is still not a copied password.
    """
    token = uuid.uuid4().hex
    base = Path(directory).expanduser() if directory else state_dir()
    base.mkdir(parents=True, exist_ok=True)
    secret_path = base / f"{ASKPASS_HELPER_PREFIX}{token}.secret"
    helper_name = f"{ASKPASS_HELPER_PREFIX}{token}" + (".cmd" if os.name == "nt" else ".sh")
    helper_path = base / helper_name
    write_private_text(secret_path, password)
    if os.name == "nt":
        # `%~dp0` resolves the script's own directory at run time, so an exotic state
        # path never has to be quoted into the batch text.
        body = f'@echo off\r\ntype "%~dp0{secret_path.name}"\r\n'
    else:
        body = f"#!/bin/sh\nexec cat {shell_quote(str(secret_path))}\n"
    write_private_text(helper_path, body)
    if os.name != "nt":
        os.chmod(helper_path, 0o700)
    overlay = {
        "SSH_ASKPASS": str(helper_path),
        "SSH_ASKPASS_REQUIRE": "force",
        "DISPLAY": os.environ.get("DISPLAY") or "reeve-askpass",
    }
    try:
        yield overlay
    finally:
        for path in (helper_path, secret_path):
            try:
                path.unlink()
            except OSError:
                pass



# -- validation ----------------------------------------------------------------------


def _required_text(value: Any, field: str, *, limit: int) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} is required.")
    if len(text) > limit:
        raise ValueError(f"{field} is too long (max {limit} characters).")
    if _UNSAFE_TEXT_RE.search(text):
        raise ValueError(f"{field} cannot contain newlines or NUL bytes.")
    return text


def validate_name(value: Any) -> str:
    return _required_text(value, "Name", limit=MAX_NAME_CHARS)


def validate_host(value: Any) -> str:
    host = _required_text(value, "SSH host", limit=MAX_HOST_CHARS)
    if host.startswith("-") or _WHITESPACE_RE.search(host):
        raise ValueError(
            "SSH host must be a hostname, address, or SSH config alias without whitespace."
        )
    return host


def validate_user(value: Any) -> Optional[str]:
    user = str(value or "").strip()
    if not user:
        return None
    if user.startswith("-") or not _USER_RE.match(user):
        raise ValueError("SSH user contains unsupported characters.")
    return user


def validate_port(value: Any) -> int:
    if value is None or value == "":
        return DEFAULT_SSH_PORT
    if isinstance(value, bool):
        raise ValueError("SSH port must be an integer between 1 and 65535.")
    try:
        port = int(value)
    except (TypeError, ValueError):
        raise ValueError("SSH port must be an integer between 1 and 65535.") from None
    if not 1 <= port <= 65535:
        raise ValueError("SSH port must be an integer between 1 and 65535.")
    return port


def validate_identity_file(value: Any) -> Optional[str]:
    path = str(value or "").strip()
    if not path:
        return None
    if _UNSAFE_TEXT_RE.search(path):
        raise ValueError("SSH identity file cannot contain newlines or NUL bytes.")
    return path


def validate_password(value: Any) -> Optional[str]:
    """A transient password is passed through verbatim; empty means "none supplied".

    Spaces are deliberately NOT stripped — a password is opaque bytes to us. ssh reads
    one line from askpass, so newlines (and NUL) cannot be represented and are refused
    rather than silently truncated.
    """
    if value is None:
        return None
    text = str(value)
    if not text:
        return None
    if _UNSAFE_TEXT_RE.search(text):
        raise ValueError("SSH password cannot contain newlines or NUL bytes.")
    return text


# -- ssh argv (the security posture lives here) --------------------------------------


def ssh_destination(profile: dict[str, Any]) -> str:
    user = str(profile.get("user") or "").strip()
    host = str(profile.get("host") or "").strip()
    return f"{user}@{host}" if user else host


def ssh_argv(
    profile: dict[str, Any],
    *,
    known_hosts_file: Optional[str] = None,
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT_SECONDS,
    ssh_path: Optional[str] = None,
    password: bool = False,
) -> list[str]:
    """The exact argv prefix every probe uses — one place to audit the posture.

    `BatchMode=yes` refuses password/keyboard-interactive prompts, so it is passed only
    when NO password is in play; a password rides the askpass environment instead (it
    must never be an argv entry). `StrictHostKeyChecking=yes` refuses an unknown or
    changed host key, with or without a password. `-p` is only passed when the port is
    not 22, so an SSH config alias's own `Port` is not shadowed by our default.
    `UserKnownHostsFile` only ever passes through when configured explicitly (env or
    caller).
    """
    argv = [ssh_path or ssh_binary()]
    if not password:
        argv += ["-o", "BatchMode=yes"]
    argv += [
        "-o",
        f"ConnectTimeout={int(connect_timeout)}",
        "-o",
        "StrictHostKeyChecking=yes",
    ]
    port = validate_port(profile.get("port"))
    if port != DEFAULT_SSH_PORT:
        argv += ["-p", str(port)]
    if known_hosts_file:
        argv += ["-o", f"UserKnownHostsFile={known_hosts_file}"]
    identity = profile.get("identity_file")
    if identity:
        argv += ["-i", str(Path(str(identity)).expanduser())]
    return argv


def shell_quote(value: str) -> str:
    """POSIX single-quote a string so a shell parses it back as one literal word."""
    return "'" + value.replace("'", "'\"'\"'") + "'"


def inspect_command() -> str:
    """The sentinel-framed remote probe: `sh -c` so the login shell's syntax is irrelevant."""
    script = (
        f"printf '\\000{INSPECT_SENTINEL}\\000%s\\000%s\\000%s\\000' "
        '"$(uname -s)" "$(uname -m)" "$HOME"'
    )
    return f"sh -c {shell_quote(script)}"


def normalize_platform(value: Optional[str]) -> Optional[str]:
    low = (value or "").strip().lower()
    return low if low in _SUPPORTED_PLATFORMS else None


def normalize_arch(value: Optional[str]) -> Optional[str]:
    low = (value or "").strip().lower()
    if low in ("x86_64", "amd64"):
        return "x64"
    if low in ("aarch64", "arm64"):
        return "arm64"
    return None


def parse_inspection(stdout: str) -> dict[str, Any]:
    """Pull `(platform, arch, home)` out of the framed probe output.

    The frame is `\\0SENTINEL\\0<uname -s>\\0<uname -m>\\0$HOME\\0`; anything else (a
    shell that mangled it, a non-POSIX login shell, a foreign banner) is refused with
    an actionable error instead of guessed at.
    """
    fields = stdout.split("\0")
    index = -1
    for position in range(len(fields) - 1, -1, -1):
        if fields[position].strip() == INSPECT_SENTINEL:
            index = position
            break
    if (
        index < 0
        or index + 4 >= len(fields)
        or fields[index + 4].strip() != ""
    ):
        return {
            "ok": False,
            "error": (
                "The SSH host returned an invalid probe payload. Check that the host "
                "has a POSIX shell and that nothing interferes with the login session."
            ),
        }
    raw_platform = fields[index + 1].strip()
    raw_arch = fields[index + 2].strip()
    home = fields[index + 3]
    platform = normalize_platform(raw_platform)
    arch = normalize_arch(raw_arch)
    if platform is None or arch is None:
        return {
            "ok": False,
            "error": (
                f"Remote target {raw_platform or 'unknown'}/{raw_arch or 'unknown'} is "
                "unsupported; only Linux and macOS on x64 or arm64 are supported."
            ),
        }
    if not home.startswith("/") or _UNSAFE_TEXT_RE.search(home):
        return {
            "ok": False,
            "error": "The SSH host returned an invalid HOME directory; expected an absolute POSIX path.",
        }
    return {"ok": True, "platform": platform, "arch": arch, "home": home, "error": None}


def _bounded(text: str, limit: int = MAX_ERROR_CHARS) -> str:
    """Strip sentinel noise, collapse whitespace runs, and cap the length."""
    clean = (text or "").replace(INSPECT_SENTINEL, "")
    clean = "\n".join(line.strip() for line in clean.splitlines() if line.strip())
    if len(clean) > limit:
        clean = clean[:limit].rstrip() + "…"
    return clean


def _failure(error: str) -> dict[str, Any]:
    return {"ok": False, "platform": None, "arch": None, "home": None, "error": error}


def classify_connection_error(
    profile: dict[str, Any],
    stderr: str,
    stdout: str,
    returncode: int,
    *,
    password: bool = False,
) -> tuple[str, str]:
    """Classify ssh's own diagnostics into `(kind, person-readable reason)`.

    The kind is the machine-stable half phase 2 maps onto typed errors (``auth``,
    ``host_key``, ``refused``, ``dns``, ``unreachable``, ``timeout``, ``connection``);
    the message is the exact vocabulary phase 1 already shows, so both layers speak the
    same refusal strings. ``password=True`` sharpens the auth message: the user tried a
    password, so "check the password" is the actionable half.
    """
    destination = ssh_destination(profile) or "the host"
    combined = f"{stderr}\n{stdout}"
    low = combined.lower()
    detail = _bounded(combined)

    if (
        "remote host identification has changed" in low
        or "host key verification failed" in low
        or "no matching host key" in low
    ):
        return (
            "host_key",
            f"The host key for {destination} is not trusted or has changed. "
            "Add it to your SSH known_hosts (or remove the stale entry), then try again."
            + (f"\nssh said: {detail}" if detail else ""),
        )
    if "permission denied" in low or "authentication failed" in low:
        if password:
            return (
                "auth",
                f"Authentication failed (password) for {destination}. Check the "
                "password and try again."
                + (f"\nssh said: {detail}" if detail else ""),
            )
        return (
            "auth",
            f"Authentication failed for {destination}. Add your public key to the "
            "host's authorized_keys, save a password for this host in Settings, or "
            "point the profile at the right identity file."
            + (f"\nssh said: {detail}" if detail else ""),
        )
    if "connection refused" in low:
        return (
            "refused",
            f"{destination} refused the connection. Check the SSH port and that sshd "
            "is running on the host."
            + (f"\nssh said: {detail}" if detail else ""),
        )
    if "could not resolve hostname" in low:
        return (
            "dns",
            f"Could not resolve the SSH host for {destination}. Check the host name."
            + (f"\nssh said: {detail}" if detail else ""),
        )
    if "no route to host" in low or "network is unreachable" in low:
        return (
            "unreachable",
            f"{destination} is unreachable. Check the address and your network."
            + (f"\nssh said: {detail}" if detail else ""),
        )
    if "timed out" in low or "timeout" in low:
        return (
            "timeout",
            f"Timed out connecting to {destination}. Check the address, port, and network."
            + (f"\nssh said: {detail}" if detail else ""),
        )
    return (
        "connection",
        f"ssh could not connect to {destination} (exit {returncode})."
        + (f"\nssh said: {detail}" if detail else ""),
    )


def _connection_error(
    profile: dict[str, Any],
    stderr: str,
    stdout: str,
    returncode: int,
    *,
    password: bool = False,
) -> str:
    """A person-readable reason, classified from ssh's own diagnostics."""
    return classify_connection_error(
        profile, stderr, stdout, returncode, password=password
    )[1]


def test_connection(
    profile: dict[str, Any],
    *,
    runner: Optional[Runner] = None,
    known_hosts_file: Optional[str] = None,
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT_SECONDS,
    password: Optional[str] = None,
) -> dict[str, Any]:
    """Probe one profile with the system ssh. Always returns `{ok, platform, arch, home, error}`.

    Never raises for an unreachable or misconfigured host: the failure is the payload.
    The caller decides how to present it. `known_hosts_file=None` means "use the
    configured env value if any"; passing a path forces `UserKnownHostsFile`.
    `password` is TRANSIENT: it lives only in the askpass helper file for the duration
    of the call (never argv, never the profile), and is removed before this returns.
    """
    resolved_password = validate_password(password)
    resolved_known_hosts = (
        known_hosts_file if known_hosts_file is not None else configured_known_hosts_file()
    )
    argv = ssh_argv(
        profile,
        known_hosts_file=resolved_known_hosts,
        connect_timeout=connect_timeout,
        password=resolved_password is not None,
    )
    argv += [ssh_destination(profile), inspect_command()]
    run = runner or run_ssh
    askpass: Any = (
        askpass_environment(resolved_password)
        if resolved_password is not None
        else contextlib.nullcontext(None)
    )
    try:
        with askpass as askpass_env:
            kwargs: dict[str, Any] = {"args": argv, "timeout": PROBE_TIMEOUT_SECONDS}
            if askpass_env:
                kwargs["env"] = askpass_env
            result = run(**kwargs)
    except subprocess.TimeoutExpired:
        return _failure(
            f"Timed out after {PROBE_TIMEOUT_SECONDS}s waiting for "
            f"{ssh_destination(profile) or 'the host'}. Check the address, port, and "
            "network, then try again."
        )
    except FileNotFoundError:
        return _failure(
            "The system ssh client was not found. Install OpenSSH so `ssh` is on "
            "PATH, then try again."
        )
    except OSError as exc:
        return _failure(f"Could not start ssh: {exc}")

    returncode = int(getattr(result, "returncode", 1) or 0)
    stdout = str(getattr(result, "stdout", "") or "")
    stderr = str(getattr(result, "stderr", "") or "")
    if returncode != 0:
        return _failure(
            _connection_error(
                profile,
                stderr,
                stdout,
                returncode,
                password=resolved_password is not None,
            )
        )
    parsed = parse_inspection(stdout)
    if not parsed.get("ok"):
        return _failure(str(parsed.get("error") or "The SSH probe failed."))
    return {
        "ok": True,
        "platform": parsed["platform"],
        "arch": parsed["arch"],
        "home": parsed["home"],
        "error": None,
    }


# Pytest must not collect the module-level probe as a test when tests import it.
test_connection.__test__ = False  # type: ignore[attr-defined]


# -- the store -----------------------------------------------------------------------


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _profile_from_input(payload: dict[str, Any]) -> dict[str, Any]:
    now = _now()
    return {
        "id": str(uuid.uuid4()),
        "name": validate_name(payload.get("name")),
        "host": validate_host(payload.get("host")),
        "user": validate_user(payload.get("user")),
        "port": validate_port(payload.get("port", DEFAULT_SSH_PORT)),
        "identity_file": validate_identity_file(payload.get("identity_file")),
        "created_at": now,
        "updated_at": now,
    }


def _apply_patch(profile: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    updated = dict(profile)
    if "name" in patch:
        updated["name"] = validate_name(patch["name"])
    if "host" in patch:
        updated["host"] = validate_host(patch["host"])
    if "user" in patch:
        updated["user"] = validate_user(patch["user"])
    if "port" in patch:
        updated["port"] = validate_port(patch["port"])
    if "identity_file" in patch:
        updated["identity_file"] = validate_identity_file(patch["identity_file"])
    updated["updated_at"] = _now()
    return updated


def _normalize_loaded(item: Any) -> Optional[dict[str, Any]]:
    """Coerce one stored profile into the canonical shape, or drop it.

    The canonical shape has no password slot at all: even a foreign/torn file that
    somehow carries a `password` key loses it here, so nothing downstream can echo it.
    """
    if not isinstance(item, dict):
        return None
    environment_id = item.get("id")
    if not isinstance(environment_id, str) or not environment_id.strip():
        return None
    try:
        return {
            "id": environment_id,
            "name": validate_name(item.get("name")),
            "host": validate_host(item.get("host")),
            "user": validate_user(item.get("user")),
            "port": validate_port(item.get("port", DEFAULT_SSH_PORT)),
            "identity_file": validate_identity_file(item.get("identity_file")),
            "remembered": bool(item.get("remembered")),
            "created_at": str(item.get("created_at") or ""),
            "updated_at": str(item.get("updated_at") or ""),
        }
    except ValueError:
        return None


class RemoteEnvironmentStore:
    """File-backed CRUD for SSH profiles + the probe, behind the runner seam.

    Passwords enter through `create`/`update`/`test_connection` and leave through the
    credential seam (`harness.config.credentials`): the JSON holds only `remembered`, the
    wire shape holds only `has_password`/`remembered`, and `load_password` is the one
    door a session build uses to reach a remembered secret.
    """

    def __init__(
        self,
        path: Optional[str | Path] = None,
        *,
        runner: Optional[Runner] = None,
        known_hosts_file: Optional[str] = None,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT_SECONDS,
        credentials: Optional[CredentialStore] = None,
    ) -> None:
        self.path = Path(path).expanduser() if path else profiles_path()
        self._runner = runner
        self._known_hosts_file = known_hosts_file
        self._connect_timeout = connect_timeout
        self._credentials: CredentialStore = credentials or default_credential_store()
        self._lock = threading.Lock()

    # -- reads ------------------------------------------------------------------
    def list(self) -> list[dict[str, Any]]:
        """Every profile, sorted by display name, freshest file read."""
        profiles = self._read()
        ordered = sorted(
            profiles, key=lambda p: (str(p.get("name", "")).lower(), str(p.get("id", "")))
        )
        return [self._public(profile) for profile in ordered]

    def get(self, environment_id: str) -> dict[str, Any]:
        """One profile. Raises `KeyError` for an unknown id (the routes map it to 404)."""
        return self._public(self._find(environment_id))

    def load_password(self, environment_id: str) -> Optional[str]:
        """The remembered password for a session build, or None when none is set.

        Raises `CredentialStoreUnavailable` (a ValueError the session-start path already
        knows how to surface) when the profile says it remembers one but the OS store
        cannot be read — a silent fallback to key auth would misreport why.
        """
        return self._remembered_password(self._find(environment_id))

    # -- writes -----------------------------------------------------------------
    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            profile = _profile_from_input(payload or {})
            password = validate_password((payload or {}).get("password"))
            remember = bool((payload or {}).get("remember"))
            if remember and password is None:
                raise ValueError(
                    "Enter a password to remember it, or leave \"Remember password\" "
                    "unchecked to use it for this connection only."
                )
            if remember and password is not None:
                self._credentials.put(
                    credential_target(profile["id"]), password
                )
            profile["remembered"] = remember
            profiles = self._read()
            profiles.append(profile)
            self._write(profiles)
            return self._public(profile)

    def update(self, environment_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        patch = patch or {}
        with self._lock:
            profiles = self._read()
            updated: Optional[dict[str, Any]] = None
            for index, profile in enumerate(profiles):
                if profile["id"] != str(environment_id or "").strip():
                    continue
                # Validate the field patch first: a bad name must not leave a credential
                # written (or deleted) against a profile that keeps its old fields.
                updated = _apply_patch(profile, patch)
                password = validate_password(patch.get("password"))
                remember = (
                    bool(patch["remember"])
                    if "remember" in patch
                    else bool(profile.get("remembered"))
                )
                if remember:
                    if password is not None:
                        self._credentials.put(
                            credential_target(profile["id"]), password
                        )
                    elif not self._has_password(profile) and self._credentials.available:
                        # The flag says "remembered" but the OS store has nothing (or the
                        # user is re-saving on a platform where it never could): keeping
                        # the flag silently would pretend a password exists.
                        raise ValueError(
                            "The remembered password could not be found — re-enter it, "
                            "or uncheck \"Remember password\"."
                        )
                elif profile.get("remembered") and self._credentials.available:
                    self._credentials.delete(credential_target(profile["id"]))
                updated["remembered"] = remember
                profiles[index] = updated
                break
            if updated is None:
                raise KeyError(str(environment_id or "").strip())
            self._write(profiles)
            return self._public(updated)

    def delete(self, environment_id: str) -> bool:
        with self._lock:
            wanted = str(environment_id or "").strip()
            profiles = self._read()
            target = next((p for p in profiles if p["id"] == wanted), None)
            if target is None:
                return False
            if target.get("remembered") and self._credentials.available:
                self._credentials.delete(credential_target(wanted))
            self._write([profile for profile in profiles if profile["id"] != wanted])
            return True

    # -- the probe --------------------------------------------------------------
    def test_connection(
        self, environment_id: str, password: Optional[str] = None
    ) -> dict[str, Any]:
        """Probe one profile, with the transient password or the remembered one.

        `password` is what the form tested with — used for this call and discarded.
        Without one, the profile's remembered secret is loaded through the credential
        seam; `remembered` with no readable secret is an auth failure (password), not a
        silent key-auth retry.
        """
        profile = self._find(environment_id)
        effective = validate_password(password)
        if effective is None:
            remembered = bool(profile.get("remembered"))
            try:
                effective = self._remembered_password(profile)
            except CredentialStoreUnavailable as error:
                return _failure(str(error))
            if remembered and effective is None and self._credentials.available:
                return _failure(
                    f"Authentication failed (password) for "
                    f"{ssh_destination(profile) or 'the host'}. The remembered password "
                    "could not be found — re-enter it and save the host."
                )
        return test_connection(
            profile,
            runner=self._runner,
            known_hosts_file=self._known_hosts_file,
            connect_timeout=self._connect_timeout,
            password=effective,
        )

    # -- the picker's directory browsing ----------------------------------------
    def list_directories(
        self, environment_id: str, path: Optional[str] = None
    ) -> dict[str, Any]:
        """List one host directory's immediate subdirectories (the environment picker).

        Resolves the profile and its remembered password through the SAME seam
        `test_connection` uses (runner, known-hosts file, connect timeout), then runs the
        one framed listing command; the typed failures are the transport's own. Raises
        `KeyError` for an unknown id, `ValueError`/`PermissionError` for an unusable path,
        and the transport's `RemoteEnvError` for connection failures.
        """
        from .remote_env import RemoteSession
        from .remote_env.directories import list_remote_directories

        profile = self._find(environment_id)
        try:
            password = self._remembered_password(profile)
        except CredentialStoreUnavailable as error:
            raise ValueError(str(error)) from None
        if (
            profile.get("remembered")
            and password is None
            and self._credentials.available
        ):
            raise ValueError(
                f"Authentication failed (password) for "
                f"{ssh_destination(profile) or 'the host'}. The remembered password "
                "could not be found — re-enter it and save the host."
            )
        session = RemoteSession(
            profile,
            runner=self._runner,
            known_hosts_file=self._known_hosts_file,
            connect_timeout=self._connect_timeout,
            password=password,
        )
        return list_remote_directories(session, path)

    # -- internals --------------------------------------------------------------
    def _find(self, environment_id: str) -> dict[str, Any]:
        wanted = str(environment_id or "").strip()
        for profile in self._read():
            if profile["id"] == wanted:
                return profile
        raise KeyError(wanted)

    def _public(self, profile: dict[str, Any]) -> dict[str, Any]:
        """The wire shape: the profile plus `has_password` (a probe, not the flag) and
        `remembered` — and never a password, because the canonical profile has none."""
        public = dict(profile)
        public["has_password"] = self._has_password(profile)
        return public

    def _has_password(self, profile: dict[str, Any]) -> bool:
        if not profile.get("remembered"):
            return False
        try:
            return (
                self._credentials.get(credential_target(str(profile.get("id"))))
                is not None
            )
        except CredentialStoreUnavailable:
            return False

    def _remembered_password(self, profile: dict[str, Any]) -> Optional[str]:
        environment_id = str(profile.get("id") or "")
        if not profile.get("remembered") or not self._credentials.available:
            return None
        try:
            return self._credentials.get(credential_target(environment_id))
        except CredentialStoreUnavailable as error:
            raise CredentialStoreUnavailable(
                f"The remembered password for "
                f"'{profile.get('name') or environment_id}' could not be read: {error}"
            ) from None

    def _read(self) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        raw = data.get("profiles") if isinstance(data, dict) else None
        if not isinstance(raw, list):
            return []
        profiles: list[dict[str, Any]] = []
        for item in raw:
            normalized = _normalize_loaded(item)
            if normalized is not None:
                profiles.append(normalized)
        return profiles

    def _write(self, profiles: list[dict[str, Any]]) -> None:
        payload = {"version": PROFILE_FILE_VERSION, "profiles": profiles}
        write_private_text(self.path, json.dumps(payload, indent=2) + "\n")


__all__ = [
    "ASKPASS_HELPER_PREFIX",
    "CommandResult",
    "CredentialStoreUnavailable",
    "DEFAULT_CONNECT_TIMEOUT_SECONDS",
    "DEFAULT_SSH_PORT",
    "INSPECT_SENTINEL",
    "PROFILES_FILE_NAME",
    "PROFILE_FILE_VERSION",
    "PROBE_TIMEOUT_SECONDS",
    "RemoteEnvironmentStore",
    "Runner",
    "askpass_environment",
    "classify_connection_error",
    "configured_known_hosts_file",
    "inspect_command",
    "normalize_arch",
    "normalize_platform",
    "parse_inspection",
    "profiles_path",
    "run_ssh",
    "shell_quote",
    "ssh_argv",
    "ssh_binary",
    "ssh_destination",
    "test_connection",
    "validate_host",
    "validate_identity_file",
    "validate_name",
    "validate_password",
    "validate_port",
    "validate_user",
]
