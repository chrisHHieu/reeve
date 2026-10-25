"""``RemoteExecutor`` — the Executor protocol over SSH.

One command per call, run in a fresh remote POSIX shell rooted at the session's remote
workspace. There is no long-lived remote process yet (a persistent PTY-backed shell is
deliberately deferred), so the two facts a persistent shell would hold are emulated the
honest way: the working directory is tracked from the frame's final ``$PWD`` and
re-applied with ``cd`` on the next call, and the managed ``REEVE_*`` variables are
exported before every command exactly as ``LocalExecutor`` does.

The scratch contract: ``~/.reeve/scratch/<session>`` is created on first use
(``mkdir -p`` + ``chmod 700``) and exported as ``$REEVE_SCRATCH`` — the same variable
the local world's shell environment publishes — so remote commands put deliverables
where the session's artifact/scratch rules expect them without the model spelling out a
path. ``$REEVE_WORKSPACE`` and ``$REEVE_SESSION`` travel with it.

Result shape and error vocabulary are ``LocalExecutor``'s, because consumers
(``shell_tools``, the presenters, the engine) already speak them: ``{command, cwd,
exit_code, output, timed_out, truncated[, omitted_lines][, full_output][, error]}``.
Where a local result reports a timeout or a dead shell, a remote one maps
``RemoteTimeoutError``/``RemoteEnvError`` onto the same keys. Output keeps both ends via
``tools/retention.retain`` under the same 20,000-character default budget.
"""

from __future__ import annotations

import posixpath
import time
import uuid
from typing import Any, Optional

from ..remote_environments import shell_quote
from harness.tools.retention import retain
from harness.tools.shell import Executor, _DEFAULT_TIMEOUT

#: What a remote command that ran out its wall-clock budget is told. Unlike the local
#: world there is no auto-background here — the ssh call IS the command's lifetime, so a
#: timeout is a stop — and the honest message names both readings and the check.
_REMOTE_TIMEOUT_HINT = (
    "this command timed out after {elapsed:.1f}s and printed nothing before that: it was "
    "stopped. It may have been waiting for input nothing here can type (re-run it "
    "non-interactively, e.g. -y / --yes / --non-interactive), or it was simply slow: raise "
    "timeout_seconds, or start it in the background if the tool supports it. Either way it "
    "may have done something before it was stopped, so check the remote state separately."
)
from harness.tools.shell_env import SCRATCH, SESSION, WORKSPACE
from .files import mkdir_script
from .transport import (
    RemoteCommandError,
    RemoteEnvError,
    RemoteSession,
    RemoteTimeoutError,
)


