"""CoreMixin — session lifecycle, workspaces, and engine construction.
"""
from __future__ import annotations

import logging

import asyncio
import json
import os
import re
import shutil
import subprocess
import time
from .helpers import git_branch
from harness.compose import build_engine
from harness.config.config import load_config, workspace_allowed_commands
from harness.context.memory import MemorySettingsStore
from harness.context.mentions import MentionSessionStore
from harness.extensions.mcp import MCPManager
from harness.extensions.skills import SessionSkillStore, SkillStore
from harness.integrations.automation import Scheduler, TaskStore
from harness.integrations.connections import SessionConnectionStore
from harness.integrations.connectors import Gateway
from harness.integrations.connectors.messaging import ParkedStore
from harness.integrations.inbox import InboxStore
from harness.integrations.lsp import LSPManager
from harness.integrations.subscriptions import ChannelBuffer, SubscriptionStore
from harness.integrations.unrouted import UnroutedStore
from harness.llm.providers import ProviderClient, ProviderRouter
from harness.loop import Approver, TurnEngine
from harness.loop.goals import GoalStore
from harness.loop.selfwake import WakeStore
from harness.modes.agents import get_agent, resolve_agent_id
from harness.observability.audit import AuditStore
from harness.safety.permissions import Mode
from harness.safety.secrets import SecretStore, database_path, state_dir
from harness.safety.workspace_trust import WorkspaceTrustStore
from harness.session.continuity import ContinuityStore
from harness.session.conversations import ConversationStore
from harness.session.session_log import SessionLog
from harness.session.sessions import SessionRecord
from pathlib import Path
from typing import Any, Optional

import posixpath
import threading

from .background_status import background_context
from .child_worktrees import handback_context
from .anchor import session_workspace
from .metrics_review import SelfReview
from .metrics_store import MetricsStore
from .shares import ShareStore
from harness.orchestration.worktrees import list_worktrees
from harness.platform.spawn import run as spawn_run

logger = logging.getLogger("harness.manager")


