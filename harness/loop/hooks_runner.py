"""Running hooks — the subprocess half of `harness/loop/hooks.py`.

Split out when the contract module crossed the architecture ceiling: what a hook IS (its
spec, its events, the loader, the trust digest) reads differently from how one is RUN
(spawn, timeout, parse, log), and the second half is the one that talks to the OS.

The rules live in the other module's docstring; the short version is that nothing here may
raise, grant, or loop: a hook denies or adds context, and its failure is a log line.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Iterable, Optional

from .hooks import (
    HookOutcome,
    HookSpec,
    _clip_context,
)

logger = logging.getLogger("harness.loop.hooks")


def hook_source(name: str, source: str, plugin: str = "") -> str:
    """"hook <name> from plugin <id>" / "from this workspace's hooks" / "from your hooks"."""
    if source == "plugin" and plugin:
        return f"hook {name} from plugin {plugin}"
    if source == "workspace":
        return f"hook {name} from this workspace's hooks"
    return f"hook {name} from your hooks"


#: Reeve's hook events under Claude Code's names, for a plugin hook written for it.
_CLAUDE_EVENTS = {
    "session_start": "SessionStart",
    "user_prompt_submit": "UserPromptSubmit",
    "pre_tool_use": "PreToolUse",
    "post_tool_use": "PostToolUse",
    "post_tool_use_failure": "PostToolUseFailure",
    "stop": "Stop",
    "session_end": "SessionEnd",
}
#: Argument names that differ between a Reeve tool and its Claude Code counterpart
#: (design §14.2), so a Claude Code hook reading `tool_input.file_path` finds it.
_CLAUDE_FIELDS = {
    "read_file": {"path": "file_path", "start_line": "offset", "max_lines": "limit"},
    "write_file": {"path": "file_path"},
    "replace_in_file": {"path": "file_path", "old": "old_string", "new": "new_string"},
    "edit_notebook": {"path": "notebook_path", "cell": "cell_id", "source": "new_source", "action": "edit_mode"},
}
#: Events where Claude Code reads a hook's plain stdout as context for the model.
_CONTEXT_EVENTS = ("session_start", "user_prompt_submit")


def claude_payload(payload: dict[str, Any], *, workspace: str, session_id: str) -> dict[str, Any]:
    """Reeve's payload plus the keys a Claude Code hook reads (design §5.4). Added, not
    replacing: a hook may read either. No `transcript_path` — reading the raw transcript
    is a permission of its own, not a default."""
    from harness.extensions.plugins.compat import claude_tool

    event = str(payload.get("event") or "")
    tool = str(payload.get("tool") or "")
    arguments = payload.get("arguments") if isinstance(payload.get("arguments"), dict) else None
    out = {
        **payload,
        "hook_event_name": _CLAUDE_EVENTS.get(event, event),
        "session_id": session_id,
        "cwd": workspace,
        "permission_mode": "default",
    }
    if tool:
        renames = _CLAUDE_FIELDS.get(tool, {})
        out["tool_name"] = claude_tool(tool)
        out["tool_input"] = {renames.get(k, k): v for k, v in (arguments or {}).items()}
    return out


def parse_claude(event: str, returncode: int, stdout: str, stderr: str) -> tuple[str, str, str, str]:
    """(decision, reason, context, error) from a Claude Code hook's answer (design §5.4).
    Exit 2 blocks. `permissionDecision` deny blocks and ask asks; allow is IGNORED — a
    plugin never grants — and so is `updatedInput`, which would change a call after a
    person approved it. Any other non-zero exit is a failed hook that blocks nothing."""
    text = (stdout or "").strip()
    parsed: dict[str, Any] = {}
    if text.startswith("{"):
        try:
            value = json.loads(text)
            parsed = value if isinstance(value, dict) else {}
        except json.JSONDecodeError:
            parsed = {}
    specific = parsed.get("hookSpecificOutput") if isinstance(parsed.get("hookSpecificOutput"), dict) else {}
    reason = str(
        specific.get("permissionDecisionReason") or parsed.get("reason") or parsed.get("stopReason") or ""
    ).strip()
    if returncode == 2:
        return "deny", reason or (stderr or "").strip()[:300] or "a plugin hook blocked this", "", ""
    if returncode != 0:
        detail = (stderr or "").strip()[:300]
        return "allow", "", "", f"exited {returncode}" + (f": {detail}" if detail else "")
    notes = []
    if "updatedInput" in specific or "updatedInput" in parsed:
        notes.append("updatedInput ignored: a plugin may not change a call's arguments")
    decision = str(specific.get("permissionDecision") or "").lower()
    if not decision and str(parsed.get("decision") or "").lower() == "block":
        decision = "deny"
    context = str(specific.get("additionalContext") or parsed.get("systemMessage") or "").strip()
    if not parsed and text and event in _CONTEXT_EVENTS:
        context = text
    if decision in ("deny", "ask"):
        return decision, reason, context, "; ".join(notes)
    return "allow", "", context, "; ".join(notes)


# -- running ----------------------------------------------------------------------


def _close_transport(proc: Any) -> None:
    """Close the subprocess transport explicitly.

    On Windows the proactor transport warns ("Event loop is closed") when GC collects it
    after the loop is gone — which turns every test that ran a hook into a warning, and a
    warning people learn to ignore is worse than no warning. The pipes are ours; closing
    them here is the fix, and it is best-effort like everything else in this module.
    """
    transport = getattr(proc, "_transport", None)
    close = getattr(transport, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


class HookRunner:
    """Runs the specs for one event. Never raises; every run is logged."""

    def __init__(
        self,
        specs: Iterable[HookSpec],
        *,
        workspace: Optional[str | Path] = None,
        session_id: str = "",
        log: Any = None,
        env: Optional[dict[str, str]] = None,
        note: str = "",
    ) -> None:
        self.specs = list(specs)
        self.workspace = str(workspace) if workspace else os.getcwd()
        self.session_id = session_id
        #: Late-bound: the manager attaches the session log AFTER the engine is built, so
        #: the runner starts with None and the engine fills it in at first use.
        self.log = log
        self._env = env
        #: Why some hooks are not running (untrusted workspace declaration, bad config).
        #: The engine shows it once per session — silence would look like a broken hook.
        self.note = note

    @property
    def enabled(self) -> bool:
        return bool(self.specs)

    def for_event(self, event: str, tool: str = "") -> list[HookSpec]:
        return [
            spec for spec in self.specs if spec.event == event and spec.matches(tool)
        ]

    async def run(self, event: str, payload: dict[str, Any], *, tool: str = "") -> HookOutcome:
        outcome = HookOutcome()
        specs = self.for_event(event, tool)
        if not specs:
            return outcome
        body = json.dumps({"event": event, **payload})
        claude_body: Optional[str] = None
        for spec in specs:
            if spec.protocol == "claude" and claude_body is None:
                claude_body = json.dumps(
                    claude_payload({"event": event, **payload}, workspace=self.workspace, session_id=self.session_id)
                )
            decision, reason, context, error, duration_ms = await self._run_one(
                spec, claude_body if spec.protocol == "claude" else body
            )
            self._log_run(spec, decision, reason, context, error, duration_ms)
            if error and not (spec.protocol == "claude" and error.startswith("updatedInput ignored")):
                outcome.errors.append(f"{spec.name}: {error}")
                continue
            if context:
                outcome.context = _clip_context(
                    (outcome.context + "\n" + context) if outcome.context else context
                )
            if decision == "deny" and not outcome.denied:
                outcome.decision = "deny"
                outcome.reason = reason or f"a hook ({spec.name}) refused this"
                outcome.by = hook_source(spec.name, spec.source, spec.plugin)
            elif decision == "ask" and outcome.decision == "allow":
                outcome.decision = "ask"
                outcome.reason = reason or f"a hook ({spec.name}) asks you to approve this"
                outcome.by = hook_source(spec.name, spec.source, spec.plugin)
        return outcome

    async def _run_one(
        self, spec: HookSpec, body: str
    ) -> tuple[str, str, str, str, int]:
        env = {
            **(self._env if self._env is not None else os.environ),
            **dict(spec.env),
            "REEVE_HOOK_EVENT": spec.event,
            "REEVE_HOOK_NAME": spec.name,
            "REEVE_HOOK_WORKSPACE": self.workspace,
            "REEVE_SESSION_ID": self.session_id,
        }
        started = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_exec(
                *spec.command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.workspace if Path(self.workspace).is_dir() else None,
                env=env,
            )
        except (OSError, ValueError) as exc:
            return "allow", "", "", f"could not start: {exc}", 0
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(body.encode("utf-8")), timeout=spec.timeout_seconds
            )
        except asyncio.TimeoutError:
            with_suppress = getattr(proc, "kill", None)
            if callable(with_suppress):
                with_suppress()
            _close_transport(proc)
            return (
                "allow",
                "",
                "",
                f"timed out after {spec.timeout_seconds:g}s",
                int((time.monotonic() - started) * 1000),
            )
        finally:
            _close_transport(proc)
        duration_ms = int((time.monotonic() - started) * 1000)
        if spec.protocol == "claude":
            decision, reason, context, error = parse_claude(
                spec.event,
                int(proc.returncode or 0),
                (stdout or b"").decode("utf-8", "replace"),
                (stderr or b"").decode("utf-8", "replace"),
            )
            return decision, reason, context, error, duration_ms
        if proc.returncode != 0:
            detail = (stderr or b"").decode("utf-8", "replace").strip()[:300]
            return (
                "allow",
                "",
                "",
                f"exited {proc.returncode}" + (f": {detail}" if detail else ""),
                duration_ms,
            )
        text = (stdout or b"").decode("utf-8", "replace").strip()
        if not text:
            return "allow", "", "", "", duration_ms
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            # Plain text is a CONTEXT, not an error: `echo "remember the branch"` is the
            # shortest useful hook, and refusing it would teach people to wrap everything.
            # Text that OPENS like JSON is the exception: it meant to serialize something
            # and failed, and calling that "context" would hide a broken hook forever.
            if text.lstrip()[:1] in ("{", "["):
                return "allow", "", "", f"output is not valid JSON: {text[:200]}", duration_ms
            return "allow", "", text, "", duration_ms
        if not isinstance(parsed, dict):
            return "allow", "", text, "", duration_ms
        decision = str(parsed.get("decision") or "allow").strip().lower()
        if decision not in ("allow", "deny"):
            return "allow", "", "", f"unknown decision {decision!r}", duration_ms
        return (
            decision,
            str(parsed.get("reason") or "").strip(),
            str(parsed.get("context") or "").strip(),
            "",
            duration_ms,
        )

    def _log_run(
        self,
        spec: HookSpec,
        decision: str,
        reason: str,
        context: str,
        error: str,
        duration_ms: int,
    ) -> None:
        if self.log is None:
            return
        try:
            self.log.append(
                "hook/run",
                event=spec.event,
                name=spec.name,
                source=spec.source,
                plugin=spec.plugin or None,
                decision=decision if not error else None,
                reason=reason or None,
                context=context or None,
                error=error or None,
                duration_ms=duration_ms,
            )
        except Exception:  # a log that cannot be written never costs a turn
            pass