class RemoteExecutor(Executor):
    """The Executor protocol, backed by one ``RemoteSession`` and no local process."""

    def __init__(
        self,
        session: RemoteSession,
        *,
        cwd: str,
        workspace: Optional[str] = None,
        session_id: str = "",
        default_timeout: float = _DEFAULT_TIMEOUT,
        max_output_chars: int = 20_000,
        spill: Optional[Any] = None,
    ) -> None:
        self.session = session
        self.cwd = str(cwd)
        self.workspace = str(workspace or cwd)
        self.session_id = session_id or uuid.uuid4().hex[:12]
        self.default_timeout = default_timeout
        self.max_output_chars = max_output_chars
        # Bound once, like LocalExecutor: only truncating results ever consult it.
        self._spill = spill.sink("shell") if spill is not None else None
        self._scratch_dir: Optional[str] = None
        self._scratch_ready = False

    # -- scratch -------------------------------------------------------------------

    @property
    def scratch(self) -> str:
        """The remote scratch root for this session, resolved from remote HOME."""
        if self._scratch_dir is None:
            self._scratch_dir = posixpath.join(
                self.session.ensure_home(), ".reeve", "scratch", self.session_id
            )
        return self._scratch_dir

    def _exports(self) -> str:
        return "\n".join(
            [
                f"export {WORKSPACE}={shell_quote(self.workspace)}",
                f"export {SCRATCH}={shell_quote(self.scratch)}",
                f"export {SESSION}={shell_quote(self.session_id)}",
            ]
        )

    def _ensure_scratch(self) -> None:
        if self._scratch_ready:
            return
        result = self.session.run(mkdir_script(self.scratch, 0o700))
        if result.exit_code != 0 or not result.frame_found:
            raise RemoteCommandError(
                f"could not create the remote scratch directory {self.scratch}",
                exit_code=result.exit_code,
                output=result.output,
            )
        self._scratch_ready = True

    # -- Executor ------------------------------------------------------------------

    def interrupt_now(self) -> None:
        """The engine's Stop hooks call this on every executor. A remote command runs in
        its own short-lived ssh process with no local handle to signal, so there is
        nothing to kill from here — the call's own wall-clock timeout is the bound and
        the ssh process is reaped when the runner returns."""
        return None

    def run(self, command: str, timeout: Optional[float] = None) -> dict[str, Any]:
        started = time.monotonic()
        budget = (
            float(timeout)
            if isinstance(timeout, (int, float)) and timeout > 0
            else self.default_timeout
        )
        try:
            self._ensure_scratch()
            result = self.session.run(self._script(command), timeout=budget)
        except RemoteTimeoutError:
            elapsed = time.monotonic() - started
            return self._result(
                command,
                None,
                "",
                timed_out=True,
                error=_REMOTE_TIMEOUT_HINT.format(elapsed=elapsed),
                elapsed=elapsed,
            )
        except RemoteEnvError as exc:
            return self._result(
                command,
                None,
                "",
                timed_out=False,
                error=exc.tool_error,
                elapsed=time.monotonic() - started,
            )

        elapsed = time.monotonic() - started
        # ssh keeps the command's stderr on its own stream; LocalExecutor merges the two,
        # and every consumer here reads one `output`.
        text = result.output
        if result.stderr:
            text = f"{text}\n{result.stderr}" if text.strip() else result.stderr
        kept = retain(text, budget=self.max_output_chars, spill=self._spill)
        if result.cwd:
            self.cwd = result.cwd
        error = None
        if result.exit_code is None:
            error = (
                "the remote command ended without reporting an exit status — it may "
                "have closed the shell or the connection. The output above is all that "
                "came back; check the remote state before re-running anything."
            )
        return self._result(
            command,
            result.exit_code,
            kept.text,
            timed_out=False,
            truncated=kept.truncated,
            omitted_lines=kept.omitted_lines,
            spilled=kept.spilled,
            error=error,
            elapsed=elapsed,
        )

    def _script(self, command: str) -> str:
        # `cd` falls back to the workspace if the tracked directory is gone; the frame's
        # final $PWD then reports the truth, so the next call starts from reality rather
        # than from a path that no longer exists.
        header = (
            f"cd {shell_quote(self.cwd)} 2>/dev/null || "
            f"cd {shell_quote(self.workspace)} 2>/dev/null"
        )
        return f"{header}\n{self._exports()}\n{command}"

    def _result(
        self,
        command: str,
        exit_code: Optional[int],
        output: str,
        *,
        timed_out: bool,
        truncated: bool = False,
        omitted_lines: int = 0,
        spilled: Optional[str] = None,
        error: Optional[str] = None,
        elapsed: Optional[float] = None,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "command": command,
            "cwd": self.cwd,
            "exit_code": exit_code,
            "output": output,
            "timed_out": timed_out,
            "truncated": truncated,
        }
        if elapsed is not None:
            result["elapsed_seconds"] = round(elapsed, 2)
        if truncated and omitted_lines:
            result["omitted_lines"] = omitted_lines
        if spilled:
            result["full_output"] = spilled
        if error:
            result["error"] = error
        return result


__all__ = ["RemoteExecutor"]
