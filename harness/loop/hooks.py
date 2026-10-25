"""Lifecycle hooks — a user's own script at named moments of a turn.

A hook is a command the USER registered for an event. When the event happens the engine
runs it, hands it JSON on stdin, and reads JSON (or plain text) back:

    in   {"event": "pre_tool_use", "tool": "run_shell", "arguments": {...},
          "workspace": "C:/work", "session": "…", "turn": 3}
    out  {"decision": "deny", "reason": "use make clean, not rm -rf"}
         {"context": "the team rule: run `ruff format` after every write"}

Three rules, each with a reason:

- **A hook may deny or add context, never grant.** `decision: "deny"` refuses the call
  before any permission path sees it; `allow` is not a permission either — the normal
  guard stage and presets still run, so a hook cannot widen what a session may do
  (the guard stage's monotonic rule, applied to the newest extension point).
- **A hook cannot break a turn.** Timeouts (default 10s), a missing binary, a crash, bad
  JSON: all become an `error` on the outcome, logged, and the turn continues. Same
  contract the session log lives under.
- **A workspace hook needs trust, by DECLARATION DIGEST.** A repository can ship
  `.reeve/hooks.toml`; running it without a person's consent would turn `git clone`
  into arbitrary code execution. The digest covers the commands and matchers, so editing
  the file invalidates trust — a `git pull` cannot silently change what runs. Global hooks
  (in the user's own `config.toml`) are trusted by construction: the user wrote them.

Everything a hook DID is logged (`hook/run`), and a context it returns enters the turn as
a `steering/message` with `source: "hook:<event>"` — model-visible means logged (0001).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Iterable, Optional

from harness.safety.secrets import state_dir

logger = logging.getLogger("harness.loop.hooks")

#: The moments a hook can attach to. Named after common events so a hook written for another tool
#: harness reads the same here, minus the ones this engine has no honest equivalent for.
EVENTS = (
    "session_start",
    "user_prompt_submit",
    "pre_tool_use",
    "post_tool_use",
    "post_tool_use_failure",
    "stop", "session_end",  # session_end: the session was deleted or the server is closing
)

#: Tool events match on the tool NAME; everything else ignores the matcher.
TOOL_EVENTS = ("pre_tool_use", "post_tool_use", "post_tool_use_failure")

DEFAULT_TIMEOUT_SECONDS = 10.0
MAX_TIMEOUT_SECONDS = 120.0

#: How much hook context may enter one turn. A hook that prints a novel would otherwise
#: be a context-budget incident; the text is bounded and says it was cut.
MAX_CONTEXT_CHARS = 4_000

#: How many hooks may be registered for one event. A config listing fifty scripts is a
#: mistake (or a denial-of-service against the user's own turn), not a configuration.
MAX_HOOKS_PER_EVENT = 20


@dataclass(frozen=True)
class HookSpec:
    event: str
    name: str
    command: tuple[str, ...]
    matcher: str = ""
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    #: "global" (the user's config.toml — trusted by construction), "workspace"
    #: (`.reeve/hooks.toml` — needs a trust entry) or "plugin" (trusted by the plugin's
    #: consented code digest).
    source: str = "global"
    #: The plugin a "plugin" hook belongs to (`<name>@<origin>`).
    plugin: str = ""
    #: "claude": a plugin hook written for Claude Code, which reads Claude Code's payload
    #: keys and answers in Claude Code's protocol (exit 2 denies, `permissionDecision`).
    protocol: str = "reeve"
    #: Extra environment for the process (the plugin's paths and settings).
    env: tuple[tuple[str, str], ...] = ()

    def matches(self, tool: str) -> bool:
        if self.event not in TOOL_EVENTS or not self.matcher:
            return True
        try:
            return re.search(self.matcher, tool or "") is not None
        except re.error:
            return False  # a broken pattern matches nothing; the loader warns


@dataclass
class HookOutcome:
    """What the hooks for one event, taken together, decided."""

    decision: str = "allow"  # "allow" | "ask" | "deny"
    reason: str = ""
    context: str = ""
    errors: list[str] = field(default_factory=list)
    #: Which hook made the decision, and where it comes from ("hook guard from plugin
    #: release-helper@acme"): a refusal or an ask names its source to the person and the
    #: model, so a plugin's hook is never mistaken for Reeve's own rule (design §11.1).
    by: str = ""

    def attributed(self, fallback: str) -> str:
        """The reason as a person reads it: the hook's own words, then who said them."""
        text = self.reason or fallback
        if self.by and self.by not in text:
            text = f"{text} ({self.by})"
        return text

    @property
    def denied(self) -> bool:
        return self.decision == "deny"

    @property
    def asked(self) -> bool:
        """A hook wants a person to approve this call, whatever the mode would allow. It
        can only ADD a human (guards deny or demand a human, never grant)."""
        return self.decision == "ask"

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"decision": self.decision}
        if self.reason:
            out["reason"] = self.reason
        if self.context:
            out["context"] = self.context
        if self.errors:
            out["errors"] = self.errors
        if self.by:
            out["by"] = self.by
        return out



