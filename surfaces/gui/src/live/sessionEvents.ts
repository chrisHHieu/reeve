// What each server frame does to a conversation's live state.
//
// This used to be a 600-line closure inside App's connect effect, bound to React setters,
// which is why it could only ever serve the conversation on screen. It now writes to a
// `LiveSession` (state + facts) and reaches the view only through `SessionEventHost`, so the
// same code serves every open conversation: the one being read updates the screen, the
// others keep their transcript current for when the reader returns.
//
// View-level concerns (the composer's mode/model pickers, the folder chip, the rail, the
// sidebar) are NOT written here. They are recorded as facts on the session and announced
// through the host; the host applies them only when the session is the one on screen.
import { isConnectorSource, type MessageSource } from "../api";
import type { SessionRevert } from "../api";
import { approvalItemFromEvent } from "../approvalItem";
import { FILE_WRITE_TOOLS, newId, retryReason } from "../app/appHelpers";
import { changesItem, userItemFromContent } from "../itemsFromMessages";
import { runRefOf } from "../runRef";
import { foldBlock, reasoningOf, textOf } from "../streamBlocks";
import { appendToolOutput, markToolStarted, updateLastTool, updateToolMetadata } from "../toolRows";
import { appendJobChunk } from "./jobOutput";
import { TODO_TOOL, planFromTodoWrite } from "../todoPlan";
import { isHiddenTool } from "../toolMeta";
import { fromParentAgent, wakeNotice } from "../wakeNotice";
import { compactionNotice } from "../compactionNotice";
import type { Item, SentCommand, WsEvent } from "../types";
import { addTokens, addTurnUsage, stepTokens, withContext } from "../usage";
import type { LiveSession } from "./liveSession";

/** The transcript line a plugin reload leaves. */
/** The frames that end a `/compact` run between turns. */
const MANUAL_COMPACT_ENDS = new Set(["compacted", "compact_failed", "turn_start", "ready", "error"]);

export function pluginsReloadedNotice(plugins: unknown, errors: unknown): Item {
  const n = Number(plugins || 0);
  const bad = Number(errors || 0);
  const text =
    `Plugins reloaded — ${n} active` +
    (bad ? `, ${bad} with a problem (Settings ▸ Plugins)` : "") +
    ". The next reply re-reads the whole conversation.";
  return { kind: "notice", tone: bad ? "warn" : "info", text };
}

/** The view's side of an event. Every method is a no-op for a session that is not on
 *  screen unless its comment says otherwise. */
export interface SessionEventHost {
  /** The session's facts changed. `ready` is the connect-time truth (a pick the person made
   *  before it landed outranks it); `patch` is a later move the server made or confirmed
   *  (mode/plan/model/thinking), which is always the truth. The host applies them to the
   *  pickers when `live` is the active session. */
  factsChanged(live: LiveSession, kind: "ready" | "patch"): void;
  /** Something the rail shows may have changed (browser state, a file write). */
  railStale(live: LiveSession): void;
  /** Re-read the transcript from the server (a `revert` frame from another window). */
  reloadTranscript(live: LiveSession): void;
  /** The engine behind this socket was replaced (`/reload-plugins`); reconnect to adopt it.
   *  Runs for every session: a background one reconnects without waiting to be viewed. */
  engineReplaced(live: LiveSession): void;
  /** A turn ended. Runs for every session (the sidebar's counters, a manual automation run
   *  being finalized depend on it). */
  turnEnded(live: LiveSession): void;
  /** Whether a remote-host start refusal belongs on the setup row (the session is on screen
   *  and bound to a host). */
  showsEnvironmentError(live: LiveSession): boolean;
}

/** Remote-start failures: a SETUP refusal while the draft is still empty, not a turn error. */
const REMOTE_ERROR_KINDS = [
  "remote",
  "auth",
  "host_key",
  "timeout",
  "ssh_missing",
  "unsupported",
  "protocol",
  "connection",
  "command",
];

const STOP_STATUSES = [
  "max_iterations_exceeded",
  "repetition_stopped",
  "token_budget_exceeded",
  "no_progress_stopped",
];