class CoreMixin:
    def __init__(
        self,
        *,
        workspace: Optional[str | Path] = None,  # default/seed workspace (e.g. --cwd)
        data_dir: Optional[str | Path] = None,
        model: str = "gpt-5.6-sol",
        mode: Mode = Mode.WORKSPACE_WRITE,
        provider: Optional[ProviderClient] = None,
    ) -> None:
        self.default_workspace = (
            str(Path(workspace).expanduser().resolve()) if workspace else None
        )
        self.model = model
        self.mode = mode
        self.provider = provider

        if data_dir is not None:
            base = Path(data_dir).expanduser()
        elif self.default_workspace is not None:
            base = Path(self.default_workspace) / ".reeve"
        else:
            base = state_dir()
        base.mkdir(parents=True, exist_ok=True)

        # The on/off switches plus the user's SOUL.md instructions.
        # Settings-level, outside the memory table; read at engine build time.
        self.memory_settings = MemorySettingsStore(base / "memory-settings.json")
        self.audit_store = AuditStore(database_path(base))
        self.session_store = ConversationStore(base, db_path=database_path(base))
        self.session_store.canonicalize_workspaces()  # collapse /tmp vs /private/tmp etc.
        if self.default_workspace:
            self.session_store.touch_workspace(self.default_workspace)
        self._engines: dict[str, TurnEngine] = {}
        # Sessions whose workspace was promoted mid-turn:
        # evicted from the engine cache at the next mark_idle so the following turn
        # rebuilds fully anchored on the new workspace.
        self._promotion_rebuild: set[str] = set()
        self._running_sessions: set[str] = (
            set()
        )  # sessions with an in-flight turn (busy)
        #: Set once the server starts shutting down: no NEW turn
        #: is admitted, and `aclose` waits — bounded — for the running ones to land so a
        #: turn that is one step from finishing still persists instead of being killed.
        self.shutting_down: bool = False
        # Background `task` children (step D): child session id -> the detached
        # task driving its turn, and -> the parent it notifies when the turn ends. The
        # values are `asyncio.Task`s on the manager loop in the real app; without a running
        # manager loop (`_spawn_subagent_sync`'s `asyncio.run` bridge, a bare test manager)
        # they are the daemon thread that owns the child's private loop instead — either
        # way the key is the child session id and the entry exists exactly while the child
        # is in flight (`_start_background_child`). The completion writer clears it; a
        # Stop reaches the child through its engine, not through this map.
        self._background_tasks: dict[str, Any] = {}
        self._background_parents: dict[str, str] = {}
        #: What a running background child is doing, for the parent's per-turn "still
        #: running" line (background_status.py). Same lifetime as `_background_parents`.
        self._background_labels: dict[str, str] = {}
        #: Per-child git worktrees: child id → `{path, branch, base_head,
        #: root}` while isolation is active. `mark_idle` releases the entry once the
        #: worktree holds no work; one that does stays here so the next idle retries.
        self._child_worktrees: dict[str, dict[str, Any]] = {}
        # Why a worktree that was ASKED for could not be set up, per child, until its
        # result carries it; and what the person answered for a kept one.
        self._worktree_unavailable: dict[str, str] = {}
        self._worktree_handbacks: dict[str, dict[str, tuple[str, int]]] = {}
        #: Revert generation per session (branch fencing):
        #: bumped on every `/undo`. A background child records the parent's generation when
        #: it starts, and a completion whose generation is older than the parent's current
        #: one is DROPPED — otherwise a notification about work from the undone span lands in
        #: the visible tail and the model sees a task it was never told about.
        self._revert_generation: dict[str, int] = {}
        self._background_generation: dict[str, int] = {}
        #: How many clients are OBSERVING each shell job, `session_id -> {task_id: count}`.
        #: Read on the executor's reader thread by `_shell_output` for every
        #: coalesced flush, so it is kept to one dict lookup — in-memory, never a store read
        #: — and it only ever holds jobs that are live: `_shell_finished` drops an entry when
        #: the job settles. Empty (the map, or the session's inner map) means "nobody is
        #: looking, send nothing"; that is why the absence of an entry is not a leak.
        self._shell_observed: dict[str, dict[str, int]] = {}
        #: Guards the counters above. The reader thread only ever READS them, which the GIL
        #: makes atomic; the lock is for the two threads that write a count — the observe
        #: route on the loop and the completion watcher.
        self._shell_observe_lock = threading.Lock()
        #: The pushed jobs roster's pacing (`jobs_roster._jobs_changed`): the timer
        #: a printing job armed, the sessions that have one (read off-loop, a set lookup), and
        #: when a roster with a running job last went out. All written on the loop only.
        self._jobs_timers: dict[str, Any] = {}
        self._jobs_due: set[str] = set()
        self._jobs_sent: dict[str, float] = {}
        #: ...and the sessions with a roster send in flight, and those owed another after it.
        self._jobs_sending: set[str] = set()
        self._jobs_again: set[str] = set()
        # Follow-up prompts a parent sent to a background child that was already running
        # child id -> pending runs.
        # The completion writer drains them after the current turn, so the parent's single
        # notification covers the whole extended chain. Guarded by `_subagent_lock`
        # because the sync task door may append from a foreign thread.
        self._background_followups: dict[str, list[Any]] = {}
        #: Children inside a turn right now, foreground or background: a parent's
        #: message to one of them joins that turn (`queue_steering`). Entered before the turn
        #: runs and left in the same locked step that finds no message the turn missed.
        self._live_child_turns: set[str] = set()
        #: `parent id -> {name: child id}` for the names `task` calls gave their children in
        #: this process. The transcript is the durable record; this only covers a
        #: name whose result is not in the transcript yet.
        self._child_names: dict[str, dict[str, str]] = {}
        self._subagent_lock = threading.Lock()
        # The loop a background child actually runs on; the manager loop in the app, a
        # private daemon loop when there is none (`_detach_background`). `cancel_session`
        # hands the interrupt to that loop thread-safely instead of touching the child
        # engine's asyncio primitives from a foreign thread.
        self._background_loops: dict[str, asyncio.AbstractEventLoop] = {}
        # Child sessions whose Stop arrived in the window between the claim
        # (`try_mark_running`) and the turn's engine existing: `_run_child_turn` reads this
        # once, right after the build, and interrupts then. Without it a background child
        # spawned at the same moment as a Stop could outrun the cancel.
        self._cancel_requested: set[str] = set()
        # Stop flags for EXTERNAL subagent children (codex / claude-code CLI runs):
        # child session id -> the threading.Event its runner polls. `cancel_session`
        # sets them alongside the engine interrupts, so Stop kills the CLI tree too.
        self._external_stops: dict[str, threading.Event] = {}
        # Foreground `task` children that may be promoted to background while they run
        # (P6): child
        # session id -> `{parent_id, future, loop}` parked by `_run_foreground_child`.
        # `promote_subagent` resolves the futures of the session's entries; the entry is
        # removed the moment the spawn returns (promoted or not), so a finished child can
        # never be promoted.
        self._promotable: dict[str, Any] = {}
        # Live child sessions per parent (`parent_id -> child ids`): the per-session
        # concurrency cap (`subagent_max_parallel`) is enforced against this, and the
        # slot is released when the child's turn settles. The thread harness bounded
        # this at 4 from the start; the managed path had no cap.
        self._live_children: dict[str, set[str]] = {}
        # Set whenever a slot comes back: what a foreground spawn past the cap waits on
        # (`_take_child_slot`). Made on first use, inside the running loop.
        self._child_slot_freed: Optional[asyncio.Event] = None
        # Calls queued for a child slot, by parent: (the waiting task, its description).
        self._queued_spawns: dict[str, list[tuple[asyncio.Future, str]]] = {}
        # Live child id -> the ROOT tool call that spawned it (a workflow run answers
        # `<root>:code:<n>`): the map `cancel_call_children` walks when a run dies.
        self._child_call_origins: dict[str, str] = {}
        # The permission broker: `{request_id: PendingPermission}` for
        # every ask a child tool or a `task` spawn is parked on, and the session-scoped
        # "Allow always" grants those replies mint. Session-scoped deliberately, not
        # instance-global — see `permission_broker.py`.
        self._permission_pending: dict[str, Any] = {}
        self._permission_grants: dict[str, list[tuple[str, str]]] = {}
        # Sessions with an auto-title LLM call in flight — one call at a time.
        self._autotitle_inflight: set[str] = set()
        self._autotitle_tasks: set[asyncio.Task] = set()
        self._autotitle_attempts: dict[str, int] = {}
        self.workspace_trust = WorkspaceTrustStore()
        self.secrets = SecretStore()
        # A key under a profile nothing reads any more is removed, not left on disk.
        from harness.safety.secrets import drop_retired_profiles

        drop_retired_profiles(self.secrets)
        # `[compaction]` tables from config.toml, snapshotted per workspace: the engine's
        # due-check runs per step, and re-reading TOML there would put file I/O on the hot
        # path. A file edit applies to sessions built after it (same as every config table).
        self._compaction_tables: dict[str, dict[str, Any]] = {}
        # No explicit provider injected → route by the model's `provider:` prefix (OpenAI default,
        # Ollama, …). Tests inject a provider directly and bypass the router. The same router is
        # shared by every engine and the `/v1/chat/completions` proxy.
        if self.provider is None:
            self.provider = ProviderRouter(
                self.secrets, default_provider="openai", on_use=self._note_provider_use
            )
        self.mcp = MCPManager(secrets=self.secrets)
        # Language servers (`harness/integrations/lsp/`): one client per (workspace root, language),
        # spawned lazily, shared by every session on that root — including subagent
        # engines, which is why the manager is passed down through `build_engine`.
        # Closed in `aclose()`; `SessionManager` never lets a language server outlive it.
        self.lsp = LSPManager()
        # OAuth MCP servers with a sign-in in flight / their last connect error —
        # feeds list_mcp's status so the GUI can show "authorizing…" and failures.
        self._mcp_authorizing: set[str] = set()
        self._mcp_errors: dict[str, str] = {}
        # Pending device grants: server name -> the code the user must paste.
        self._device_flows: dict[str, dict[str, Any]] = {}
        self._background_flows: set = set()  # sign-in pollers, held until they end

        # ChatGPT-subscription provider sign-in in flight / its last error — feeds
        # the providers list + status route so the GUI can show "authorizing…".
        self._codex_authorizing = False
        self._codex_error: Optional[str] = None

        # Claude-subscription provider sign-in in flight / its last error
        self._anthropic_subscription_authorizing = False
        self._anthropic_subscription_error: Optional[str] = None
        # http servers whose anonymous connect came back 401/403 — the failure is
        # "needs sign-in", so the GUI offers the OAuth switch instead of a raw error.
        self._mcp_auth_hints: set[str] = set()
        # Servers that failed to connect while preparing a session's tools —
        # drained once by the WS handler to append a transcript notice.
        self._mcp_session_failures: dict[str, list[str]] = {}
        # What each live session's MCP tools were last armed to (`_mcp_signature`), so a
        # turn can tell "nothing changed" from "re-arm" without touching the network.
        # Read in exactly one place — `arm_mcp` — which is also where a stale entry is
        # dropped, so no engine-eviction site has to know this dict exists.
        self._mcp_armed: dict[str, tuple] = {}
        #: In-flight MCP connects that outlived a turn's arm budget (prepare_mcp_tools).
        self._mcp_pending: dict[tuple[str, str], Any] = {}
        #: The list each server last answered with, on disk: what a session is told a
        #: server offers when its connection is not up yet.
        from harness.extensions.mcp.tool_cache import ToolListCache

        self.mcp_tool_lists = ToolListCache()
        #: Connects a turn did not wait for, whose outcome is still owed to the MCP page.
        self._mcp_watched: set[tuple[str, str]] = set()
        # One SessionLog per session id; see `session_log`.
        self._session_logs: dict[str, SessionLog] = {}
        self.gateway: Optional[Gateway] = None
        self._data_base = base
        # Desktop/UI prefs (default model, onboarding state) — not secrets; a plain JSON file.
        self._prefs = self._load_prefs()
        # The default shell is read by every world built from here on (`shell_kinds.pick_default`).
        from harness.tools.shell_kinds import set_preferred

        set_preferred(self._prefs.get("shell"))
        if self._prefs.get("default_model"):
            self.model = self._prefs["default_model"]
        # Settings ▸ General's default mode for new conversations wins over config.toml's
        # `mode`, as the default model does over `model`; a value no preset reads is ignored.
        if self._prefs.get("default_mode"):
            try:
                self.mode = Mode(self._prefs["default_mode"])
            except ValueError:
                pass
        # The SQLite facts become memory files, once: global → the user
        # memory, workspace → that project's notes. Best-effort; a failure retries next start.
        try:
            from harness.context.memory.migrate_user import migrate_sqlite_facts

            migrate_sqlite_facts(
                database_path(base), self._user_memory(), project_memory_for=self._auto_memory_for
            )
        except Exception:  # noqa: BLE001 — memory must never stop the app from starting
            logger.warning("moving the SQLite memories into files failed", exc_info=True)
        # The old per-conversation scratch memories go to projects/.archive/.
        try:
            from harness.context.memory.scratch_archive import archive_scratch_memories

            scratch_dir = self._auto_memory_for(str(self.scratch_base().expanduser().resolve()))
            archive_scratch_memories(base / "projects", scratch_dir.root.parent.name)
        except Exception:  # noqa: BLE001
            logger.warning("archiving the scratch memories failed", exc_info=True)
        # Seed the PDF-fallback module global from prefs so engines see the user's
        # choice from the first turn (set_pdf_settings keeps it in sync after).
        from harness.llm.pdf_support import set_fallback_mode

        set_fallback_mode(self.pdf_settings()["pdf_fallback"])
        # Per-session live-view registry: every socket open on a session id gets the turn's events,
        # whoever drives the turn (foreground user_message, channel delivery, self-wake, resume).
        # Delivery itself is socket-independent — this only governs *live visibility*.
        self._session_clients: dict[str, set[Any]] = {}
        # The PERSON's shells (the `/terminal` panel), one per session, deliberately
        # separate from each engine's `terminals` (the model's tools): both read through
        # the same pipe-backed TerminalSession, but the agent's reader drains a cursor
        # and would consume output the panel is watching. See user_terminal.py.
        self._user_shells: dict[str, Any] = {}
        self._user_shell_subs: dict[str, set[Any]] = {}
        self._user_shell_lock = threading.Lock()
        # App-wide event sockets (/ws/events): session-independent pushes — today the
        # automation-run-started toast; badges could ride it later.
        self._event_clients: set[Any] = set()
        # Automation: scheduled tasks store + the tick scheduler (started in the lifespan).
        # The scheduler also resumes self-wake'd sessions each tick (extra_tick).
        self.task_store = TaskStore(base / "automation.db")
        self.scheduler = Scheduler(
            self.task_store, self._run_scheduled_task, extra_tick=self._scheduler_tick
        )
        # Ops: the pre-aggregated metrics fold (one row per session, one per turn) that
        # the Ops surface reads. Written by `save()` + a background tick; see
        # metrics_api.py. The debounce map keeps `flush_session`'s frequent saves from
        # folding the whole log each time.
        self.metrics_store = MetricsStore(base / "metrics.db")
        # The page's self-review (failure shapes, claims), run in a child process and kept on disk.
        self.self_review = SelfReview(base, on_done=lambda: self._announce_metrics_changed(""))
        self._metrics_folded_at: dict[str, float] = {}
        self._metrics_lock = threading.Lock()
        # A step-triggered fold waits here, one timer per session, so a burst of steps is one fold
        # that reads the newest log. One fold runs at a time: a slower, older one must not
        # overwrite a newer one.
        self._metrics_pending: dict[str, threading.Timer] = {}
        self._metrics_fold_lock = threading.Lock()
        self._metrics_ticker_started = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        # The prompts that wait on the person (kept on disk, so one survives a restart), and
        # self-wake records.
        self.inbox = InboxStore(base / "inbox.json")
        self.wakes = WakeStore(base / "wakes.json")
        # What was running when the last process ended: turn claims and background jobs.
        # Read once at startup by `resume_interrupted_work`.
        self.continuity = ContinuityStore(base / "continuity.json")
        # Goals (`harness/loop/goals.py`): one in-flight driver pass per session plus the
        # requests coalesced onto it, and the startup disarm. A process that restarted
        # mid-goal must never keep opening rounds on its own; disarm is ACTIVATION-only
        # (process-local), so the durable phase is untouched and a reopened session shows
        # an inactive goal until someone resumes it.
        self._goal_inflight: set[str] = set()
        self._goal_requested: set[str] = set()
        # A goal round's engine facts, captured before its idle edge.
        self._goal_round_facts: dict[str, Any] = {}
        try:
            disarmed = GoalStore.disarm_all()
            if disarmed:
                logger.info("disarmed %d armed goal(s) at startup", disarmed)
        except Exception:
            logger.warning("goal startup disarm failed", exc_info=True)
        # Channel subscriptions (inbound): persisted (session_id, channel) records + a ring buffer
        # of recently-seen channel messages for get_channel_messages.
        self.subscriptions = SubscriptionStore(base / "subscriptions.json")
        self.channel_buffer = ChannelBuffer(state_path=base / "channels.json")
        # Mention router: thread target → the session that owns that Slack thread.
        # Also the durable source of the thread's standing send_message grant (re-seeded
        # onto the engine in get_engine).
        self.mention_sessions = MentionSessionStore(base / "mention_threads.json")
        # Unauthorized inbound messages, parked instead of dropped (one-step allow-and-deliver).
        self.parked = ParkedStore(base / "parked.json")
        # People directory: "platform:user_id" → display name, noted from every inbound
        # (authorized or parked) so allow-list chips read "Sam Okafor", not "U07JK…".
        self._people_path = base / "people.json"
        try:
            self._people: dict[str, str] = json.loads(self._people_path.read_text())
        except (OSError, ValueError):
            self._people = {}
        # Seed from already-parked messages (they carry resolved names) so an allow made from
        # an old parked item still gets a named chip.
        for it in self.parked.list():
            if it.get("user_name"):
                self._people.setdefault(
                    f"{it['platform']}:{it['user_id']}", it["user_name"]
                )
        # Per-session connector overrides. With the connected set they make the
        # session's effective connectors, which gate inbound delivery and the engine's connector
        # tools.
        self.session_connections = SessionConnectionStore(
            base / "session_connections.json"
        )
        # Skills: folder-backed CRUD + per-session mutes. The effective menu
        # gates the engine's skill catalog the same way effective_connectors gates connector
        # tools — one resolver feeds the catalog injection, the rail, and the composer popup.
        self.skill_store = SkillStore()
        self.session_skills = SessionSkillStore(base / "session_skills.json")
        # Dead-letter: inbound messages with no destination + background-turn failures, so neither
        # vanishes silently (a debugging/visibility surface, not a redelivery queue).
        self.unrouted = UnroutedStore(base / "unrouted.json")
        # `/share` records (harness/interface/server/manager/shares.py): what a session's configured
        # endpoint returned, so `/unshare` can revoke after a restart. There is no hosted
        # backend — an empty [share] endpoint keeps the command disabled with that reason.
        self.share_store = ShareStore(base / "shares.json")
        # SSH environment profiles. `register_remote` attaches the app's store
        # here; a bare manager (tests, CLI) has none and refuses remote bindings rather
        # than silently pretending to build a world it cannot reach.
        self.remote_environments: Optional[Any] = None

    # -- workspaces -------------------------------------------------------------
    def open_workspace(self, path: str, *, create: bool = False) -> dict[str, Any]:
        resolved = Path(path).expanduser()
        if resolved.exists() and not resolved.is_dir():
            return {"path": str(resolved), "ok": False, "error": "not a directory"}
        if not resolved.exists():
            if not create:
                return {
                    "path": str(resolved),
                    "ok": False,
                    "error": "folder does not exist",
                }
            try:
                resolved.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                return {"path": str(resolved), "ok": False, "error": str(exc)}
        resolved = resolved.resolve()
        self.session_store.touch_workspace(str(resolved))
        return {
            "path": str(resolved),
            "ok": True,
            "git_branch": git_branch(resolved),
            "command_trust": self.workspace_command_trust(resolved),
        }

    def workspace_command_trust(self, path: str | Path) -> dict[str, Any]:
        if not str(path).strip():
            return {
                "workspace": "",
                "requested_commands": [],
                "trusted": False,
                "required": False,
            }
        canonical = WorkspaceTrustStore.canonical(path)
        commands = (
            workspace_allowed_commands(canonical)
            if Path(canonical).is_dir()
            else []
        )
        trusted = self.workspace_trust.is_trusted(canonical)
        return {
            "workspace": canonical,
            "requested_commands": commands,
            "trusted": trusted,
            "required": bool(commands and not trusted),
        }

    def _mcp_workspace_trusted(self, workspace: Optional[str | Path]) -> bool:
        """Whether workspace `.reeve/mcp.json` may be loaded (#213).

        Same consent boundary as repository ``allowed_commands``: an untrusted
        clone must not define stdio processes that spawn at session open.
        """
        return bool(workspace and self.workspace_trust.is_trusted(workspace))

    def set_workspace_trust(
        self, path: str | Path, *, trusted: bool
    ) -> dict[str, Any]:
        if not str(path).strip():
            return {"ok": False, "error": "workspace path is required"}
        candidate = Path(path).expanduser()
        if trusted and not candidate.is_dir():
            return {"ok": False, "error": "workspace is not a directory"}
        canonical = self.workspace_trust.set_trusted(candidate, trusted)
        effective = load_config(
            canonical, workspace_trusted=trusted
        ).allowed_commands
        # Apply trust/revocation immediately to live sessions rooted at this exact path.
        for engine in self._engines.values():
            engine_workspace = str(
                (getattr(engine, "audit_context", {}) or {}).get("workspace", "")
            )
            if engine_workspace and WorkspaceTrustStore.canonical(
                engine_workspace
            ) == canonical:
                engine.permissions.allowed_commands = list(effective)
        return {
            "ok": True,
            **self.workspace_command_trust(canonical),
        }

    def trusted_workspaces(self) -> list[dict[str, Any]]:
        return [
            {
                **self.workspace_command_trust(path),
                "exists": Path(path).is_dir(),
            }
            for path in self.workspace_trust.list()
        ]

    def recent_workspaces(self) -> list[dict[str, Any]]:
        """Recent real projects for the folder gate. Per-conversation scratch dirs are
        excluded — they're workspaces to the session store, but never something a user
        should re-open as a 'project'."""
        scratch = self.scratch_base().resolve()
        out = []
        for path in self.session_store.recent_workspaces():
            p = Path(path)
            try:
                if p.resolve().is_relative_to(scratch):
                    continue
            except OSError:
                pass
            out.append({"path": path, "name": p.name, "exists": p.is_dir()})
        return out

    def workspace_worktrees(self, path: Optional[str] = None) -> dict[str, Any]:
        """The workspace's git worktrees (the `/workspace` sidebar section,
        a toggle). Read-only: listing is the v1 verb, and a non-repo answers
        `is_git: false` rather than an error the caller has to translate."""
        target = self.resolve_workspace(path)
        if not target:
            return {
                "is_git": False,
                "root": "",
                "worktrees": [],
                "error": "no workspace folder",
            }
        return list_worktrees(target)

    DEFAULT_SCRATCH_BASE = "~/Reeve"

    def scratch_base(self) -> Path:
        """Common area for per-conversation scratch directories. Configurable via prefs;
        the env override keeps tests (and any sandboxed run) out of the real home dir —
        universal scratch means every session provisions here, not just orphan ones."""
        base = (
            self._prefs.get("scratch_base")
            or os.environ.get("REEVE_SCRATCH_BASE")
            or self.DEFAULT_SCRATCH_BASE
        )
        return Path(base).expanduser()

    def _provision_scratch(self, session_id: str) -> str:
        """Create (idempotently) and return this conversation's scratch directory."""
        d = self.scratch_base() / session_id
        d.mkdir(parents=True, exist_ok=True)
        return str(d.resolve())

    def _scratch_path(self, session_id: str) -> str:
        """This conversation's scratch directory, NOT created: what a session is built with.

        Building an engine is not using one. The GUI builds one the moment a new conversation
        opens, so creating the folder there left one behind for every conversation opened and
        never sent: 879 folders with no session in one person's scratch area, 793 of them empty
        (2026-10-18). The engine makes it when a turn starts (`TurnEntryMixin._make_scratch`),
        and the terminal or an attachment when one of them needs it first."""
        return str((self.scratch_base() / session_id).expanduser().resolve())

    def sweep_unused_scratch(self, *, older_than_s: float = 86_400) -> int:
        """Remove the EMPTY scratch folders no conversation owns: left by builds before
        `_scratch_path`, and by conversations that were never sent. It runs at startup.

        Only `rmdir`, which cannot remove a folder that holds anything, so nothing a person or an
        agent wrote is lost; a folder whose name is not a session id, one a session record owns,
        one a live engine holds, and one younger than a day (another process sharing the folder
        may be building it) are left alone. Returns how many went."""
        base = self.scratch_base()
        try:
            entries = list(base.iterdir())
        except OSError:
            return 0
        now = time.time()
        removed = 0
        for folder in entries:
            name = folder.name
            if not self._SESSION_ID_RE.match(name) or name in {".", ".."} or name in self._engines:
                continue
            try:
                if not folder.is_dir() or folder.is_symlink() or now - folder.stat().st_mtime < older_than_s:
                    continue
                if any(folder.iterdir()) or self.session_store.load(name) is not None:
                    continue
                folder.rmdir()
                removed += 1
            except Exception:  # noqa: BLE001
                continue
        return removed

    _SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")

    def is_temp_workspace(self, path: Optional[str]) -> bool:
        """True when `path` is a per-conversation temporary directory (lives under the
        scratch base). The GUI uses this to label the folder "Temporary folder" instead
        of exposing its raw path."""
        if not path:
            return False
        try:
            return (
                Path(path).expanduser().resolve().is_relative_to(self.scratch_base().resolve())
            )
        except OSError:
            return False

    def provision_temp_workspace(self, session_id: str, *, git: bool = True) -> dict[str, Any]:
        """The setup row's "Start in a temporary folder": create the conversation's temporary
        directory at SEND time (not connect) and, for code-family work, make git ready.
        Idempotent — re-sending against an existing dir is a no-op."""
        if not self._SESSION_ID_RE.match(session_id or "") or session_id in {".", ".."}:
            return {"ok": False, "error": "invalid session id"}
        path = self._provision_scratch(session_id)
        if git and not (Path(path) / ".git").is_dir():
            try:
                spawn_run(
                    ["git", "init", "-q"],
                    cwd=path,
                    capture_output=True,
                    timeout=10,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError):
                pass  # no git on PATH → still a usable folder, just not a repo
        return {"ok": True, "path": path, "git": (Path(path) / ".git").is_dir()}

    def save_temp_as_project(self, session_id: str, dest: str) -> dict[str, Any]:
        """The setup row's "Save as project…": move a session's temporary folder to a real
        location and rebind the session there. The cached engine is dropped so the next
        connect rebuilds against the new path — callers must reconnect after this."""
        if not dest or not dest.strip():
            return {"ok": False, "error": "no destination folder"}
        record = self.session_store.load(session_id)
        src = record.workspace if record and record.workspace else None
        if not src:
            engine = self._engines.get(session_id)
            src = session_workspace(engine) if engine else None
        if not src or not self.is_temp_workspace(src) or not Path(src).is_dir():
            return {"ok": False, "error": "this session is not in a temporary folder"}
        if self.is_running(session_id):
            return {"ok": False, "error": "wait for the current task to finish first"}
        d = Path(dest).expanduser()
        if d.exists():
            if not d.is_dir() or any(d.iterdir()):
                return {"ok": False, "error": "destination must be a new or empty folder"}
            d.rmdir()  # shutil.move into an existing dir would nest src inside it
        # Windows refuses to rename a directory a live process is using as its cwd, and
        # this session's shell starts in exactly this folder (a background task's process
        # may still be running there): close the executor (and any
        # terminal) before the move, or "Save as project" fails with WinError 32 on any
        # session that has ever run a command. The engine is dropped below and rebuilt
        # against the new path.
        engine_for_move = self._engines.get(session_id)
        if engine_for_move is not None:
            for closer in (
                getattr(getattr(engine_for_move, "executor", None), "close", None),
                getattr(getattr(engine_for_move, "terminals", None), "close_all", None),
            ):
                if callable(closer):
                    try:
                        closer()
                    except Exception:
                        pass  # best-effort: the move below is the operation that matters
        # The person's shell holds the folder as its cwd too (user_terminal.py): close it
        # before the move for the same Windows reason.
        self.user_terminal_close(session_id)
        try:
            d.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(src, str(d))
        except OSError as e:
            return {"ok": False, "error": f"could not move the folder: {e}"}
        new_path = str(d.resolve())
        if record:
            record.workspace = new_path
            self.session_store.save(record)
        self._engines.pop(session_id, None)
        self.session_store.touch_workspace(new_path)
        return {"ok": True, "path": new_path}

    def resolve_workspace(self, requested: Optional[str]) -> Optional[str]:
        if requested:
            p = Path(requested).expanduser()
            if p.is_dir():
                return str(p.resolve())
            return None
        return self.default_workspace

    # -- engines ----------------------------------------------------------------
    def engine_workspace(
        self, session_id: str, *, workspace: Optional[str] = None, agent: str = "reeve"
    ) -> Optional[str]:
        """The workspace `get_engine` would bind — for prepping MCP tools beforehand."""
        record = self.session_store.load(session_id)
        if record:
            return record.workspace or None
        return self.resolve_workspace(workspace)

    # -- remote environments --------------------------------
    @staticmethod
    def environment_public(
        profile: Optional[dict[str, Any]],
    ) -> Optional[dict[str, Any]]:
        """The wire shape of a bound environment: what a surface needs to render
        `user@host:path` and nothing an ssh invocation needs.

        `identity_file` is a LOCAL path and never crosses this boundary — the store
        owns it, the session wire has no reason to carry it (phase 2b-1's rule for
        GET, now the one rule for PATCH and the sessions list too).
        """
        if not profile:
            return None
        return {
            "id": profile.get("id"),
            "name": profile.get("name", ""),
            "user": profile.get("user") or None,
            "host": profile.get("host", ""),
            "port": profile.get("port", 22),
        }

    @staticmethod
    def _validate_remote_workspace(path: Any) -> str:
        """The remote workspace anchor: an absolute POSIX path, normalized.

        Controller-side validation only — whether the path EXISTS is the remote host's
        answer, and asking it is the session-build probe's job (never a route that would
        block on a network hop).
        """
        text = str(path or "").strip()
        if not text:
            raise ValueError("a remote workspace path is required")
        if any(character in text for character in ("\0", "\n", "\r")):
            raise ValueError(
                "the remote workspace path cannot contain newlines or NUL bytes"
            )
        if not posixpath.isabs(text):
            raise ValueError("the remote workspace path must be absolute (start with /)")
        return posixpath.normpath(text)

    def set_session_environment(
        self,
        session_id: str,
        environment_id: Optional[str],
        workspace: Optional[str] = None,
    ) -> dict[str, Any]:
        """Bind, switch, or clear a session's SSH remote environment (phase 2b).

        `environment_id` is a profile id, or None/""/"local" to clear the binding.
        A remote bind requires an absolute POSIX `workspace`; a local switch accepts a
        local folder (or falls back to the configured default workspace). The cached
        engine is dropped — the world is part of the build, so the next connect rebuilds
        against the new one. Refused mid-turn: the running loop already holds a world.
        """
        if self.is_running(session_id):
            return {
                "ok": False,
                "error": "the session is mid-turn — switch environments once the turn settles",
            }
        env_id = str(environment_id or "").strip() or None
        if env_id in ("local", "none"):
            env_id = None
        profile: Optional[dict[str, Any]] = None
        target: Optional[str] = None
        if env_id is not None:
            store = self.remote_environments
            if store is None:
                return {
                    "ok": False,
                    "error": "remote environments are not available in this session host",
                }
            try:
                profile = store.get(env_id)
            except KeyError:
                return {
                    "ok": False,
                    "error": f"No remote environment with id '{env_id}'.",
                }
            try:
                target = self._validate_remote_workspace(workspace)
            except ValueError as error:
                return {"ok": False, "error": str(error)}
        else:
            if workspace:
                local = Path(str(workspace)).expanduser()
                if not local.is_dir():
                    return {"ok": False, "error": "the workspace folder does not exist"}
                target = str(local.resolve())
            else:
                record = self.session_store.load(session_id)
                if record is not None and record.environment_id:
                    # Clearing a remote binding must land somewhere real; the default
                    # workspace is the only local anchor the manager owns.
                    target = self.default_workspace
                    if not target:
                        return {
                            "ok": False,
                            "error": "choose a local folder to switch to",
                        }
        existed = self.session_store.load(session_id) is not None
        self.session_store.set_environment(session_id, env_id, target)
        if not existed:
            # The binding can land before the session's first turn (the client picks a
            # host on the welcome screen), and `set_environment` creates a bare row —
            # seed the defaults the first `save()` would otherwise supply, so the next
            # engine build reads a usable model/mode instead of NULLs.
            self.session_store.save(
                SessionRecord(
                    session_id=session_id,
                    workspace=target or "",
                    model=self.model,
                    mode=self.mode.value,
                    messages=[],
                    agent="reeve",
                ),
                touch=False,
            )
        self._engines.pop(session_id, None)
        return {
            "ok": True,
            "session_id": session_id,
            "environment_id": env_id,
            "workspace": target,
            # The path ON THE HOST when bound (a POSIX path — never a local folder);
            # null when this call landed back on Local. Surfaces render the remote
            # readout from this, so `workspace` can never leak into `user@host:port`.
            "remote_workspace": target if env_id else None,
            # The reduced wire shape: name/user/host/port, never identity_file.
            "environment": self.environment_public(profile),
        }

    def session_summary(self, session_id: str) -> dict[str, Any]:
        """One session's identity + its environment binding (GET /v1/sessions/{id})."""
        record = self.session_store.load(session_id)
        if record is None:
            return {"ok": False, "error": "session not found"}
        environment_id = getattr(record, "environment_id", None) or None
        profile: Optional[dict[str, Any]] = None
        if environment_id and self.remote_environments is not None:
            try:
                profile = self.remote_environments.get(environment_id)
            except KeyError:
                profile = None
        return {
            "ok": True,
            "session_id": record.session_id,
            "workspace": record.workspace,
            # A bound session's `workspace` IS the POSIX path on the host; this names
            # it explicitly so a surface's remote readout never has to infer it (and
            # can never fall back to the local workspace state instead). Null locally.
            "remote_workspace": (record.workspace or None) if environment_id else None,
            "environment_id": environment_id,
            # The display/readout fields only (user@host:port + name) — never
            # identity_file (a local path the session wire has no reason to carry).
            "environment": self.environment_public(profile),
            "agent": resolve_agent_id(record.agent),
            "model": record.model,
            "mode": record.mode,
            "title": record.title or "",
            "pinned": record.pinned,
            "archived": record.archived,
            "kind": record.kind,
            "updated_at": record.updated_at,
        }

    def lsp_status(self) -> dict[str, Any]:
        """The shared language-server pool's live state (GET /v1/lsp → the Status popover)."""
        return self.lsp.snapshot()

    def get_engine(
        self,
        session_id: str,
        *,
        workspace: Optional[str] = None,
        agent: str = "reeve",
        approver: Optional[Approver] = None,
        extra_tools: Optional[list[Any]] = None,
        plan_approver: Optional[Any] = None,
        question_asker: Optional[Any] = None,
        tool_requester: Optional[Any] = None,
    ) -> Optional[TurnEngine]:
        engine = self._engines.get(session_id)
        if engine is not None:
            if approver is not None:
                engine.approver = approver
            if plan_approver is not None:
                engine.plan_approver = plan_approver
            if question_asker is not None:
                engine.question_asker = question_asker
            if tool_requester is not None:
                engine.tool_requester = tool_requester
            return engine

        record = self.session_store.load(session_id)
        agent_name = resolve_agent_id((record.agent if record else agent) or "")
        ag = get_agent(agent_name)

        # The SSH binding is resolved HERE, before any path handling:
        # a bound session's record.workspace is a path on the remote host, so every local
        # branch below (directory check, scratch provisioning, recents) must be skipped
        # rather than asked to make sense of it.
        environment_id = (
            (getattr(record, "environment_id", None) or None) if record else None
        )
        remote_profile: Optional[dict[str, Any]] = None
        remote_runner: Optional[Any] = None
        remote_password: Optional[str] = None
        remote_root = ""
        if environment_id:
            store = self.remote_environments
            if store is None:
                raise ValueError(
                    f"session {session_id} is bound to remote environment "
                    f"{environment_id!r}, but this host has no remote-environment store"
                )
            try:
                remote_profile = store.get(environment_id)
            except KeyError:
                raise ValueError(
                    f"session {session_id} is bound to remote environment "
                    f"{environment_id!r}, which no longer exists"
                ) from None
            # Tests inject a FakeSsh through the store's runner seam; the engine build
            # must reach the same seam rather than the real ssh binary.
            remote_runner = getattr(store, "_runner", None)
            # The remembered password (password auth), loaded through the OS
            # credential store when the profile says one is remembered. A store that
            # cannot read it raises the manager's ValueError vocabulary, so the session
            # start says why instead of silently attempting key auth.
            remote_password = store.load_password(environment_id)

        if record:
            ws = record.workspace or None
            # Legacy stored modes map onto the 3 presets; a stored raw `plan` was a MODE
            # and its read-only/overlay state must survive as the plan overlay (Mode.migrated).
            mode, plan = Mode.migrated(record.mode)
            plan = plan or bool(getattr(record, "plan", False))
            model, messages = record.model, record.messages
        else:
            ws = self.resolve_workspace(workspace)
            model, mode, messages = self.model, self.mode, None
            plan = False

        if remote_profile is not None:
            # The remote workspace is the anchor the RemoteWorld is built against; `ws`
            # stays None so no local instruction/config/memory path is read from it.
            remote_root = str(ws or "")
            if not remote_root:
                raise ValueError(
                    f"session {session_id} is bound to a remote environment but has no "
                    "remote workspace path"
                )
            ws = None
        elif not ws or not Path(ws).is_dir():
            # Sessions without a folder start "orphan" on a per-conversation scratch directory
            # (generalizes MyHelper's auto-workspace), named here and made when a turn starts
            # (`TurnEntryMixin._make_scratch`). Folder-gated personas (requires_folder) still demand a real
            # directory picked by the user.
            if not ag.requires_folder:
                ws = self._scratch_path(session_id)
            else:
                return None

        if ws:
            self.session_store.touch_workspace(ws)
        # Universal scratch: EVERY session is multi-root
        # with a per-conversation scratch dir. Orphan sessions run ON their scratch
        # (ws == scratch, primary). Sessions on a real folder — gated personas, or a
        # temp-workspace pick that later became a project — keep that folder primary and
        # gain scratch as a second writable root, so deliverables/temp files have a home
        # that never dirties the user's repo. A remote session's roots are remote POSIX paths, built by
        # `build_engine` from the world itself (no local scratch dir exists).
        roots = None
        if ws:
            extra = [
                r
                for r in ((record.extra_roots if record else []) or [])
                if not r.get("remote") and Path(str(r.get("path", ""))).is_dir()
            ]
            if self.is_temp_workspace(ws):
                roots = [{"path": ws, "writable": True, "label": "scratch"}, *extra]
            elif self._SESSION_ID_RE.match(session_id or "") and session_id not in {".", ".."}:
                roots = [
                    {"path": ws, "writable": True, "label": "workspace"},
                    {
                        "path": self._scratch_path(session_id),
                        "writable": True,
                        "label": "scratch",
                    },
                    *extra,
                ]
            else:
                # A session id we won't put in a filesystem path: primary root only.
                roots = [{"path": ws, "writable": True, "label": "workspace"}, *extra]
        elif remote_profile is not None:
            # A remote grant's persisted roots (tagged `remote: true` when it landed)
            # ride the record; the local geometry never sees them and vice versa. The
            # workspace stays index 0; `build_engine` merges these into its root list.
            remote_extra = [
                r
                for r in ((record.extra_roots if record else []) or [])
                if isinstance(r, dict)
                and r.get("remote") is True
                and str(r.get("path", "")).startswith("/")
            ]
            roots = [
                {
                    "path": str(r.get("path")),
                    "writable": bool(r.get("writable", False)),
                    "label": str(r.get("label") or ""),
                    **({"reached": True} if r.get("reached") else {}),
                }
                for r in remote_extra
            ] or None
        # Auto memory as markdown (Claude-shaped): a machine-local per-project directory
        # OUTSIDE the workspace, so the agent's own notes never pollute `git status`.
        # Gated by the Settings switch; None → no tool and no injected index, exactly as
        # before this feature existed. A remote session keys by its remote path — the
        # notes still live on this machine, one bucket per remote workspace.
        workspace_key = remote_root or ws
        am_files = None
        if self.memory_settings.auto_memory:
            am_files = self._auto_memory_for(self._memory_key_for(record, workspace_key))
        engine = build_engine(
            agent=ag,
            workspace=workspace_key,
            environment=remote_profile,
            environment_runner=remote_runner,
            environment_password=remote_password,
            # The process's shared language-server pool (see `self.lsp` above).
            lsp_manager=self.lsp,
            model=model,
            mode=mode,
            # The plan OVERLAY, independent of the preset (`/plan`). Restored from
            # the record, including a legacy bare `plan` mode translated by `Mode.migrated`.
            plan=plan,
            # The session's saved thinking level; None for a brand-new session (the
            # provider's own default rides). Handled like `model`/`mode` above.
            thinking_level=(record.thinking if record else None),
            provider=self.provider,
            # Item 2.5: the store itself, not a snapshot — a search late in a long
            # session must see the turns that happened after the engine was built.
            conversation_store=self.session_store,
            # Callable, not a snapshot: editing your instructions in Settings applies
            # to conversations already open.
            user_rules=lambda: self.memory_settings.prompt_rules(),
            # Auto memory (markdown): injected MEMORY.md index + the `auto_memory` tool.
            auto_memory=am_files,
            auto_memory_enabled=lambda: self.memory_settings.auto_memory,
            messages=messages,
            extra_tools=list(extra_tools or []) or None,
            secrets=self.secrets,
            task_store=self.task_store,
            wake_store=self.wakes,
            # The `task` tool spawns a real child session through the manager (step B
            # of child sessions): the sync bridge because the tool runs in a worker thread while the
            # child's turn runs on the server loop. None on direct build_engine callers
            # (CLI, tests), which keeps the thread harness as their only path.
            subagent_spawner=self._spawn_subagent_sync,
            # Which of this session's background children are still in flight, read each
            # turn into the live context (background_status.py).
            background_status=lambda sid=session_id: "\n\n".join(
                part
                for part in (background_context(self, sid), handback_context(self, sid))
                if part
            ),
            # A dead workflow run stops its in-flight `agent()` children through the same
            # interrupt tree the Stop button pulls (they are ordinary `task` sessions).
            subagent_cancel_origin=self.cancel_call_children,
            # A `workflow` call runs in the background as a child session. Only
            # a root session gets the door: a child's own workflow waits, like its `task`.
            workflow_spawner=(
                None
                if self.session_depth(session_id) > 0
                else lambda call_id, arguments, on_metadata, sid=session_id: self.spawn_workflow(
                    parent_id=sid, call_id=call_id, arguments=arguments, on_metadata=on_metadata
                )
            ),
            # The caller's ancestry depth for the `subagent_depth` check (P5):
            # the ancestry is walked when the task tool runs, so the manager hands the walker
            # down instead of a snapshot — this session may gain children as it works.
            session_depth=lambda sid=session_id: self.session_depth(sid),
            # The tool emits the parent's live `tool_metadata` frames through this sync
            # emitter while the child works (step C) — the tool run is on a
            # worker thread, the broadcast is not.
            subagent_metadata=self._subagent_metadata_emitter(session_id),
            # The `task` tool's spawn ask (P1): the tool asks the session-scoped broker
            # before spawning when the ruleset says `ask`:
            # permission "task", patterns [subagent_type], always ["*"].
            # None on every non-manager build, so the CLI/thread path is unchanged.
            permission_asker=self._permission_asker(session_id),
            # The session's goal record (same `scheduling` gate as self-wake): the model
            # gets the `goal` tool, the driver above opens the rounds.
            goal_store=GoalStore.for_session(session_id),
            session_id=session_id,
            audit_sink=self.audit_store.append,
            roots=roots,
            # A session with a window passes callbacks that show the prompt in it. A run with
            # none (background, self-wake, durable resume) parks its prompt, so a rebuilt
            # engine can still be answered (and, on resume, an item already answered returns
            # at once); the person finds it when they open the conversation.
            approver=approver or self.inbox_approver(session_id, agent),
            plan_approver=plan_approver or self.inbox_plan_approver(session_id, agent),
            question_asker=question_asker
            or self.inbox_question_asker(session_id, agent),
            tool_requester=tool_requester,
            subscription_store=self.subscriptions,
            channel_buffer=self.channel_buffer,
            # Per-session connection hierarchy: expose only effective-enabled connectors' tools.
            connector_filter=self.effective_connectors(session_id),
            # Per-session skill menu, LIVE: a callable so load_skill sees
            # disables/new skills immediately; the catalog snapshot is taken at build.
            skill_filter=lambda sid=session_id, w=ws, a=agent_name: (
                self.effective_skill_names(sid, w, agent=a)
            ),
            # Progressive tool discovery: MCP schemas off the wire, behind the
            # search bridge. Same prefs-backed, next-build semantics.
            tool_discovery=self.tool_discovery(),
            visuals=self.visuals(),
            # Whether a delegation call may pick its own
            # route. Same prefs-backed, next-build semantics.
            model_selection=self.subagent_model_selection(),
        )
        # A call that reaches outside the session's folders, once allowed, adds the folder as a
        # root the way the Files panel does: persisted, announced, removable there, and
        # marked as reached, so its read-only start is not taken for a person's refusal.
        engine.root_granter = lambda path, writable, sid=session_id: _granted_root(
            self.add_root(sid, path, writable=writable, reached=True)
        )
        # An automation run rebuilt here (manual "Run now" over WS, durable resume) still
        # carries its task's standing allowances — the rules live on the task record.
        owning_task = self.task_store.task_for_run_session(session_id)
        if owning_task is not None:
            self._seed_task_permissions(engine, owning_task)
        # A mention-spawned session keeps its in-thread reply pre-approved across
        # rebuilds/restarts — the grant is re-derived from the durable thread map.
        for thread_target in self.mention_sessions.targets_for(session_id):
            engine.permissions.task_rules.setdefault("send_message", set()).add(
                thread_target
            )
        if record is not None and record.grants:
            self._apply_grants(engine, record.grants)
        # Auto-compaction: restore the persisted view boundary and wire the live
        # Settings getter — post-construction, so build_engine's signature stays put.
        if record is not None and record.compaction:
            from harness.context.compaction import CompactionState

            engine.compaction_state = CompactionState.from_dict(record.compaction)
        engine.compaction_settings = lambda: self.compaction_settings(ws)
        # Message-level revert: a reloaded session keeps its staged marker, so
        # the truncated outbound view survives a restart exactly like the transcript does.
        if record is not None and record.revert:
            engine.revert_state = dict(record.revert)
        # Makes the turn's record durable before a consequential tool runs
        # (`harness/session/durability.py`). A closure rather than a plain function because the
        # check needs the engine's live message list, and installed here rather than in
        # `build_engine` because only a host that owns a store can answer the question at
        # all — the CLI and the bench deliberately leave `engine.flush` as None.
        engine.flush = lambda: self.flush_session(session_id, engine)
        # The session event log, beside this session's messages (session_log.py). Attached
        # here rather than in `build_engine` for the same reason compaction settings are:
        # the engine is usable without a session, and a script or a test should not have to
        # own a file to run one.
        engine.log = self.session_log(session_id)
        from harness.extensions.plugins import log_active

        log_active(engine.log, ws, reason="reload" if self.take_plugin_reload(session_id) else "")
        # Background shell tasks report their own completion (see
        # `_deliver_shell_result`): the model should do other work or end its reply
        # instead of polling with `sleep`/`ps`, and each poll costs a full-context
        # round trip (measured: a 61-step session spent 20% of its tokens polling).
        # Wired here, post-construction, like the settings getter above.
        executor = getattr(engine, "executor", None)
        setter = getattr(executor, "set_background_listener", None)
        if callable(setter):
            setter(lambda info: self._shell_finished(session_id, info))
        # ...and the LIVE one: a running job's output as it is read, so a jobs panel that
        # anchored with the tail route appends `job_output` frames instead of re-reading the
        # tail every second. Wired here beside the completion listener because it is the same
        # executor, the same thread discipline and the same fence.
        output_setter = getattr(executor, "set_background_output_listener", None)
        if callable(output_setter):
            output_setter(lambda info: self._shell_output(session_id, info))
        # ...and the roster: a job that joins the list or leaves it is pushed, so a panel
        # shows it when it happens and not when it next asks.
        roster_setter = getattr(executor, "set_background_roster_listener", None)
        if callable(roster_setter):
            roster_setter(lambda: self._roster_changed(session_id))
        # ...and the fence: a task stamped before an `/undo` is dropped on completion
        # rather than waking the session over work the person just undid.
        generation_setter = getattr(executor, "set_generation_provider", None)
        if callable(generation_setter):
            generation_setter(lambda: self._revert_generation.get(session_id, 0))
        self._engines[session_id] = engine
        return engine


def _granted_root(result: Any) -> Any:
    """`add_root`'s answer as a grant: a refusal (not a folder, a remote host that said no) is
    raised, so the engine says on the transcript why the call still could not reach it."""
    if isinstance(result, dict) and result.get("ok") is False:
        raise RuntimeError(str(result.get("error") or "the folder could not be added"))
    return result