def _clip_context(text: str) -> str:
    text = (text or "").strip()
    if len(text) <= MAX_CONTEXT_CHARS:
        return text
    dropped = len(text) - MAX_CONTEXT_CHARS
    return text[:MAX_CONTEXT_CHARS] + f"\n…[{dropped:,} more characters from the hook]"


# -- trust ------------------------------------------------------------------------


def declaration_digest(specs: Iterable[HookSpec]) -> str:
    """`sha256:<hex>` over what a workspace's hooks DECLARE: event, name, command, matcher.

    Not the file's bytes: a comment or a reordered key must not invalidate trust, while a
    changed command or matcher must. Timeouts are excluded for the same reason a comment
    is — they cannot change what runs.
    """
    digest = hashlib.sha256()
    for spec in sorted(specs, key=lambda s: (s.event, s.name, s.command)):
        digest.update("\0".join([spec.event, spec.name, spec.matcher, *spec.command]).encode("utf-8"))
        digest.update(b"\n")
    return "sha256:" + digest.hexdigest()


class HookTrustStore:
    """`hook_trust.json` — which workspace hook DECLARATIONS the person approved.

    A file beside the other user-owned state (and in `boundary.protected_paths`, so the
    agent cannot grant itself trust). Path-keyed like `workspace_trust.json`, with the
    digest recorded: the difference is deliberate — a command allowlist is a prefix that
    still passes through approval, while a hook is code that runs unattended.
    """

    def __init__(self, path: Optional[str | Path] = None) -> None:
        self.path = Path(path) if path is not None else state_dir() / "hook_trust.json"

    @staticmethod
    def canonical(workspace: str | Path) -> str:
        return str(Path(workspace).expanduser().resolve())

    def _load(self) -> dict[str, str]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(data, dict):
            return {}
        entries = data.get("trusted_hooks", {})
        if not isinstance(entries, dict):
            return {}
        return {str(k): str(v) for k, v in entries.items() if isinstance(v, str)}

    def is_trusted(self, workspace: str | Path, digest: str) -> bool:
        return self._load().get(self.canonical(workspace)) == digest

    def trust(self, workspace: str | Path, digest: str) -> str:
        entries = self._load()
        entries[self.canonical(workspace)] = digest
        self._write(entries)
        return digest

    def untrust(self, workspace: str | Path) -> None:
        entries = self._load()
        entries.pop(self.canonical(workspace), None)
        self._write(entries)

    def list(self) -> dict[str, str]:
        return dict(sorted(self._load().items()))

    def _write(self, entries: dict[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        tmp.write_text(
            json.dumps({"trusted_hooks": entries}, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)


# -- loading ----------------------------------------------------------------------


def _specs_from_table(
    table: Any, *, source: str, warnings: list[str]
) -> list[HookSpec]:
    """One `[hooks.<event>]` table: either a single hook or a list of them."""
    entries = table if isinstance(table, list) else [table]
    out: list[HookSpec] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            warnings.append(f"hook #{index + 1} is not a table")
            continue
        event = str(entry.get("event") or "").strip()
        if event not in EVENTS:
            warnings.append(f"unknown hook event: {event or '(missing)'}")
            continue
        command = entry.get("command")
        if isinstance(command, str):
            command = [command]
        if not isinstance(command, list) or not all(
            isinstance(part, str) and part for part in command
        ):
            warnings.append(f"{event}: `command` must be a non-empty list of strings")
            continue
        timeout = entry.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
        try:
            timeout = float(timeout)
        except (TypeError, ValueError):
            warnings.append(f"{event}: timeout_seconds must be a number")
            continue
        timeout = max(0.1, min(timeout, MAX_TIMEOUT_SECONDS))
        matcher = str(entry.get("matcher") or "")
        if matcher:
            try:
                re.compile(matcher)
            except re.error as exc:
                warnings.append(f"{event}: bad matcher {matcher!r} ({exc})")
                continue
        name = str(entry.get("name") or "").strip() or f"{event}-{index + 1}"
        out.append(
            HookSpec(
                event=event,
                name=name,
                command=tuple(command),
                matcher=matcher,
                timeout_seconds=timeout,
                source=source,
            )
        )
    return out


def _read_hook_tables(path: Path, *, source: str, warnings: list[str]) -> list[HookSpec]:
    if not path.is_file():
        return []
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        warnings.append(f"{path}: {exc}")
        return []
    table = data.get("hooks") if isinstance(data, dict) else None
    if not isinstance(table, dict):
        return []
    out: list[HookSpec] = []
    for event, entry in table.items():
        if event not in EVENTS:
            warnings.append(f"{path}: unknown hook event {event!r}")
            continue
        out.extend(
            _specs_from_table({**entry, "event": event} if isinstance(entry, dict) else entry,
                              source=source, warnings=warnings)
        )
    return out


def plugin_hook_specs(declarations: Iterable[dict[str, Any]], warnings: list[str]) -> list[HookSpec]:
    """Plugin hooks (already converted from Claude Code's format and cleared by the
    plugin's consented digest — `harness.extensions.plugins.running`) as specs."""
    out: list[HookSpec] = []
    for decl in declarations:
        try:
            out.append(
                HookSpec(
                    event=str(decl["event"]),
                    name=str(decl["name"]),
                    command=tuple(str(part) for part in decl["command"]),
                    matcher=str(decl.get("matcher") or ""),
                    timeout_seconds=float(decl.get("timeout_seconds") or DEFAULT_TIMEOUT_SECONDS),
                    source="plugin",
                    plugin=str(decl.get("plugin") or ""),
                    protocol="claude",
                    env=tuple(sorted((str(k), str(v)) for k, v in (decl.get("env") or {}).items())),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            warnings.append(f"plugin hook {decl.get('name')}: {exc}")
    return out


def load_hooks(
    workspace: Optional[str | Path] = None,
    *,
    global_path: Optional[Path] = None,
    trust: Optional[HookTrustStore] = None,
    plugin_hooks: Iterable[dict[str, Any]] = (),
) -> tuple[list[HookSpec], str]:
    """(specs, note). `note` explains skipped workspace hooks — shown once per session, so
    a person whose hooks are not running learns why instead of guessing."""
    warnings: list[str] = []
    global_file = global_path if global_path is not None else state_dir() / "config.toml"
    specs = _read_hook_tables(global_file, source="global", warnings=warnings)

    note = ""
    if workspace:
        workspace_file = Path(workspace).expanduser() / ".reeve" / "hooks.toml"
        workspace_specs = _read_hook_tables(
            workspace_file, source="workspace", warnings=warnings
        )
        if workspace_specs:
            digest = declaration_digest(workspace_specs)
            store = trust if trust is not None else HookTrustStore()
            if store.is_trusted(workspace, digest):
                specs.extend(workspace_specs)
            else:
                note = (
                    f"{workspace_file} declares {len(workspace_specs)} hook(s) that are "
                    "NOT running: a workspace hook is code, and it needs your consent. "
                    "Trust this exact declaration with:\n"
                    "  python -c \"from harness.loop.hooks import HookTrustStore, "
                    "load_hooks, declaration_digest; "
                    f"w=r'{Path(workspace).expanduser()}'; "
                    "HookTrustStore().trust(w, declaration_digest(load_hooks(w)[0]))\""
                )
                logger.info("workspace hooks skipped (untrusted): %s", workspace_file)

    # A plugin's hooks come LAST: the cap's seats go to the person's own hooks first.
    specs.extend(plugin_hook_specs(plugin_hooks, warnings))

    # Per-event cap, after every source is merged: global hooks win the seats, because
    # the user wrote them; a plugin never takes a seat from a person's hook.
    kept: list[HookSpec] = []
    counts: dict[str, int] = {}
    for spec in specs:
        counts[spec.event] = counts.get(spec.event, 0) + 1
        if counts[spec.event] > MAX_HOOKS_PER_EVENT:
            warnings.append(
                f"{spec.event}: more than {MAX_HOOKS_PER_EVENT} hooks; "
                f"{spec.name} ignored"
            )
            continue
        kept.append(spec)
    if warnings:
        logger.warning("hook configuration: %s", "; ".join(warnings))
        note = (note + "\n" if note else "") + "hook configuration: " + "; ".join(warnings)
    return kept, note


def hook_payload(**fields: Any) -> dict[str, Any]:
    """The payload minus empty fields, so a hook sees only what applied."""
    return {k: v for k, v in fields.items() if v is not None}


__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "EVENTS",
    "HookOutcome",
    "HookSpec",
    "HookTrustStore",
    "declaration_digest",
    "hook_payload",
    "load_hooks",
    "plugin_hook_specs",
    "replace",
]
