"""CoreMixin: building the engine, and the state it shares across its parts.

`__init__` takes the collaborators and settings a caller chooses, then sets up state in named groups.
Most of what is not a constructor argument is set after construction by whoever owns it (the manager,
`build_engine`), so the argument list stays as it is and every group has a safe default: a helper
called directly from a test or a script must not raise before a turn has ever run.
"""

from __future__ import annotations

import asyncio
import threading
from collections import deque
from typing import Any, Awaitable, Callable, Optional

from harness.context import compaction, session_facts
from harness.llm.providers import ProviderClient
from harness.llm.providers.fallback import RouteCooldown, default_cooldown
from harness.safety import provenance
from harness.safety.permissions import PermissionEngine
from harness.session.invariants import ModelVisibilityAudit
from harness.session.session_log import NULL_LOG, SessionLog
from harness.tools import ToolRegistry
from harness.tools.pipeline import ToolPipeline
from harness.tools.repeat import RepeatGuard
from harness.tools.todo import TodoList
from harness.tools.undo import UndoStore

from .approvals import Approver, deny_all
from .limits import STREAM_IDLE_SECONDS

Requester = Callable[[dict[str, Any]], "Awaitable[dict[str, Any]]"]


class CoreMixin:
    def __init__(
        self,
        *,
        provider: ProviderClient,
        registry: ToolRegistry,
        permissions: PermissionEngine,
        model: str,
        instructions: Optional[str] = None,
        approver: Optional[Approver] = None,
        max_iterations: Optional[int] = None,
        # What the model is asked to write when a rail (the iteration cap, the repeat guard, the
        # token budget) ends the turn. None means the session handoff (`CHECKPOINT_PROMPT`),
        # resolved where it is used. A subagent overrides it, because its caller needs the report
        # and not a handoff.
        cap_prompt: Optional[str] = None,
        token_budget: int = 0,
        # Parallel-safe tool calls in flight per step. 0 is uncapped and 1 is serial even for reads.
        max_parallel_tools: int = 0,
        model_settings: Optional[dict[str, Any]] = None,
        # The named thinking level the person picked for this session (`low`, `high`, …). It is
        # translated to a provider-specific body patch at request time (`providers/variants.py`).
        # The level is kept and not a merged dict, so a switch mid-session can subtract the old
        # patch and apply the new one.
        thinking_level: Optional[str] = None,
        # Routes tried when the current model fails in a way another model can answer:
        # model ids in the router's `provider:model` form, in order. Empty, the default, keeps the
        # one-route behaviour. `build_engine` reads `fallback_models` from the global config, and a
        # caller may pass its own.
        model_fallback: Optional[list[str]] = None,
        # The per-route cooldown table. None means the process-wide default. Tests inject
        # their own, so a scripted failure cannot leak into the next test.
        fallback_cooldown: Optional[RouteCooldown] = None,
        messages: Optional[list[dict[str, Any]]] = None,
        audit_sink: Optional[Callable[[dict[str, Any]], None]] = None,
        # Lifecycle hooks (harness/loop/hooks.py): a person's scripts at named moments. The engine
        # only asks it for a decision (deny, or context). It can neither grant nor break a turn,
        # which is why a runner is a plain collaborator here and not another policy layer.
        hooks: Optional[Any] = None,
        context_provider: Optional[Callable[[], str]] = None,
        # The named capture of the context provider's last join (label to text), written by the
        # closure in compose.py. Only the context panel's per-category estimate reads it, and the
        # wire never does, since its shape is still the joined string.
        context_parts: Optional[dict[str, str]] = None,
        plan_approver: Optional[Requester] = None,
        question_asker: Optional[Requester] = None,
        tool_requester: Optional[Requester] = None,
        # Called, thread-safe and best effort, when the person stops the turn: the kill for a
        # running shell command, for one.
        interrupt_hooks: Optional[list[Callable[[], None]]] = None,
        # The session's spill sink (tools/spill.py). Surfaces with a workspace have one, and the
        # step's output budget spends it when a step's tool results add up past the aggregate cap.
        spill: Optional[Any] = None,
        # How long the provider may go silent before the stream is treated as dead and re-issued
        # (`STREAM_IDLE_SECONDS`). 0 disables the watchdog.
        stream_idle_timeout: float = STREAM_IDLE_SECONDS,
        # The model-visibility invariant (invariants.py). "record", the default, writes an
        # `invariant/violation` event and logs at ERROR when a request goes out over unlogged
        # history. "strict" raises instead, which is what tests and CI want: the raise fails the
        # change that introduced the gap.
        invariant_mode: str = "record",
    ) -> None:
        self.provider = provider
        self.registry = registry
        self.permissions = permissions
        self.model = model
        self.approver = approver or deny_all
        self.max_iterations = max_iterations
        self.cap_prompt = cap_prompt
        self.stream_idle_timeout = max(0.0, float(stream_idle_timeout or 0.0))
        # Provider traffic one turn may spend, in tokens (`TokenUsage.total`), before the turn stops
        # on the same graceful path the iteration cap uses. 0 disables it.
        #
        # It exists because `max_iterations`, when an agent declares one, bounds how many steps a
        # turn takes and says nothing about how large they were: a hundred steps at a full context is
        # two orders of magnitude more traffic than a hundred small ones, and the runaway loop is
        # exactly the shape that keeps a large context alive. It counts the turn's own round trips
        # plus whatever a delegated child reported (`_record_result` folds `result["tokens"]` in),
        # because without that second half a fan-out of subagents is spend the ceiling cannot see. It
        # does not count compaction's summarizer call, which is maintenance bounded by its own
        # anti-thrash rules and not by this clock.
        self.token_budget = max(0, int(token_budget))
        # How many low-risk calls of one segment may be in flight at once. Without a cap, a single
        # model response asking for thirty reads starts thirty together, and the default thread pool,
        # any per-directory lock and the provider's per-minute limits all feel that shape. Chunking
        # changes when calls overlap and never the order their results are recorded in.
        self.max_parallel_tools = max(0, int(max_parallel_tools or 0))
        self.spill = spill
        self.invariant_mode = invariant_mode
        self.audit_sink = audit_sink
        self.audit_context: dict[str, Any] = {}
        self.hooks = hooks
        # The fallback chain and its cooldown table. The chain is read once at build,
        # since config is not hot-reloadable mid-session, and the cooldown is shared process-wide so
        # one session's dead route helps every other.
        self.model_fallback = [
            route.strip() for route in (model_fallback or []) if isinstance(route, str) and route.strip()
        ]
        self.fallback_cooldown = fallback_cooldown or default_cooldown()

        self.model_settings = dict(model_settings or {})
        # The settings without the thinking patch, kept so `switch_thinking` can work them out
        # again. The level's patch goes under explicit settings, so a caller that names a setting
        # wins, mirroring how the two relate in `providers/variants.py`.
        self._base_model_settings = dict(model_settings or {})
        self.thinking_level = thinking_level or None
        self._apply_thinking_level()
        self.messages: list[dict[str, Any]] = list(messages or [])

        self._init_context_state(context_provider, context_parts)
        self._init_fold_state()
        self._init_requesters(tool_requester, plan_approver, question_asker)
        self._init_session_attachments(permissions)
        self._init_turn_state()
        self._init_measurement_state()
        self._init_tool_state(interrupt_hooks)
        self._seat_instructions(instructions)
        self._cancel = asyncio.Event()

    # -- the groups -----------------------------------------------------------------------

    def _init_context_state(
        self, context_provider: Optional[Callable[[], str]], context_parts: Optional[dict[str, str]]
    ) -> None:
        # Returns the live `<system-context>` block for a turn. System messages cannot be injected
        # reliably into the middle of a thread across providers, so dynamic context (the live
        # directory list, the mode reminder, the skills menu) rides in the user turn. It is read once
        # per turn by `_stamp_live_context`, which writes it into that turn's user message
        # (`api_content`) and never rewrites it, so the provider's cached prefix survives. It returns
        # "" when there is nothing to add.
        self.context_provider = context_provider
        self.context_parts = context_parts
        # The last `<system-context>` text actually written this turn, and the per-category estimate
        # taken when the request was assembled. The sidecar's context_breakdown reads both.
        self._last_ephemeral = ""
        # True when this turn's stamp was the full live context and not just the clock line. The
        # panel uses it to count the skills menu once.
        self._last_context_full = False
        # The append-only context epoch. The live block is written into the user message
        # (`api_content`) the first time the turn is sent, and nothing already sent is ever
        # rewritten: a provider matches a cached prefix byte for byte, so stamping the same position
        # again every turn prices the whole conversation behind it again. The baseline is the
        # non-clock half of the last full block, held in memory only. After a restart one redundant
        # full block is the honest cost of not persisting a cache hint. `_context_epoch_index` is
        # where that block landed, so a compaction that swallowed it is detected and the context is
        # stated again.
        self._context_baseline: Optional[str] = None
        self._context_epoch_index: int = -1
        self._context_clock: str = ""
        # The "background subagents still running" line last stated (turn_stamps), "" when none.
        self._context_background: str = ""
        # Context awareness: a factual "context: ~Nk of ~Mk tokens before compaction" line, written
        # at the turn's start once per ten-percent band, since a number that changed every step would
        # spend more tokens than it saves. `[compaction] context_budget_line = false` turns it off.
        # No instruction rides it: the recorded decision is that this harness never tells the model
        # to wrap up (docs/harness-capability-matrix.md), it states the facts.
        self.context_budget_line: bool = True
        self._budget_band: int = -1
        self._last_breakdown: Optional[dict[str, int]] = None
        self._adopt_context_epoch()

    def _init_fold_state(self) -> None:
        # The model may fold its own context (`context_fold.py`). A bare engine has it
        # off, and `build_engine` turns it on for a session's own engine.
        self.model_fold: bool = False
        # The prune pass also folds the write payloads of aged calls. None lets
        # `[compaction] prune_write_arguments` decide (on unless set false), and a bool here
        # overrides it, which is how the ablation switches it off.
        self.prune_write_arguments: Optional[bool] = None
        # True or False while the request after a fold is awaited: was the cache warm before it.
        self._fold_probe: Optional[bool] = None
        self._fold_last_cache_read: int = 0
        # Models whose cache did not survive a fold in this session. They get no more hints.
        self._fold_cache_lost: set[str] = set()
        self._fold_notice: Optional[Any] = None
        # `(boundary, foldable)` when the fold hint was last given, None if never.
        self._fold_hinted: Optional[tuple[int, int]] = None
        # The boundary whose stretch already got its one late hint, None if none yet.
        self._fold_late_hinted: Optional[int] = None
        # Task-list items seen done after the last step: one more is a finished item.
        self._fold_done_seen: int = 0
        self._fold_stamp: Optional[dict[str, Any]] = None
        # `(boundary, tokens)`: what of the context a fold cannot remove, per stretch.
        self._fold_floor: Optional[tuple[int, int]] = None
        # Mechanisms switched off for an ablation run (remove one component at a time and measure).
        # Empty in the product, and `benchmarks/ablation.py` is the only writer. The names are
        # `ABLATABLE`.
        self.ablated: frozenset[str] = frozenset()

    def _init_requesters(self, tool, plan, question) -> None:
        # `request_tool`: emits TOOL_REQUESTED and waits for the person to install the pinned build
        # or decline. None on surfaces that cannot prompt, where the tool does nothing and the agent
        # is told so it can fall back openly and not skip silently.
        self.tool_requester = tool
        # `propose_plan`: emits PLAN_PROPOSED and waits for the person's decision. An approving
        # result takes the live PermissionEngine out of plan mode, with the same session and context
        # kept. None on surfaces that cannot prompt.
        self.plan_approver = plan
        # `ask_user`: turns a question into an inbox item and waits for the answer, which can come
        # inline in a live session or from the inbox when unattended. None on surfaces that cannot
        # ask, where the tool does nothing.
        self.question_asker = question

    def _init_session_attachments(self, permissions: PermissionEngine) -> None:
        """Collaborators the surface or manager fills in after construction. Each defaults to a
        value that leaves behaviour as it was without it."""
        # Auto-compaction. `compaction_settings` is a live getter, so Settings changes
        # apply without a rebuild, and `is_attended` gates the failure prompt (None is treated as
        # unattended: a background run is never parked on internal bookkeeping).
        self.compaction_state: Optional[compaction.CompactionState] = None
        self.compaction_settings: Optional[Callable[[], dict[str, Any]]] = None
        self.is_attended: Optional[Callable[[], bool]] = None
        # Message-level revert, set by the manager. `{"boundary", "staged_at", "ts"}`
        # truncates the outbound view only (`_outbound_messages`) and the canonical thread is
        # untouched. None means no revert is staged and the request is byte-identical to before.
        self.revert_state: Optional[dict[str, Any]] = None
        # The marker this session's log last recorded (`_note_revert_change`); False until the log
        # has been read for it.
        self._revert_logged: Any = False
        # Session facts: the known world frozen at session start, plus the
        # per-turn ingestion record. None means nothing is recorded and behaviour is byte-identical.
        # Nothing consumes it yet either way.
        self.session_facts: Optional[session_facts.SessionFacts] = None
        # Extra user-facing fields for a tool's approval card, merged into the PERMISSION_REQUIRED
        # payload (web_search's live provider name, so the card can say where queries go). The
        # surface sets it, since the engine knows nothing about providers. It is called at card time
        # and not at session start, so a mid-session Settings change shows through.
        self.approval_extras: Optional[Callable[[str, dict[str, Any]], dict[str, Any]]] = None
        # Adds a folder to the session as a root, `(path, writable)`: what an allowed call that
        # reaches outside the session's folders does before it runs. The host sets it
        # (the manager's `add_root`, which persists the folder, marked as reached, and shows it in
        # the Files panel).
        # None leaves the fallback: the folder joins the live roots list.
        self.root_granter: Optional[Callable[[str, bool], Any]] = None
        # What the agent itself created this session. `python scripts/setup.py` cannot
        # be judged from its text, but the engine knows whether it wrote or downloaded that file
        # moments ago, and says so on the card. It lives in memory only, and a restart costs context
        # (more cards) and never correctness.
        self._agent_files = provenance.SessionFiles(permissions.workspace_root)
        # Completed tool calls so far, so a fact can say how many steps back the write was.
        self._step = 0
        # The session event log (session_log.py): what happened, beside what was said. The manager
        # sets it after construction, and a scripted test or one-off script keeps the null log, so
        # nothing has to check for one before recording. It is never read on the model path, since
        # losing a line there must not cost a turn.
        self.log: SessionLog = NULL_LOG
        # The standing task list (`todo_write`), owned by whoever built the toolset and handed here
        # so the engine can read it. Two readers need it and neither is the tool: the event log
        # records a `todo/write` when a call lands, and compaction carries the list across a boundary
        # that would otherwise swallow every call that set it up. None where no toolset offered it.
        self.todo: Optional[TodoList] = None
        # Which files the agent has looked at, and as what (tools/observed.py). It is None where the
        # session has no workspace to anchor a relative path to, the one case the guard cannot judge.
        self.observed: Optional[Any] = None
        # Before-images for the session's writes (tools/undo.py). `build_engine` sets it when a
        # writable root exists, and None disables the affordance rather than offering an undo that
        # cannot be honoured.
        self.undo: Optional[UndoStore] = None
        # Durable notes that an irreversible call started (tools/intents.py), item 2.2. None means
        # behaviour as before.
        self.intents: Optional[Any] = None
        # The receipt for a turn (item 4.1): what was created, what was overwritten, what left the
        # machine, from what the engine saw and not from what the model says it did. None means no
        # receipt.
        self.changes: Optional[Any] = None
        # The session's workspace root for the nested-AGENTS pass, set by `build_engine`. It is None
        # for a rootless or remote session, since a remote POSIX path must never be walked as a local
        # directory.
        self.workspace: Optional[str] = None
        # Off when the agent definition opts out of AGENTS.md injection (`inject_agents: false` in a
        # subagent definition); the nested pass follows.
        self.inject_agents: bool = True
        # Set by the host to make the turn's record durable, returning False when it could not be
        # written (`harness/session/durability.py`). None means the host owns no durable store (the
        # CLI, the bench, every test), and the gate in front of a consequential call is then a no-op
        # and not a refusal.
        self.flush: Optional[Callable[[], Awaitable[bool]]] = None
        # The last plan the person approved (propose_plan). The next compaction carries it into the
        # block (`CompactionState.approved_plan`), because the tool call that carried the plan is
        # clipped to 200 characters in the summarized span, and a session that compacts
        # mid-implementation must not lose the plan it is carrying out. None until a plan is
        # approved, and a later approval replaces it.
        self.approved_plan: Optional[str] = None
        # The session's working plan file (`.reeve/plans/<session>.md` inside a root), set by
        # `build_engine` when a session id and a writable root exist. `update_plan` writes there and
        # compaction points the model back at it, so a plan built a piece at a time survives both
        # compaction and a restart. None on surfaces with no plan file.
        self.plan_path: Optional[Any] = None
        # The tool names in effect at the last iteration. A session's toolset is no longer fixed at
        # build time (arming MCP swaps it mid-conversation), and a log that could not show that
        # would have to explain a call to a tool the transcript never mentions. It is None until the
        # first iteration records the opening set, which the system row already accounts for, and
        # only changes are worth a line. It is the registered names (a load leaves the wire as it
        # was); see `_note_tool_changes`.
        self._tool_signature: Optional[frozenset[str]] = None
        # The system prompt the last header carried: a model switch changes it alone.
        self._system_logged: Optional[str] = None

    def _init_turn_state(self) -> None:
        """Per-turn state. It is set here and not only in `_loop`, because `resume()` and `retry()`
        drive tool recording without entering the loop, and a missing attribute there is a crash in
        the middle of recovery, the one moment that can least afford one."""
        # The check-loop rail, reset by `_loop_body`. The defaults let a direct helper call
        # (tests, tooling) run before a turn has ever happened.
        self._check_streak = 0
        self._check_streak_index = -1
        self._check_streak_outcome: Optional[str] = None
        self._check_loop_noticed = False
        # Repairs: the project's store (set by the agent builder, None without a project
        # memory directory), the last check already looked at, the failures this turn was told
        # about, and what each recalled repair cost the first time.
        self.repairs: Any = None
        self._repair_primed = False
        self._repair_seen: Optional[str] = None
        self._turn_recalled: set[str] = set()
        self._turn_lesson_asked = False
        self._recalled_steps: dict[str, int] = {}
        self._visibility_audit = ModelVisibilityAudit()
        self._visibility_reported: set[str] = set()
        # Turn-boundary bookkeeping: whether this turn's `turn/end` has been written (the finalizer
        # consults it), and which log has had its dangling turns repaired (crash recovery runs once
        # per attached log).
        self._turn_end_logged = False
        self._dangling_checked_for: Optional[SessionLog] = None
        # The `session_start` hook fires once, on the first turn of this engine.
        self._session_started = False
        # The `stop` hook may object once per turn, like the verification gate: a hook that can
        # loop is worse than one that can be ignored once.
        self._turn_stop_hooked = False
        # The note that hooks are declared but not trusted is shown once per engine.
        self._hooks_noted = False
        # Turn and step: a turn is a person's request and a step is one model call plus the tools it
        # asked for. Every timing in the log is measured against one of the two, so they are counted
        # here and not worked out later.
        self._turn_no = 0
        self._step_no = 0
        self._turn_wrote_code = False
        self._turn_ran_after = False
        # What a call sent under another harness's parameter names, by call id.
        self._renamed_arguments: dict[str, dict[str, Any]] = {}
        self._turn_verify_nudged = False
        self._turn_check_nudged = False
        # Whether the latest assistant turn hit the output-token limit. It decides which diagnosis a
        # mangled (unparseable-arguments) tool call is answered with.
        self._turn_truncated = False
        # The todo nudge's bookkeeping (see TODO_NUDGE_AFTER), scoped to the turn and reset with the
        # rest of the turn's state.
        self._turn_tool_calls = 0
        self._turn_saw_todo = False
        self._turn_todo_nudged = False
        # This turn's provider traffic so far, in tokens: the token budget's counter.
        self._turn_tokens = 0
        # Background children's spend waiting for the next step to charge it (O7).
        self._carried_tokens: list[tuple[str, int]] = []
        # The current step's tool output so far, in characters: `STEP_OUTPUT_BUDGET`'s counter,
        # reset by `_handle_tool_calls` at the start of every batch.
        self._step_output_chars = 0
        # How many turns this session has opened for itself in a row, with nobody in between. It is
        # read by `selfwake_tools` when the model schedules another wake, and bounded by
        # `selfwake.MAX_WAKE_DEPTH`. Every limit the loop already has (`max_iterations`, the repeat
        # guard) counts within one turn, and this is the only one that counts across turns a wake
        # chained together.
        #
        # Whoever opens the turn sets it. The manager stamps `wake.depth + 1` for a wake-driven turn
        # and 0 for anything else, so a person replying, a channel message or a team feed resets the
        # chain. It lives on the engine and not on the wake because the engine is rebuilt on restart,
        # and a chain is precisely what must not come back as a fresh zero.
        self.wake_depth: int = 0
        # The @agent names the current turn was opened with (an @-mention). The task tool reads this
        # per call through `build_engine`'s `mention_bypass` lambda: when it is non-empty, the spawn
        # ask is skipped. It is the same per-message boolean as an agent part in the message, reset at
        # every `run` so a plain follow-up turn asks again, and not persisted, since a restart has no
        # live turn to bypass for.
        self.turn_mentions: list[str] = []
        # The goal round this turn is executing (0 is an ordinary turn). It is set from the turn's
        # MessageSource at `run` (`{"kind": "goal_round", "round": N}`), and the `goal` tool reads it
        # per call through `build_engine`'s `round_of` lambda. That is the completion authority: the
        # model may report `blocked` only from inside an autonomous round, and only after the
        # configured count.
        self.goal_round: int = 0
        # Whether a person opened this turn or spoke in it. True when `run` got no
        # `source` (typing in the app, the CLI or the TUI) or a person steered into the live turn.
        # Every producer that is not a person names itself with a source: a goal round, a background
        # result, a scheduled run, a connector. It is not derived from the transcript as `goal_round`
        # is. A turn run again in the same process keeps the value, and an engine rebuilt after a
        # restart starts at False, which only costs a retyped request. The `goal` tool reads it per
        # call.
        self.person_turn: bool = False
        # Each pending steering message: (text, an optional MessageSource sidecar dict, an optional
        # display-only `_display` sidecar). There is one entry per `queue_steering`.
        self._steering: list[tuple[str, Optional[dict[str, Any]], Optional[str]]] = []
        # Set while a message the person typed into the live turn waits for the step to end
        # (`person_steered`), cleared when it is delivered. A wait the step is in reads it and gives
        # up (`tools/shell_wait.py`): the person is waiting on the step, so the step must not wait on
        # a command. A `threading.Event`, because the waits run on tool threads. The hooks wake them.
        self.person_waiting = threading.Event()
        self._person_steered_at: Optional[float] = None
        self._person_steer_hooks: list[Callable[[], None]] = []
        # Call ids this resume must not repeat on its own. Only `resume` fills it, and every turn
        # start clears it: a fresh turn proposing the same action is a new decision by the model, not
        # the interrupted one.
        self._uncertain_calls: set[str] = set()
        # Consecutive tool calls whose arguments the provider could not parse (it hands back
        # `{"_raw": …}`). Three in a row end the turn, and the budget is not burned on a model that
        # cannot produce a readable call.
        self._malformed_args_streak: int = 0
        self._malformed_args_exhausted: bool = False

    def _init_measurement_state(self) -> None:
        """What the engine has measured about the context: provider counts, the estimator's bias,
        cache timing and compaction failures."""
        self._last_context_tokens: Optional[int] = None
        # Set once a provider's prompt counts prove partial (`_record_context_measure`).
        self._usage_unreliable = False
        # How much of `messages` the count above accounted for, so the pressure signal can add what
        # has arrived since and not read a step behind.
        self._context_measured_at: int = 0
        # When the last provider request went out. The prompt cache's TTL runs from there, and the
        # opportunistic prune (`_cache_cold`) reads it to tell a warm cache (pruning would buy a miss
        # on the next request) from a cold one (that request pays full price anyway, so folding aged
        # results is free).
        self._last_request_at: Optional[float] = None
        # The model the last request went to: a cache belongs to one model (`_cache_cold`).
        self._last_request_model: Optional[str] = None
        # The outbound prefix changed outside compaction (a staged `/undo`), so the provider's cached
        # prefix is unusable by construction. The next request is cold, and the opportunistic prune
        # may run at once. The pass that uses it clears it.
        self._prefix_dirty: bool = False
        # When a compaction last failed. The gate based on state only knows about attempts that
        # produced a state, and a failed first attempt left no stamp, so every following step paid
        # for the same failure again. This clock is its cooldown (`_compaction_gate_now`).
        self._compaction_failed_at: float = 0.0
        # The last summarizer failure's text, for the manual `/compact` report. With the trim
        # fallback gone, this is the only place the reason survives to the surface.
        self._last_compaction_error: str = ""
        # Content to spill path for the prune tier (`_prune_spill`). The sink runs on every outbound
        # render, so a given result's whole output is written once and not on each call.
        self._spill_paths: dict[str, str] = {}
        # The chars/4 estimator's bias for this model: the provider's count over the estimate of the
        # same request (`context_price._record_context_measure`), taken from the last request. None
        # until a provider has priced one.
        self._estimate_bias: Optional[float] = None
        # The estimate of the last request the provider priced, for the check that its counts are
        # whole.
        self._measured_view_tokens: Optional[int] = None
        # Whether replaying the conversation for the summarizer earns its cache read. It starts
        # optimistic, then the provider's own usage numbers decide it and it holds for the session,
        # since a prefix that missed once will miss again for the same reasons.
        self._summarizer_replay_pays = True

    def _init_tool_state(self, interrupt_hooks: Optional[list[Callable[[], None]]]) -> None:
        # The wall time of each dispatched call, keyed by call id, handed to the tool result message
        # and popped there. It lives here and not on ToolResult because that type is frozen and
        # shared with the pipeline's listeners, none of which has any business seeing a clock.
        self._call_timing: dict[str, dict[str, float]] = {}
        # Live tool output (harness/tools/output.py): chunks a running tool published, waiting to be
        # forwarded as `tool_output_delta` events. It is a deque behind a lock because the publisher
        # is a worker thread, a shell command's reader, while the only reader is the turn's own
        # generator (`_drain_tool_output`).
        self._output_chunks: deque[tuple[str, str]] = deque()
        self._output_lock = threading.Lock()
        # tool_call.id to the standing rule that auto-allowed it ("tool → target"), so the
        # TOOL_FINISHED event can carry the note to the tool card.
        self._standing_notes: dict[str, str] = {}
        # Attachments produced while a turn's tool calls run, held so they are appended after the
        # whole tool-result run and not between results. Every provider requires the responses to
        # follow the assistant message directly, and DeepSeek rejects the interleaved shape outright
        # (see `order_tool_responses`). None outside `_handle_tool_calls`, where a result's
        # attachment is appended at once, as before.
        self._attach_queue: Optional[list[dict[str, Any]]] = None
        self._interrupt_hooks: list[Callable[[], None]] = list(interrupt_hooks or [])
        # Where cross-cutting execution concerns attach (tools/pipeline.py): deadlines, and anything
        # that transforms a tool's value. It is always present and empty by default, so an engine
        # with nothing registered dispatches as before. It is not a constructor parameter, because
        # `build_engine` already carries 35 and every caller that wires policy does it after
        # construction.
        self.pipeline = ToolPipeline()
        # The repeat guard is deliberately not a pipeline listener. What it produces is a line
        # addressed to the model and not a different result, so routing it through `post_execute`
        # would mean editing tool return values to carry a message about them, which corrupts every
        # structured result it touches. It observes where the model-facing message is built
        # instead (tools/repeat.py).
        self.repeats = RepeatGuard()

    def _seat_instructions(self, instructions: Optional[str]) -> None:
        """Put the system prompt at the head of the history."""
        if not instructions:
            return
        if self.messages and self.messages[0].get("role") == "system":
            # A transcript carries the system prompt it was created with. An engine built over it
            # runs under the instructions built now: the stored ones describe the tools, roots and
            # memory of whatever harness wrote them, and kept as they were they went on being sent
            # after what they described was gone (2026-10-05: 11 sessions still carried an index of
            # notes about the person that were no longer loaded). The first step logs the
            # header it runs with, as for any new engine (`_note_tool_changes`), so the request is
            # still explainable.
            if self.messages[0].get("content") != instructions:
                self.messages[0] = {**self.messages[0], "content": instructions}
        else:
            self.messages.insert(0, {"role": "system", "content": instructions})
        # A transcript that ran on other models says so in the model line (`context/model_line.py`).
        self._restate_model()

    @property
    def turn_tokens(self) -> int:
        """Provider tokens the current (or just-finished) turn spent: the token budget's counter,
        read by the goal ledger to charge a round."""
        return int(self._turn_tokens or 0)
