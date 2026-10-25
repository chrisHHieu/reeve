import type { SentCommand, WsEvent } from "../types";
import { fetch, httpBase, openWebSocket, wsBase } from "./base";
import type { GmailFilters } from "./connectors";

// -- automations ---------------------------------------------------------------
export interface Automation {
  id: string;
  title: string;
  instructions: string;
  schedule: string;
  schedule_raw?: { kind: string; cron?: string | null; fire_at?: string | null; timezone?: string };
  workspace: string;
  agent: string;
  // Pinned model (null = server default) and the chat composer's permission mode the
  // unattended run takes — the editor's control row, round-tripped.
  model?: string | null;
  mode?: string | null;
  enabled: boolean;
  next_run: number | null;
  last_run: number | null;
  last_status: string | null;
  run_count: number;
  notify_on_completion: boolean;
  // Sidebar badges: runs started since the user last opened this automation's
  // detail; `unseen_failed` = the newest unseen run errored (danger tint).
  unseen_runs?: number;
  unseen_failed?: boolean;
  seen_runs_at?: number;
  // Its newest run, in the list: what the row says the automation last came to.
  latest_run?: AutomationRun | null;
  // Standing scoped approvals: target-bound rules this automation may exercise
  // without asking. `entry` is the raw record entry — the revoke handle; `target` is
  // null for legacy name-only entries.
  always_allowed: { entry: string; tool: string; target: string | null }[];
}

export interface AutomationRun {
  run_id: string;
  task_id: string;
  session_id: string;
  started_at: number;
  finished_at: number | null;
  status: string;
  result_text: string | null;
  artifacts: string[];
  error: string | null;
  trigger: string;
  // How many prompts of the run's conversation wait on the person, and the kind of the
  // oldest: the run reads "Needs you" while there is one.
  attention?: number;
  attention_kind?: string | null;
}

export async function getAutomations(): Promise<Automation[]> {
  const res = await fetch(`${httpBase()}/v1/automations`);
  // A failed read is not an empty list: the page tells the two apart.
  if (!res.ok) throw new Error(`automations: ${res.status}`);
  return (await res.json()).tasks ?? [];
}

// Fired after any automation mutation the sidebar should reflect immediately
// (mark-seen, create, delete) — its poll covers the rest.
export const AUTOMATIONS_CHANGED = "reeve:automations-changed";
export function announceAutomationsChanged() {
  window.dispatchEvent(new CustomEvent(AUTOMATIONS_CHANGED));
}

/** App-wide event stream (/ws/events): session-independent server pushes — today
 * automation_run_started (the toast). Quietly reconnects while the app is
 * open; the returned cleanup stops it for good. */
export function connectEvents(
  onEvent: (msg: { type: string; data?: Record<string, unknown> }) => void
): () => void {
  let ws: WebSocket | null = null;
  let timer: number | null = null;
  let closed = false;
  const open = () => {
    if (closed) return;
    ws = openWebSocket(`${wsBase()}/ws/events`);
    ws.onmessage = (e) => {
      try {
        onEvent(JSON.parse(e.data));
      } catch {
        /* malformed frame — ignore */
      }
    };
    ws.onclose = () => {
      if (!closed) timer = window.setTimeout(open, 5000);
    };
  };
  open();
  return () => {
    closed = true;
    if (timer !== null) window.clearTimeout(timer);
    ws?.close();
  };
}

/** Advance the automation's seen mark — clears its unseen-runs badge. */
export async function markAutomationSeen(id: string): Promise<{ ok: boolean }> {
  const res = await fetch(`${httpBase()}/v1/automations/${id}/seen`, { method: "POST" });
  return res.json();
}