/** Patch the session's recorded facts and tell the host. */
function patchReady(live: LiveSession, host: SessionEventHost, patch: Record<string, unknown>): void {
  live.facts.ready = { ...(live.facts.ready ?? {}), ...patch };
  host.factsChanged(live, "patch");
}

export function handleSessionEvent(live: LiveSession, ev: WsEvent, host: SessionEventHost): void {
  const d = ev.data || {};
  const { setItems, setBlocks, resetStream, setRunning, setUsage, setTodo, setGoal } = live;

  // Item 4.1: the turn's receipt. The engine sends it only when something actually
  // changed, so the absence of this line is itself information — a turn that merely
  // answered a question does not get one.
  const appendChanges = (data: any) => {
    const receipt = changesItem(data?.changes);
    if (receipt) setItems((p) => [...p, receipt]);
  };
  // An interrupted/errored turn never emits assistant_message, so its streamed partial would
  // otherwise live only in the ephemeral buffer until the next turn_start wipes it
  // (owner-hit 2026-07-22). Promote it to a durable transcript item — the engine persists the
  // same text server-side, so the live view and a session reload agree.
  const flushPartialStream = () => {
    const blocks = live.current.blocks;
    const partial = textOf(blocks);
    const thinking = reasoningOf(blocks);
    if (!partial && !thinking) return;
    resetStream();
    setItems((p) => [
      ...p,
      {
        kind: "assistant",
        text: partial,
        ts: Date.now() / 1000,
        ...(thinking ? { reasoning: thinking } : {}),
      },
    ]);
  };

  // Any engine event after `compacting` means the summarizer finished (compacted / silent
  // no-op / failure prompt) — the transient must never outlive it. A `/compact` between turns
  // is the exception: other frames (a job's roster, a metrics tick) arrive while it runs, and
  // only its own end, or a turn, ends it.
  if (ev.type !== "compacting" && !(live.current.compactingManual && !MANUAL_COMPACT_ENDS.has(ev.type))) {
    live.setCompacting(false);
  }

  switch (ev.type) {
    case "ready": {
      // Server truth on a turn already in flight: after a reload or reconnect mid-turn the
      // client would otherwise draw an idle composer (Send instead of Stop/Steer), and the
      // message typed there bounces off "already running".
      if (typeof d.running === "boolean") setRunning(d.running);
      live.facts.ready = { ...d };
      // The engine was built: a remote machine's earlier refusal is over.
      live.facts.environmentError = "";
      host.factsChanged(live, "ready");
      break;
    }
    case "mode_changed":
      // The server broadcast a permission-mode move (picked in a second window, an approval
      // adoption). Recorded so the picker of whichever session is on screen stays honest.
      if (typeof d.mode === "string") patchReady(live, host, { mode: d.mode });
      break;
    case "plan_changed":
      if (typeof d.plan === "boolean") patchReady(live, host, { plan: d.plan });
      break;
    case "steering_queued":
      // A typed sentence accepted into the RUNNING turn. The ack is the only
      // place the note is created: a rejected steer cannot leave a message nobody read on
      // screen. `kind: "steer"` keeps it inside the running turn's group.
      // In a subagent's session the server sends the same frame for a message from the agent
      // that started it, with its `source`.
      setItems((p) => [
        ...p,
        {
          kind: "steer",
          text: String(d.text ?? ""),
          ts: Date.now() / 1000,
          ...(fromParentAgent(d.source) ? { from: "agent" as const } : {}),
        },
      ]);
      break;
    case "turn_start":
      setRunning(true);
      resetStream();
      live.facts.pendingTokens = undefined;
      // No plan clear here: the standing plan lives until the model writes another
      // (todoPlan.ts, owner call 2026-09-04).
      // Background-delivered turns (channel message, self-wake, durable resume) have no local
      // send(), so the triggering message isn't in `items` yet — surface it. A connector
      // message carries a structured `source` → render the rich card; an ENGINE-injected
      // message (a finished background child's envelope) is not the person
      // talking — the model reads it, and the transcript draws one line saying what reported
      // back, so the turn it starts is not stacked onto the one before. Foreground turns
      // already appended theirs in send(); skip the duplicate.
      if (isConnectorSource(d.source)) {
        const src = d.source as MessageSource;
        setItems((p) => {
          const last = p[p.length - 1];
          return last && last.kind === "connector" && last.source.ts === src.ts && last.source.text === src.text
            ? p
            : [...p, { kind: "connector", source: src }];
        });
      } else if (typeof d.source?.synthetic === "string" && d.source.synthetic && !fromParentAgent(d.source)) {
        const wake = wakeNotice(d.source, Date.now() / 1000);
        if (wake)
          setItems((p) => {
            const last = p[p.length - 1];
            return last && last.kind === "notice" && last.wake === wake.wake ? p : [...p, wake];
          });
      } else if (typeof d.input === "string" && d.input) {
        // `display` (force-run) is the user's literal "/name …" line; the framed `input` is
        // model-facing. Surface/dedupe on what the user actually sees.
        const shown = (typeof d.display === "string" && d.display) || (d.input as string);
        setItems((p) => {
          // Look past trailing notices — the "Temporary folder created" line sits
          // between the local echo and this event's arrival.
          let i = p.length - 1;
          while (i >= 0 && p[i].kind === "notice") i--;
          const last = p[i];
          return last && last.kind === "user" && last.text === shown
            ? p
            : [
                ...p,
                {
                  kind: "user",
                  text: shown,
                  ts: Date.now() / 1000,
                  // Sent with a slash command from another view: drawn as the command.
                  ...(d.command && typeof d.command === "object" ? { command: d.command as SentCommand } : {}),
                },
              ];
        });
      }
      // The message just sent learns where its files were kept, so its cards open now
      // and not only once the conversation is reopened. Its own attachments are the same list,
      // in the same order, as the rows.
      if (Array.isArray(d.attached) && d.attached.length) {
        const rows = d.attached as { name?: string; path?: string; size?: number; lines?: number }[];
        setItems((p) => {
          let i = p.length - 1;
          while (i >= 0 && p[i].kind === "notice") i--;
          const last = p[i];
          if (!last || last.kind !== "user" || !last.attachments?.length) return p;
          const attachments = last.attachments.map((a, k) => {
            const row = rows[k];
            if (!row || row.name !== a.name) return a;
            return {
              ...a,
              ...(row.path ? { path: row.path } : {}),
              ...(typeof row.size === "number" ? { size: row.size } : {}),
              ...(typeof row.lines === "number" ? { lines: row.lines } : {}),
            };
          });
          const next = p.slice();
          next[i] = { ...last, attachments };
          return next;
        });
      }
      break;
    case "assistant_delta":
      setBlocks((b) => foldBlock(b, "text", d));
      break;
    case "tool_call_delta":
      // A call being WRITTEN, not one being run: `tool_proposed` still opens the tool card when
      // the engine dispatches the parsed call. This only lets the step show which tool it is
      // reaching for while the arguments are still arriving.
      setBlocks((b) => foldBlock(b, "tool_call", d));
      break;
    case "retrying":
      // The answer on screen was cut off mid-sentence and the same request is going again, so
      // the fragment has to go: leaving it would splice a discarded half-answer onto the real
      // one. The notice replaces it, because a stream that silently restarts looks like a stall.
      if (d.discard_partial) resetStream();
      setItems((p) => [
        ...p,
        {
          kind: "notice",
          tone: "info",
          text: `${retryReason(d)} — retrying (${d.attempt}/${d.of}).`,
          // Mid-turn by construction: render it INSIDE the running group.
          inline: true,
        },
      ]);
      break;
    case "reasoning_delta":
      setBlocks((b) => foldBlock(b, "reasoning", d));
      break;
    case "assistant_message": {
      if (d.usage) setUsage((u) => addTurnUsage(u, d.usage));
      if (typeof d.compact_at === "number" && d.compact_at > 0) {
        patchReady(live, host, { compact_at: d.compact_at });
      }
      // The event's reasoning is authoritative (covers background-delivered turns); the local
      // buffer is the fallback for older servers.
      const reasoning = d.reasoning || reasoningOf(live.current.blocks);
      const model = typeof d.usage?.model === "string" && d.usage.model ? d.usage.model : undefined;
      live.facts.stepModel = model;
      // What this step spent rides the row it makes: this answer, else its first call (the
      // replay does the same, itemsFromMessages).
      const tokens = addTokens(live.facts.pendingTokens, stepTokens(d.usage));
      live.facts.pendingTokens = d.text || reasoning ? undefined : tokens;
      if (d.text || reasoning)
        setItems((p) => [
          ...p,
          {
            kind: "assistant",
            text: d.text || "",
            ts: Date.now() / 1000,
            ...(reasoning ? { reasoning } : {}),
            ...(Array.isArray(d.tool_calls) && d.tool_calls.length ? { withCalls: true } : {}),
            ...(model ? { model } : {}),
            ...(tokens ? { tokens } : {}),
          },
        ]);
      resetStream(); // finalized into items (or empty tool-only turn)
      break;
    }
    case "tool_proposed":
      if (d.name === TODO_TOOL) {
        // Held, not shown: the plan lands on screen when the call does (tool_finished below).
        // A proposal is not a write, and the replay fold reads writes — the two paths have to
        // agree about the same session (todoPlan.ts).
        live.facts.proposedPlan = planFromTodoWrite(d.name, d.arguments);
      }
      // A tool with its own card (a question, a plan or folder request, the To-dos dock)
      // already said this moment on its dedicated event; the generic row would be the same
      // thing twice. See toolMeta.HIDDEN_TOOLS.
      if (isHiddenTool(d.name)) break;
      const stepSpend = live.facts.pendingTokens;
      live.facts.pendingTokens = undefined;
      setItems((p) => [
        ...p,
        // Stamped at BOTH ends (proposal here, finish in tool_finished): a step row can only
        // say how long it took if someone wrote down when it started.
        {
          kind: "tool",
          id: newId(),
          name: d.name,
          args: d.arguments,
          status: "…",
          ts: Date.now() / 1000,
          // Written whole, not running yet: the engine screens every call of a message before
          // the first runs, and writes run one at a time. `tool_started` clears it, so four
          // edits read as one running and three next in line, not four stuck rows. Only a
          // call with an id can be told apart when it starts.
          // A sub-call of a program is the exception: the engine proposes it from inside the
          // program and never announces a start (`program_calls.py`), so it is running the
          // moment its row exists and would otherwise read "waiting to run" until it ends.
          ...(d.call_id && d.in_program !== true ? { queued: true } : {}),
          // The engine's own id for this call. The finish carries the same one, so the result
          // lands on the row that made it — by NAME alone, four concurrent `query_database`
          // calls hand each other's output around.
          ...(d.call_id ? { callId: d.call_id } : {}),
          // What the tool says about the call BEFORE it runs: the command and its working
          // directory, the diff a write proposes.
          ...(d.presentation ? { callPresentation: d.presentation } : {}),
          // Whether the call only looks, as the server judged it.
          ...(d.looks === true ? { looks: true } : {}),
          ...(live.facts.stepModel ? { model: live.facts.stepModel } : {}),
          ...(stepSpend ? { tokens: stepSpend } : {}),
        },
      ]);
      break;
    case "permission_required":
      setItems((p) => [...p, approvalItemFromEvent(d)]);
      break;
    case "tool_requested":
      setItems((p) => [
        ...p,
        {
          kind: "toolreq",
          tool: d.name || "",
          reason: d.reason || "",
          // Fail CLOSED: only offer Install when the event says a pinned build exists.
          installable: d.installable === true,
          version: d.version || "",
          summary: d.summary || "",
          source: d.source || "",
        },
      ]);
      break;
    case "plan_proposed":
      setItems((p) => [
        ...p,
        {
          kind: "planreq",
          plan: d.plan || "",
          ...(typeof d.path === "string" && d.path ? { path: d.path } : {}),
        },
      ]);
      break;
    case "question_requested":
      // ask_user: answered in the conversation.
      setItems((p) => [
        ...p,
        {
          kind: "question",
          question: d.question || "",
          options: d.options || [],
          allow_text: d.allow_text !== false,
          multi: !!d.multi,
          header: d.header || "",
          questions: d.questions || [],
        },
      ]);
      break;
    case "tool_started":
      setItems((p) => markToolStarted(p, d.call_id, Date.now() / 1000));
      break;
    case "tool_output_delta":
      // A running command's output, as it arrives (harness/tools/output.py). Display only:
      // the call's result still carries the whole text, and a surface that missed these has
      // missed nothing.
      setItems((p) => appendToolOutput(p, d.call_id, String(d.text ?? "")));
      break;
    case "tool_metadata":
      // The parent's live view of a spawned child (step C). Decoration only —
      // `tool_finished` below is what settles the row.
      // A child reporting in is a call that runs, so it is no longer next in line.
      setItems((p) => updateToolMetadata(markToolStarted(p, d.call_id, Date.now() / 1000), d.call_id, d.metadata));
      break;
    case "job_output":
      // A background job's output, coalesced by the engine and pushed as it is read (the reference design's
      // `job.follow`). It rides THIS conversation's own state, so a panel left open in a
      // background session keeps filling — and the jobs list, not this frame, is
      // what says the job is over.
      if (typeof d.task_id === "string" && d.task_id) {
        live.setJobOutput(d.task_id, (previous) =>
          appendJobChunk(
            previous,
            String(d.chunk ?? ""),
            typeof d.total === "number" ? d.total : undefined,
            d.ended === true,
          ),
        );
      }
      break;
    case "jobs":
      // The whole roster, pushed when a job joins it, ends, or prints. It replaces
      // what this conversation holds — a frame that was missed is corrected by the next one.
      if (Array.isArray(d.jobs)) live.setJobs(d.jobs);
      break;
    case "tool_finished":
      if (d.name === TODO_TOOL) {
        const written = live.facts.proposedPlan;
        live.facts.proposedPlan = null;
        // `tool_finished` carries a preview, not the list, so the list comes from the proposal
        // that opened this call — committed only now that it succeeded.
        if (written !== null && d.status === "ok") setTodo(written);
      }
      // No row was opened for a card-superseded tool, so there is nothing to settle — calling
      // `updateLastTool` here could land this result on an unrelated pending row.
      if (isHiddenTool(d.name)) break;
      setItems((p) =>
        updateLastTool(p, {
          name: d.name,
          // A call that dispatched but did not do what was asked (`{"error": …}`) is a failed row,
          // not an "ok" one: `is_error` is the log's own verdict.
          status: d.is_error && d.status === "ok" ? "error" : d.status,
          // `result_text` rides the frame when the 300-character preview was cut: it is the
          // text the model was given, the one a reload shows. Without it a live row drew the
          // cut fragment as the call's output.
          preview: (typeof d.result_text === "string" && d.result_text) || d.result_preview || d.reason,
          hidden: d.display?.hidden_by_filters,
          standingRule: d.standing_rule,
          presentation: d.presentation,
          callId: d.call_id,
          undoable: d.undoable,
          ...(d.looks === true ? { looks: true } : {}),
          // A delegated call's run reference: the row's `· N steps` and its `↗ Open`
          // affordance both come from here (`runRef.ts`).
          ...(() => {
            const ref = runRefOf({
              presentation: d.presentation,
              preview: d.result_preview,
              name: d.name,
            });
            return ref
              ? {
                  runId: ref.runId,
                  ...(ref.steps !== undefined ? { stepCount: ref.steps } : {}),
                }
              : {};
          })(),
          // The call's own wall time from the SERVER's clock. The receipt stamps beside it
          // measure the gap between two events, which charges the tool for the approval it
          // waited on and for every sibling running alongside it.
          durationMs: d.duration_ms,
          endTs: Date.now() / 1000,
          // Media the call produced for the model (`read_image`, an MCP screenshot): the event
          // carries it, so the LIVE row draws the same thumbnail the reloaded one does.
          ...(Array.isArray(d.attachments) && d.attachments.length
            ? { attachments: userItemFromContent(d.attachments).attachments }
            : {}),
          // What Reeve measured on a design page this call wrote.
          ...(Array.isArray(d.design_check) && d.design_check.length ? { designCheck: d.design_check } : {}),
        }),
      );
      // Refresh the right rail when something it shows may have changed: browser state, or a
      // file write that should appear under Artifacts immediately (not only after the turn).
      if (String(d.name || "").startsWith("browser_") || FILE_WRITE_TOOLS.has(d.name)) {
        host.railStale(live);
      }
      break;
    case "turn_end":
      // Nothing is running once the turn is over. A step still marked "…" here never got an
      // answer from the engine, and leaving its spinner turning says the work continues —
      // which also keeps the whole turn group reading as live.
      setItems((p) => p.map((it) => (it.kind === "tool" && it.status === "…" ? { ...it, status: "no result" } : it)));
      appendChanges(d);
      if (STOP_STATUSES.includes(d.status)) {
        // "Max iterations reached" names the counter that stopped the turn and nothing the
        // reader can act on. When the engine identified an action that kept being retried to
        // the same effect, that action IS the explanation. The model's own handoff (the
        // checkpoint message above this notice) carries what to do next.
        const r = d.repeated;
        const loop = d.check_loop;
        const text =
          d.status === "token_budget_exceeded"
            ? `Stopped after ${Number(d.tokens || 0).toLocaleString()} tokens this turn - the handoff above says what remains.`
            : d.status === "no_progress_stopped"
              ? `Stopped after ${d.iterations} steps - ${loop?.command ?? "a check"} kept failing ${loop?.count ?? "several"} times with no change since; the handoff above says what remains.`
              : r
                ? `Stopped after ${d.iterations} steps - it kept ${d.status === "repetition_stopped" ? "repeating" : "trying"} the same thing (${r.summary}) ${r.count} times and got the same result each time.`
                : `Stopped after ${d.iterations} steps without finishing.`;
        setItems((p) => [...p, { kind: "notice", tone: "warn", text }]);
      }
      if (d.unverified) {
        // Code changed this turn and nothing was executed after it. Said here because this is
        // where a person reads the answer: without this sentence "done" is a claim, not a result.
        setItems((p) => [
          ...p,
          {
            kind: "notice",
            tone: "warn",
            text: "Code was changed this turn and nothing was run afterwards - the change is unverified.",
          },
        ]);
      }
      if (d.check_failed) {
        // The last check RAN and failed, and nothing changed since.
        setItems((p) => [
          ...p,
          {
            kind: "notice",
            tone: "warn",
            text: "The last check run this session failed and no file has changed since - the answer above is not verified.",
          },
        ]);
      }
      break;
    case "plugins_reloaded":
      // This view's engine was replaced: reconnect so the next turn runs on the new one.
      setItems((p) => [...p, pluginsReloadedNotice(d.plugins, d.errors)]);
      host.engineReplaced(live);
      break;
    case "model_changed":
      // Server-applied switch: the composer's control and the answer's footer say which model
      // it is. No line in the chat (owner, 2026-10-13: "Model switched to …" after every
      // switch, of the model or the thinking level, was noise). The engine still records it.
      if (d.model) patchReady(live, host, { model: d.model });
      break;
    case "thinking_changed":
      if (typeof d.thinking === "string") patchReady(live, host, { thinking: d.thinking });
      break;
    case "compacting":
      live.setCompacting(true, d.manual === true);
      live.setCompactNote(null);
      break;
    case "compact_failed":
      // Said where the compacting line stood, until the person's next message. Nothing to
      // fold is not a failure and is said quietly.
      live.setCompactNote({
        nothing: d.nothing === true,
        text: String(d.error || "the summarizer could not condense this history"),
        ...(d.detail ? { detail: String(d.detail) } : {}),
      });
      break;
    case "compacted":
      // Auto-compaction marker: outbound-only — the transcript stays intact, this
      // divider just shows where the model's memory was summarized. `now_tokens` is the
      // engine's post-compaction size, and the provider's last price describes a history that
      // is no longer sent — so the meter moves HERE, not one turn later.
      if (d.now_tokens) setUsage((u) => withContext(u, d.now_tokens));
      // The prune tier is quiet in the GUI (owner call 2026-09-23) — the meter still moves
      // (the window really shrank), only the divider is skipped.
      if (d.reason === "pruned") break;
      // A fold the model made itself is a step of the turn and its tool row tells it
      // (`foldBuilder`): a divider here told it twice and cut the turn in two.
      if (d.reason === "folded") break;
      // Closed, like a fold's marker: what it kept is one click away.
      setItems((p) => [...p, compactionNotice(d)]);
      break;
    case "goal": {
      // The goal driver's outcome (round limit reached, paused after a stop/failure) OR a
      // bar-driven mutation (pause/resume/edit/clear). The frame carries the whole view, so
      // the strip moves without a refetch. Only the DRIVER's outcome is also a persisted
      // transcript notice (`driver: true`) — a bar mutation is strip state, and a live-only
      // line for it would vanish on reload.
      if (d.goal_id) {
        setGoal({
          goal_id: String(d.goal_id),
          objective: String(d.objective ?? ""),
          phase: d.phase,
          activation: d.activation === "armed" ? "armed" : "disarmed",
          rounds_started: Number(d.rounds_started ?? 0),
          max_rounds: Number(d.max_rounds ?? 0),
          revision: Number(d.revision ?? 1),
          note: d.note,
          blocker: d.blocker ?? null,
          checks: Array.isArray(d.checks) ? d.checks.map(String) : [],
          ...(Array.isArray(d.check_status) ? { check_status: d.check_status } : {}),
          // The ledger's accounting; absent from an older server.
          ...(typeof d.token_budget === "number" ? { token_budget: d.token_budget } : {}),
          ...(typeof d.tokens_used === "number" ? { tokens_used: d.tokens_used } : {}),
          ...(typeof d.stalled_rounds === "number" ? { stalled_rounds: d.stalled_rounds } : {}),
        });
      } else {
        setGoal(null);
      }
      if (d.driver) {
        setItems((p) => [...p, { kind: "notice", tone: "info", text: d.text || "Goal updated" }]);
      }
      break;
    }
    case "goal_activation":
      // The activation edge (armed/disarmed) rides beside the goal frame; patch the matching
      // goal in place and ignore a stale revision's edge.
      setGoal((prev) =>
        prev && String(d.goal_id) === prev.goal_id && Number(d.revision) === prev.revision
          ? { ...prev, activation: d.activation === "armed" ? "armed" : "disarmed" }
          : prev,
      );
      break;
    case "revert":
      // Message-level revert: another view staged or cleared the marker. The
      // transcript is the server's canonical record, so redraw it from the same read a reload
      // uses — the boundary item and the row gates follow the marker.
      live.setRevert((d.revert as SessionRevert | null) ?? null);
      host.reloadTranscript(live);
      break;
    case "interrupted":
      flushPartialStream();
      setItems((p) => [...p, { kind: "notice", tone: "warn", text: "Interrupted." }]);
      appendChanges(d);
      break;
    case "error": {
      // A bound remote host that fails its start probe is a SETUP refusal, not a turn error:
      // while the draft is still empty it belongs on the setup row (where the host was
      // picked), not as a transcript line that retires the row.
      if (
        typeof d.kind === "string" &&
        REMOTE_ERROR_KINDS.includes(d.kind) &&
        host.showsEnvironmentError(live)
      ) {
        live.facts.environmentError = String(d.error || "could not start the remote session");
        live.setConnected(false);
        host.factsChanged(live, "patch");
        if (live.current.items.length === 0) break;
      }
      flushPartialStream();
      setItems((p) => [
        ...p,
        { kind: "notice", tone: "warn", text: "Error: " + (d.error || "unknown"), retriable: true },
      ]);
      appendChanges(d);
      break;
    }
    case "input_rejected":
      // The optimistic running from send() bought a header for a turn that will never start —
      // hand it back before the notice lands.
      setRunning(false);
      setItems((p) => [...p, { kind: "notice", tone: "warn", text: d.error || "That message was rejected." }]);
      break;
    case "turn_done": {
      setRunning(false);
      // A last step that made no row (a hidden call, then a stop) still counts: its spend goes
      // to the turn's last row.
      const left = live.facts.pendingTokens;
      live.facts.pendingTokens = undefined;
      if (left)
        setItems((p) => {
          for (let i = p.length - 1; i >= 0; i--) {
            const it = p[i];
            if (it.kind === "user") break;
            if (it.kind !== "assistant" && it.kind !== "tool") continue;
            const next = p.slice();
            next[i] = { ...it, tokens: addTokens(it.tokens, left) };
            return next;
          }
          return p;
        });
      // The plan is NOT cleared: the dock unmounts with the turn (SessionDock), and the
      // standing list reopens with the next one, which the engine starts from the same list.
      host.turnEnded(live);
      break;
    }
  }
}
