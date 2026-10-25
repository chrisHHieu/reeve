"""Starting the server: `reeve-server`, and the sidecar the desktop app runs."""

from __future__ import annotations

import argparse
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

from harness.config.config import load_config
from harness.platform.spawn import spawn
from harness.safety.permissions import Mode
from harness.safety.secrets import state_dir, write_private_text

from .app import create_app
from .logs import configure_logging
from .manager import SessionManager
from .parent_watch import exit_when_orphaned
from .supervision import port_alive, serve_forever

# The three presets, then the older names `Mode` still maps onto them.
_MODE_CHOICES = ["read-only", "workspace-write", "danger-full-access", "discuss", "plan", "interactive", "auto", "bypass-approvals", "auto-approve"]

#: How long a restarted server waits for the one before it to give up the port.
_PORT_FREE_SECONDS = 20.0


def build_app(workspace: str | None, model: str, mode: str):
    seed = Path(workspace).expanduser().resolve() if workspace else None
    return create_app(SessionManager(workspace=seed, data_dir=state_dir(), model=model, mode=Mode(mode)))


def _ensure_ca_bundle() -> None:
    """Give SSL certifi's CA bundle when nothing else is configured.

    A framework Python on macOS has no system trust store `aiohttp` can use: it builds an `ssl`
    context with no CAs, and Slack's Socket Mode client fails with CERTIFICATE_VERIFY_FAILED.
    `httpx` and `requests` carry certifi already, and aiohttp reads `SSL_CERT_FILE`, so that is
    set once here."""
    if os.environ.get("SSL_CERT_FILE"):
        return
    try:
        import certifi

        os.environ["SSL_CERT_FILE"] = certifi.where()
    except Exception:
        return  # without certifi, the interpreter's own setup is all there is


def _ensure_api_token(port: int) -> Path | None:
    """The token every request must carry. The desktop shell passes its own, held in memory and
    never written; a standalone or dev run makes one and writes it to a file only this user can
    read, named for the port. Returns that file, or None."""
    if os.environ.get("REEVE_API_TOKEN"):
        return None
    os.environ["REEVE_API_TOKEN"] = token = secrets.token_hex(32)
    return write_private_text(state_dir() / f"sidecar-{port}.token", f"{token}\n")


def successor_command() -> list[str]:
    """How this process was started, to start it again: its interpreter and the arguments it got
    (`-m harness.interface.server.run --port 8765`, or a script's path)."""
    original = list(getattr(sys, "orig_argv", None) or [])
    return [sys.executable, *(original[1:] if original else sys.argv)]


def _inherited_stream(name: str):
    stream = getattr(sys, name, None)
    try:
        return stream.fileno() if stream is not None else None
    except (OSError, ValueError, AttributeError):
        return subprocess.DEVNULL


def start_successor() -> None:
    """Start this server again, in a process of its own, as this one is about to exit. The token
    stays in the environment, so the new one serves the same token and writes no file: a page that
    held it keeps working (`POST /v1/restart`)."""
    # A process group of its own and no console from this one (the spawn seam's defaults, ADR
    # 0053): it is meant to outlive this process, which exits as soon as it has begun.
    spawn(
        successor_command(),
        env={**os.environ, "REEVE_RESTARTED": "1"},
        cwd=os.getcwd(),
        close_fds=True,
        stdin=subprocess.DEVNULL,
        stdout=_inherited_stream("stdout"),
        stderr=_inherited_stream("stderr"),
    )


def wait_for_port(host: str, port: int, *, seconds: float = _PORT_FREE_SECONDS) -> None:
    """Until the server before this one has let go of the port, or `seconds` have passed."""
    give_up = time.monotonic() + seconds
    while time.monotonic() < give_up and port_alive(host, port, timeout=0.5):
        time.sleep(0.25)


def _parser() -> argparse.ArgumentParser:
    settings = load_config()  # the defaults come from the global config
    parser = argparse.ArgumentParser(prog="reeve-server")
    parser.add_argument("--cwd", default=None, help="optional seed/default workspace")
    parser.add_argument("--model", default=settings.model)
    parser.add_argument("--mode", default=settings.mode, choices=_MODE_CHOICES)
    parser.add_argument("--host", default=settings.host)
    parser.add_argument("--port", type=int, default=settings.port)
    parser.add_argument("--plugin-dir", action="append", default=[], help="load a plugin folder for this server's sessions (repeatable)")
    return parser


def main(argv=None) -> None:
    given = sys.argv[1:] if argv is None else list(argv)
    if given[:1] == ["--self-review"]:
        # A frozen build's child process for the Metrics page's review (`observability/
        # self_review.py`): its sys.executable is this program, so the review starts here.
        from harness.observability.self_review import main as self_review

        raise SystemExit(self_review(given[1:]))
    _ensure_ca_bundle()
    configure_logging()
    args = _parser().parse_args(argv)
    from ..cli import add_plugin_dirs

    add_plugin_dirs(args.plugin_dir)
    # The port really bound, for anything that builds a loopback URL (the managed OAuth callback):
    # the desktop app runs its sidecar on a free random port, beside a hand-run server on 8765.
    os.environ["REEVE_PORT"] = str(args.port)
    restarted = os.environ.pop("REEVE_RESTARTED", None) == "1"
    token_file = _ensure_api_token(args.port)
    if restarted:
        # The previous process's token, and its file, are this one's to clean up now.
        left = state_dir() / f"sidecar-{args.port}.token"
        token_file = token_file or (left if left.is_file() else None)
        wait_for_port(args.host, args.port)
    restart = False
    try:
        import uvicorn  # noqa: F401 - here for the error it gives when it is not installed

        exit_when_orphaned()
        restart = serve_forever(lambda: build_app(args.cwd, args.model, args.mode), args.host, args.port)
    finally:
        # On a restart the token and its file stay: the next process serves the same one.
        if token_file is not None and not restart:
            token_file.unlink(missing_ok=True)
            os.environ.pop("REEVE_API_TOKEN", None)
    if restart:
        start_successor()


if __name__ == "__main__":
    main()