export async function createAutomation(payload: {
  title: string;
  instructions: string;
  cron?: string;
  fire_at?: string;
  timezone?: string;
  // The editor's control row: run in a project folder ("" = managed scratch), pin a
  // model ("" = server default), and pick one of the chat composer's permission modes.
  workspace?: string;
  model?: string;
  mode?: string;
  // Standing grants (the creating surface rendered them; submit IS the consent).
  // Only target-bound write entries survive server-side validation.
  permissions?: { tool: string; target: string; access: "read" | "write" }[];
}): Promise<{ ok: boolean; error?: string; task?: Automation }> {
  const res = await fetch(`${httpBase()}/v1/automations`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return res.json();
}

export async function getAutomation(id: string): Promise<{ task: Automation; runs: AutomationRun[] }> {
  const res = await fetch(`${httpBase()}/v1/automations/${encodeURIComponent(id)}`);
  return res.json();
}

export async function updateAutomation(id: string, changes: Record<string, any>) {
  const res = await fetch(`${httpBase()}/v1/automations/${encodeURIComponent(id)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(changes),
  });
  return res.json();
}

export async function deleteAutomation(id: string) {
  const res = await fetch(`${httpBase()}/v1/automations/${encodeURIComponent(id)}`, { method: "DELETE" });
  return res.json();
}

export interface PreparedRun {
  ok: boolean;
  error?: string;
  run_id: string;
  session_id: string;
  workspace: string;
  agent: string;
  prompt: string;
}

/** Prepare a live manual run: returns the session to open + the opening prompt to send. */
export async function runAutomation(id: string): Promise<PreparedRun> {
  const res = await fetch(`${httpBase()}/v1/automations/${encodeURIComponent(id)}/run`, { method: "POST" });
  return res.json();
}

/** Mark a manual run complete after its first turn finished. */
export async function finalizeAutomationRun(id: string, runId: string) {
  const res = await fetch(
    `${httpBase()}/v1/automations/${encodeURIComponent(id)}/runs/${encodeURIComponent(runId)}/finalize`,
    { method: "POST" },
  );
  return res.json();
}

export async function allowUser(name: string, userId: string, displayName?: string) {
  const res = await fetch(`${httpBase()}/v1/connectors/${encodeURIComponent(name)}/allow`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      user_id: userId,
      // Directory picks carry the display name so the chip is readable at once.
      ...(displayName ? { name: displayName } : {}),
    }),
  });
  return res.json();
}

// One workspace member from the roster (people picker; users:read, cached locally).
export interface SlackMember {
  id: string;
  name: string;
  handle: string;
  guest: boolean;
}

// One channel from the workspace roster. Private channels appear only where the
// bot is a member (Slack API constraint); is_member=false → "invite @Reeve" hint.
export interface SlackChannelEntry {
  id: string;
  name: string;
  is_private: boolean;
  is_member: boolean;
}

/** Workspace member roster for the people picker. */
export async function getSlackDirectory(
  q = "",
): Promise<{ ok: boolean; error?: string; members?: SlackMember[] }> {
  const res = await fetch(`${httpBase()}/v1/connectors/slack/directory?q=${encodeURIComponent(q)}`);
  return res.json();
}

/** Channel roster for the channel typeahead (name → id resolution). */
export async function getSlackChannels(
  q = "",
): Promise<{ ok: boolean; error?: string; channels?: SlackChannelEntry[] }> {
  const res = await fetch(`${httpBase()}/v1/connectors/slack/channels?q=${encodeURIComponent(q)}`);
  return res.json();
}

/** Resolve a parked unauthorized message: dismiss / allow / allow_deliver. */
export async function resolveUnauthorized(
  name: string,
  itemId: string,
  action: "dismiss" | "allow" | "allow_deliver",
): Promise<{ ok: boolean; error?: string }> {
  const res = await fetch(
    `${httpBase()}/v1/connectors/${encodeURIComponent(name)}/unauthorized/${encodeURIComponent(itemId)}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action }),
    },
  );
  return res.json();
}

export async function disallowUser(name: string, userId: string) {
  const res = await fetch(`${httpBase()}/v1/connectors/${encodeURIComponent(name)}/disallow`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ user_id: userId }),
  });
  return res.json();
}

/** Drop ONE Gmail mailbox; the default pointer moves to the next account. */
export async function disconnectGmailAccount(email: string): Promise<{ ok: boolean; error?: string; remaining_accounts?: number }> {
  const res = await fetch(
    `${httpBase()}/v1/connectors/gmail/accounts/${encodeURIComponent(email)}/disconnect`,
    { method: "POST" },
  );
  return res.json();
}

export async function setGmailDefaultAccount(email: string): Promise<{ ok: boolean; error?: string }> {
  const res = await fetch(
    `${httpBase()}/v1/connectors/gmail/accounts/${encodeURIComponent(email)}/default`,
    { method: "POST" },
  );
  return res.json();
}

/** Drop ONE Google Calendar account; the default pointer moves to the next one. */
export async function disconnectGcalAccount(email: string): Promise<{ ok: boolean; error?: string; remaining_accounts?: number }> {
  const res = await fetch(
    `${httpBase()}/v1/connectors/google_calendar/accounts/${encodeURIComponent(email)}/disconnect`,
    { method: "POST" },
  );
  return res.json();
}

export async function setGcalDefaultAccount(email: string): Promise<{ ok: boolean; error?: string }> {
  const res = await fetch(
    `${httpBase()}/v1/connectors/google_calendar/accounts/${encodeURIComponent(email)}/default`,
    { method: "POST" },
  );
  return res.json();
}

/** Drop ONE account of a generic multi-account connector (notion, attio,
 * posthog, …); the default pointer moves to the next account. */
export async function disconnectAccount(connector: string, accountId: string): Promise<{ ok: boolean; error?: string; remaining_accounts?: number }> {
  const res = await fetch(
    `${httpBase()}/v1/connectors/${encodeURIComponent(connector)}/accounts/${encodeURIComponent(accountId)}/disconnect`,
    { method: "POST" },
  );
  return res.json();
}

export async function setDefaultAccount(connector: string, accountId: string): Promise<{ ok: boolean; error?: string }> {
  const res = await fetch(
    `${httpBase()}/v1/connectors/${encodeURIComponent(connector)}/accounts/${encodeURIComponent(accountId)}/default`,
    { method: "POST" },
  );
  return res.json();
}

/** Replace the "Never show agents" lists (senders and/or labels; omit to keep). */
export async function setGmailFilters(filters: { senders?: string[]; labels?: string[] }): Promise<{ ok: boolean; filters?: GmailFilters; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/connectors/gmail/filters`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(filters),
  });
  return res.json();
}

export type Handlers = {
  onEvent: (event: WsEvent) => void;
  onOpen?: () => void;
  onClose?: () => void;
};

export class Session {
  private ws: WebSocket;
  // Payloads sent before the socket finished opening, replayed on `onopen`. Belt-and-suspenders
  // against the first message being dropped if the user sends in the connect window.
  private outbox: object[] = [];

  constructor(sessionId: string, workspace: string, handlers: Handlers) {
    const q = `?workspace=${encodeURIComponent(workspace)}`;
    this.ws = openWebSocket(`${wsBase()}/ws/session/${sessionId}${q}`);
    this.ws.onmessage = (e) => {
      try {
        handlers.onEvent(JSON.parse(e.data));
      } catch {
        /* malformed frame — ignore */
      }
    };
    this.ws.onopen = () => {
      this.flush();
      handlers.onOpen?.();
    };
    this.ws.onclose = () => handlers.onClose?.();
  }

  /** The underlying socket's `readyState` (0 connecting, 1 open, 2 closing, 3 closed). */
  get readyState(): number {
    return this.ws.readyState;
  }

  private flush() {
    if (this.ws.readyState !== WebSocket.OPEN) return;
    const pending = this.outbox;
    this.outbox = [];
    for (const p of pending) this.ws.send(JSON.stringify(p));
  }

  private send(payload: object) {
    if (this.ws.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(payload));
    // Still connecting: queue and flush on open rather than silently dropping.
    else if (this.ws.readyState === WebSocket.CONNECTING) this.outbox.push(payload);
  }

  /** `model` = the composer's CURRENT selection, carried on every message so the turn uses
   * exactly what the user sees — immune to set_model races across reconnects (a new
   * session always reconnects once to adopt its scratch dir, which could drop a queued
   * set_model and leave the engine on a stale model; found 2026-07-04). */
  userMessage(
    text: string,
    attachments?: unknown[],
    model?: string,
    skill?: string,
    mentions?: string[],
    /** Steering: a plain-text sentence the LIVE turn reads at its next step.
     *  The server refuses this with attachments/skill/@mentions (they frame a new
     *  turn); a steer on an idle session just runs as a normal message. */
    steer?: boolean,
    /** The slash command the message was sent with, for the transcript. */
    command?: SentCommand,
  ) {
    this.send({
      type: "user_message",
      text,
      ...(command ? { command } : {}),
      ...(steer ? { steer: true } : {}),
      ...(model ? { model } : {}),
      ...(attachments?.length ? { attachments } : {}),
      // Force-run: the composer's /skill pick rides as its own field;
      // the server validates it against the session's effective menu and frames the turn.
      ...(skill ? { skill } : {}),
      // @-mentions (P4): the agent names the composer recognised. The server appends
      // The synthetic instruction to the model-facing text, keeps the literal
      // line as `_display`, and marks the turn so the `task` spawn ask is skipped
      // (`bypassAgentCheck`).
      ...(mentions?.length ? { mentions } : {}),
    });
  }

  approve(decision: string, arguments_?: Record<string, unknown>) {
    // `modify` carries the person's edited arguments; every other decision is a bare word.
    this.send({
      type: "approval",
      decision,
      ...(decision === "modify" && arguments_ ? { arguments: arguments_ } : {}),
    });
  }

  // Reply to a `request_tool` prompt: install the pinned build, or skip the check.
  respondTool(approved: boolean) {
    this.send({ type: "tool_response", approved });
  }

  // Reply to a `propose_plan` prompt: approve (choosing the execution mode) or reject with feedback.
  respondPlan(approved: boolean, mode?: string, feedback?: string) {
    this.send({
      type: "plan_response",
      approved,
      ...(mode ? { mode } : {}),
      ...(feedback ? { feedback } : {}),
    });
  }

  // Answer a live `ask_user` prompt.
  respondQuestion(answer: string) {
    this.send({ type: "question_response", answer });
  }

  interrupt() {
    this.send({ type: "interrupt" });
  }

  // Re-run a turn that ended in a provider error — no new user message; the server
  // guards on the history tail so a stray frame is a no-op.
  retry() {
    this.send({ type: "retry" });
  }

  setMode(mode: string) {
    this.send({ type: "set_mode", mode });
  }

  /** The Plan overlay: ON hard-denies consequential tools while the agent explores
   *  read-only and proposes a plan. Orthogonal to `set_mode`; the server answers with a
   *  `plan_changed` broadcast (and `ready.plan` on reconnect). */
  setPlan(plan: boolean) {
    this.send({ type: "set_plan", plan });
  }

  setModel(model: string) {
    this.send({ type: "set_model", model });
  }

  // The session's thinking level. "" or "default" clears the override (the model's own
  // default rides). Best-effort like `setModel`: the server ignores it mid-turn.
  setThinking(level: string) {
    this.send({ type: "set_thinking", thinking: level });
  }

  close() {
    // Detach before closing: this socket's async `close` event may land AFTER the
    // successor session's `open` (observed when switching into an automation-run
    // session), and a torn-down socket must not clobber the new one's connected state.
    this.ws.onopen = null;
    this.ws.onmessage = null;
    this.ws.onclose = null;
    this.ws.close();
  }
}
