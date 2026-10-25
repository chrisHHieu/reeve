import type { Attachment, SessionInfo } from "../types";
import type { RemoteEnvironmentSummary } from "./remote-environments";
import { fetch, httpBase } from "./base";


export interface Health {
  status: string;
  default_workspace: string | null;
  model: string;
}

export interface RecentWorkspace {
  path: string;
  name: string;
  exists: boolean;
}

export interface WorkspaceCommandTrust {
  workspace: string;
  requested_commands: string[];
  trusted: boolean;
  required: boolean;
  exists?: boolean;
}

/** One `git worktree list --porcelain` row (harness/worktrees.py), plus the two
 *  booleans the sidebar needs: which entry is the project itself and which is primary. */
export interface Worktree {
  path: string;
  name: string;
  head?: string;
  branch?: string;
  bare?: boolean;
  detached?: boolean;
  locked?: boolean;
  locked_reason?: string;
  prunable?: boolean;
  prunable_reason?: string;
  primary: boolean;
  current: boolean;
}

export interface WorkspaceWorktrees {
  is_git: boolean;
  root: string;
  worktrees: Worktree[];
  error?: string;
}

/** The current workspace's git worktrees (the `/workspace` sidebar section). A non-repo
 *  answers `is_git: false` with an empty list — not an exception to handle. */
export async function getWorkspaceWorktrees(path: string): Promise<WorkspaceWorktrees> {
  const res = await fetch(
    `${httpBase()}/v1/workspaces/worktrees?path=${encodeURIComponent(path)}`,
  );
  return res.json();
}

export async function getHealth(): Promise<Health> {
  const res = await fetch(`${httpBase()}/v1/health`);
  return res.json();
}

export async function getRecentWorkspaces(): Promise<RecentWorkspace[]> {
  const res = await fetch(`${httpBase()}/v1/workspaces/recent`);
  return (await res.json()).workspaces ?? [];
}

/** Ask the LOCAL sidecar to open the OS folder picker — the browser GUI can't obtain absolute
 * paths from web file dialogs. Blocks until the user picks or cancels; null on cancel/unavailable. */
export async function pickFolderViaServer(): Promise<string | null> {
  try {
    const res = await fetch(`${httpBase()}/v1/workspaces/pick`, { method: "POST" });
    const d = await res.json();
    return d.ok && d.path ? d.path : null;
  } catch {
    return null;
  }
}

export async function openWorkspace(
  path: string,
  create = false,
): Promise<{
  path: string;
  ok: boolean;
  error?: string;
  git_branch?: string | null;
  command_trust?: WorkspaceCommandTrust;
}> {
  const res = await fetch(`${httpBase()}/v1/workspaces/open`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, create }),
  });
  return res.json();
}

/** "Start in a temporary folder": create the conversation's temp dir at send time
 * (git-init'd for code-family work). Idempotent. */
export async function createTempWorkspace(
  sessionId: string,
  git = true,
): Promise<{ ok: boolean; path?: string; git?: boolean; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/workspaces/temp`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, git }),
  });
  return res.json();
}

/** "Save as project…": move a session's temporary folder to a real location.
 * Callers reconnect afterwards so the engine rebinds to the new path. */
export async function saveSessionAsProject(
  sessionId: string,
  path: string,
): Promise<{ ok: boolean; path?: string; error?: string }> {
  const res = await fetch(
    `${httpBase()}/v1/sessions/${encodeURIComponent(sessionId)}/save-as-project`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    },
  );
  return res.json();
}

export async function getTrustedWorkspaces(): Promise<WorkspaceCommandTrust[]> {
  const res = await fetch(`${httpBase()}/v1/workspaces/trusted`);
  return (await res.json()).workspaces ?? [];
}

export async function setWorkspaceTrusted(
  path: string,
  trusted: boolean,
): Promise<{ ok: boolean; error?: string } & WorkspaceCommandTrust> {
  const res = await fetch(`${httpBase()}/v1/workspaces/trust`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, trusted }),
  });
  return res.json();
}

export async function getSessions(workspace?: string): Promise<SessionInfo[]> {
  const q = workspace ? `?workspace=${encodeURIComponent(workspace)}` : "";
  const res = await fetch(`${httpBase()}/v1/sessions${q}`);
  return (await res.json()).sessions ?? [];
}

// One `task` agent definition (`GET /v1/subagents`). The card
// needs `color`; the `@` mention menu and the agent picker (P4) read `mode`/`hidden`;
// `default` marks the config's `default_agent`. Settings ▸ Subagents additionally reads
// `source`/`file`/`overrides` (where the definition comes from, and whether a markdown
// file shadows a built-in), the editable raw fields (`prompt`, `tools`,
// `inject_agents`), and `tool_count` for the list badge (null = "All tools").
export interface SubagentAgent {
  name: string;
  description?: string;
  mode?: string;
  model?: string | null;
  color?: string | null;
  steps?: number | null;
  hidden?: boolean;
  native?: boolean;
  default?: boolean;
  source?: "builtin" | "user" | "project";
  /** Absolute path of the markdown file; null for a built-in. */
  file?: string | null;
  /** True when this (built-in) name is shadowed by a markdown file. */
  overrides?: boolean;
  prompt?: string;
  tools?: SubagentTools;
  inject_agents?: boolean;
  /** Concrete tools the allowed groups carry; null means every tool. */
  tool_count?: number | null;
  /** "" = in-process; "codex"/"claude-code" = an external CLI agent. Its `model` is the
   *  CLI's own model (e.g. `sonnet`), never a Reeve provider/model id. */
  provider?: string;
  /** Off in Settings ▸ Subagents: the row stays (so the switch can flip back) while the
   *  model's listing, the `task` tool and the spawn all refuse delegation to it. */
  disabled?: boolean;
}

/** One tool-permission summary as the Settings form reads and writes it. The group
 *  names (`read`, `grep`, `list`, `shell-read`, `write`, `edit`, `shell`, `web`, `skills`,
 *  `memory`, `task`) are the GUI's; the server maps them onto the canonical permission
 *  vocabulary the ruleset speaks. */
export interface SubagentTools {
  preset: "all" | "readonly" | "custom";
  allow: string[];
  deny: string[];
}

export type SubagentScope = "user" | "project";

export async function getSubagents(workspace?: string): Promise<SubagentAgent[]> {
  const q = workspace ? `?workspace=${encodeURIComponent(workspace)}` : "";
  const res = await fetch(`${httpBase()}/v1/subagents${q}`);
  return (await res.json()).agents ?? [];
}

/** The PUT body Settings ▸ Subagents sends. `model: null` clears the field (inherit). */
export interface SubagentWriteBody {
  scope: SubagentScope;
  description: string;
  color: string;
  model: string | null;
  tools: { preset: SubagentTools["preset"]; allow: string[]; deny?: string[] };
  prompt: string;
  inject_agents?: boolean;
}

/** Create or replace one subagent definition as markdown (PUT /v1/subagents/{name}).
 *  Returns `{ok: false, error}` with a 4xx for every rejected input. */
export async function saveSubagent(
  name: string,
  body: SubagentWriteBody,
  workspace?: string,
): Promise<{ ok: boolean; error?: string; name?: string; scope?: SubagentScope; file?: string }> {
  const q = workspace ? `?workspace=${encodeURIComponent(workspace)}` : "";
  const res = await fetch(`${httpBase()}/v1/subagents/${encodeURIComponent(name)}${q}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return res.json();
}

/** The ONE editable field of a built-in subagent (`PUT /v1/subagents/{name}/model`):
 *  `model: null` clears the pin (the built-in inherits the session's model). A name that
 *  is unknown, not a built-in, or owned by a config/markdown definition is a 400 — those
 *  change their model through `saveSubagent`. Answers the effective agent row. */
export async function setSubagentModel(
  name: string,
  model: string | null,
  workspace?: string,
): Promise<{ ok: boolean; error?: string; agent?: SubagentAgent }> {
  const q = workspace ? `?workspace=${encodeURIComponent(workspace)}` : "";
  const res = await fetch(
    `${httpBase()}/v1/subagents/${encodeURIComponent(name)}/model${q}`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model }),
    },
  );
  return res.json();
}

/** The enable switch of a built-in subagent (`PUT /v1/subagents/{name}/enabled`): off
 *  keeps the row listed (so the switch can flip back) but nothing may delegate to it.
 *  Unknown / user-defined / config-defined names are a 400. Answers the effective row. */
export async function setSubagentEnabled(
  name: string,
  enabled: boolean,
  workspace?: string,
): Promise<{ ok: boolean; error?: string; agent?: SubagentAgent }> {
  const q = workspace ? `?workspace=${encodeURIComponent(workspace)}` : "";
  const res = await fetch(
    `${httpBase()}/v1/subagents/${encodeURIComponent(name)}/enabled${q}`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled }),
    },
  );
  return res.json();
}

/** Delete one scope's markdown file (DELETE /v1/subagents/{name}?scope=…). Deleting an
 *  override restores the built-in; a missing file answers `{ok: false, error}` (404). */
export async function deleteSubagent(
  name: string,
  scope: SubagentScope,
  workspace?: string,
): Promise<{ ok: boolean; error?: string }> {
  const params = new URLSearchParams({ scope });
  if (workspace) params.set("workspace", workspace);
  const res = await fetch(
    `${httpBase()}/v1/subagents/${encodeURIComponent(name)}?${params.toString()}`,
    { method: "DELETE" },
  );
  return res.json();
}

// One external CLI provider's install/auth state (`GET /v1/subagents/external`). The
// Subagents page reads it for the provider card — Install when `installed` is false, Sign
// in when installed but not signed in. `signed_in` is a best-effort credential marker; the
// CLI itself still decides auth at run time.
export interface SubagentExternalStatus {
  provider: string;
  installed: boolean;
  /** Absolute path of the CLI binary; "" when it is not installed. */
  path: string;
  signed_in: boolean;
  detail: string;
}

export async function getSubagentsExternal(): Promise<SubagentExternalStatus[]> {
  const res = await fetch(`${httpBase()}/v1/subagents/external`);
  return (await res.json()).providers ?? [];
}

/** Start a provider's browser sign-in (`login`) or global npm install (`install`). Both
 *  run detached and can take minutes; poll `getSubagentsExternal` for the badge flip.
 *  Unknown providers / a missing npm answer `{ok: false, error}`. */
export async function runSubagentExternalAction(
  provider: string,
  action: "login" | "install",
): Promise<{ ok: boolean; error?: string; command?: string }> {
  const res = await fetch(
    `${httpBase()}/v1/subagents/external/${encodeURIComponent(provider)}/${action}`,
    { method: "POST" },
  );
  return res.json();
}

/** The provider account's own model list (`GET /v1/subagents/external/{provider}/models`):
 *  Claude's `/v1/models` read with the CLI's stored OAuth token, OpenAI's list with an API
 *  key. `ok: false` carries why and the GUI falls back to static aliases. */
export interface SubagentExternalModels {
  ok: boolean;
  models?: { id: string; name: string }[];
  /** Where the list came from, e.g. "Claude account" or "OpenAI API key". */
  source?: string;
  error?: string;
}

export async function getSubagentExternalModels(
  provider: string,
): Promise<SubagentExternalModels> {
  const res = await fetch(
    `${httpBase()}/v1/subagents/external/${encodeURIComponent(provider)}/models`,
  );
  return res.json();
}

// One direct `kind="subagent"` child of a session (P3, `GET /v1/sessions/{id}/children`):
// `GET /session/:id/children` reduced to what the sidebar nests.
export interface ChildSessionRow {
  session_id: string;
  title: string;
  agent: string;
  status: "busy" | "idle";
  /** Its own copy of the repository still exists: work may be waiting in it. */
  worktree?: boolean;
}

export async function getSessionChildren(sessionId: string): Promise<ChildSessionRow[]> {
  const res = await fetch(
    `${httpBase()}/v1/sessions/${encodeURIComponent(sessionId)}/children`,
  );
  return (await res.json()).children ?? [];
}

// One parked permission ask: a child tool call
// or a `task` spawn waiting for a reply. Mirrors the server's
// `permission_asked` frame payload minus the routing internals.
export interface PermissionRequestInfo {
  requestId: string;
  sessionId: string;
  parentSession: string;
  /** The tool/permission the ask is about (`run_shell`, `task`, …). */
  permission: string;
  /** The matched value(s) — a command for `run_shell`, the agent type for `task`. */
  patterns: string[];
  /** `task` asks carry `{description, subagent_type}`; otherwise empty. */
  metadata: Record<string, unknown>;
  /** The patterns an "Allow always" would record for the session. */
  always: string[];
  tool?: { call_id?: string } | null;
}

export type PermissionReply = "once" | "always" | "reject";

export async function replyPermission(
  requestId: string,
  reply: PermissionReply,
  feedback = "",
): Promise<{ ok: boolean; error?: string }> {
  const res = await fetch(
    `${httpBase()}/v1/permissions/${encodeURIComponent(requestId)}/reply`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reply, ...(feedback ? { feedback } : {}) }),
    },
  );
  return res.json();
}

// A structured connector-delivered inbound message. Attached to the user message it framed,
// for display only — the model still sees the framed `content`; this drives the ConnectorMessageCard.
export interface MessageSource {
  connector: string; // platform id, e.g. "slack"
  kind: "channel" | "dm";
  channel_id: string; // e.g. "C0BD7KZ1AH5"
  channel_name: string; // resolved; may equal the id (e.g. "#reeve-test")
  sender_id: string;
  sender_name: string; // resolved; may equal the id
  ts: number; // epoch seconds
  text: string; // the RAW message (what the card shows)
}


// A message the ENGINE injected (step D): a finished background child's
// `<task …>` envelope goes in as a normal user-role turn so the model reads it, and this
// sidecar marks it as machine-authored so no client draws it as something a person typed.
// `session_id` is the child that finished.
export interface SyntheticSource {
  synthetic: string; // "background_task"
  session_id?: string;
}

/** Whether a message's `source` sidecar is a connector card (not the engine's marker). */
export function isConnectorSource(
  source: MessageSource | SyntheticSource | undefined,
): source is MessageSource {
  return !!source && typeof (source as { connector?: unknown }).connector === "string";
}

// A transcript message from GET /v1/sessions/{id}/messages. Kept permissive (open shape) because
// itemsFromMessages reads several role-specific fields; `source` is the optional connector sidecar.
export interface ConversationMessage {
  role: string;
  content?: any;
  tool_calls?: any[];
  tool_call_id?: string;
  source?: MessageSource | SyntheticSource;
  // Token counts for the round-trip that produced an assistant message
  // ({model, input, output, cache_read, cache_write}); absent on older servers.
  usage?: import("../types").TurnUsage;
  // User-facing metadata the agent never saw. `_display` carries the privacy filters'
  // counts; `_presentation` carries the tool's own account of its RESULT — the sentence and
  // its card — so a reopened session draws the row the live one drew. Both are stripped from
  // every provider feed before a request is built (engine._outbound_messages).
  _display?: any;
  _presentation?: import("../cards/types").Presented;
  // The call only looked (the server's verdict): a run of these folds into "Explored".
  _looks?: boolean;
  // Marks a user message the engine spliced into a RUNNING turn. The
  // transcript renders it as an in-turn note instead of a turn-opening bubble; it is
  // stripped from every provider feed like `_display`.
  _steer?: boolean;
  // On a tool result: the call did not do what was asked (the log's `is_error`), so the row
  // replays as failed. Stripped from every provider feed like `_display`.
  _failed?: boolean;
  [key: string]: any;
}

export async function getSessionMessages(sessionId: string): Promise<ConversationMessage[]> {
  const res = await fetch(`${httpBase()}/v1/sessions/${sessionId}/messages`);
  return (await res.json()).messages ?? [];
}

// Message-level revert: a marker on the session record, never a message. The
// canonical transcript is complete and unchanged; `boundary` is the canonical index the
// model's view stops at, `staged_at` the transcript length when it was staged. The GUI
// draws the undo boundary from this and gates `/undo` / `/redo`.
export interface SessionRevert {
  boundary: number;
  staged_at: number;
  ts?: number;
}

export interface RevertResult {
  ok: boolean;
  session_id?: string;
  revert?: SessionRevert | null;
  cleared?: boolean;
  message?: { index: number; text: string; attachments?: Attachment[] };
  error?: string;
}

/** The transcript plus the session's revert marker (the messages route carries both). */
export async function getSessionTranscript(
  sessionId: string,
): Promise<{ messages: ConversationMessage[]; revert: SessionRevert | null }> {
  const res = await fetch(`${httpBase()}/v1/sessions/${encodeURIComponent(sessionId)}/messages`);
  const body = await res.json();
  return { messages: body.messages ?? [], revert: body.revert ?? null };
}

/** `/undo`: stage a revert at a user message. `messageId` is
 *  the message's canonical index; omitted, the server picks the last visible user message.
 *  The response's `message` is what the composer pulls back into the draft. */
export async function revertSession(sessionId: string, messageId?: number): Promise<RevertResult> {
  const res = await fetch(`${httpBase()}/v1/sessions/${encodeURIComponent(sessionId)}/revert`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(messageId === undefined ? {} : { message_id: messageId }),
  });
  return res.json();
}

/** `/redo`: walk the boundary forward to the next
 *  user message, or clear it. `revert: null` means the marker was cleared. */
export async function unrevertSession(sessionId: string): Promise<RevertResult> {
  const res = await fetch(`${httpBase()}/v1/sessions/${encodeURIComponent(sessionId)}/unrevert`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
  });
  return res.json();
}

// `/open`: one workspace file, as the picker lists it. `path` is
// relative to the root that holds it, `abs_path` is what the viewer opens (exact even
// when two roots carry the same relative path).
export interface SessionFile {
  path: string;
  abs_path: string;
  name: string;
}

/** Every file of the session's folders as a relative path, read once when the composer's "@"
 *  opens and filtered there as it is typed. A session on a remote host answers `ok: false`
 *  with the reason. */
export async function sessionFileIndex(
  sessionId: string,
): Promise<{ ok: boolean; files: string[]; truncated: boolean; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/sessions/${encodeURIComponent(sessionId)}/files/index`);
  const body = await res.json();
  return {
    ok: body.ok !== false,
    files: Array.isArray(body.files) ? body.files.filter((f: unknown): f is string => typeof f === "string") : [],
    truncated: !!body.truncated,
    ...(typeof body.error === "string" && body.error ? { error: body.error } : {}),
  };
}

/** Search the session's roots (workspace, scratch, extra folders) for files. Noise
 *  directories are pruned server-side and the result is capped; `truncated` says the
 *  list is a prefix. `ok: false` + `error` is the honest refusal a bound remote
 *  session answers with (its files are on the host; browsing them is not here yet) —
 *  never an empty list that would read as "no files". */
export async function searchSessionFiles(
  sessionId: string,
  query = "",
  limit = 50,
): Promise<{ ok: boolean; files: SessionFile[]; truncated: boolean; error?: string }> {
  const params = new URLSearchParams({ q: query, limit: String(limit) });
  const res = await fetch(
    `${httpBase()}/v1/sessions/${encodeURIComponent(sessionId)}/files?${params}`,
  );
  const body = await res.json();
  return {
    ok: body.ok !== false,
    files: body.files ?? [],
    truncated: !!body.truncated,
    ...(typeof body.error === "string" && body.error ? { error: body.error } : {}),
  };
}

// `/export`: the whole transcript as one JSON file, `{ info, messages }`. The server has
// no single-session GET, so the `info` block comes from the sessions list; the messages are the same rows the
// transcript renders. A caller that can't find a session raises — the exporter must not
// write a file that silently claims to be empty.
export interface SessionExportData {
  info: SessionInfo | null;
  messages: ConversationMessage[];
}

export async function exportSession(sessionId: string): Promise<SessionExportData> {
  const [sessions, messages] = await Promise.all([getSessions(), getSessionMessages(sessionId)]);
  const info = sessions.find((s) => s.session_id === sessionId) ?? null;
  if (info === null && messages.length === 0) {
    throw new Error(`Session not found: ${sessionId}`);
  }
  return { info, messages };
}

/** The filename shape: the title (else the id),
 *  lowercased, every run of non `[a-z0-9_-]` collapsed to `-`, ends trimmed, `.json`. */
export function sessionExportFilename(session: { session_id: string; title?: string }): string {
  const name = (session.title || "").trim() || session.session_id;
  const clean = name
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return `${clean || session.session_id}.json`;
}

export async function renameSession(sessionId: string, title: string): Promise<{ ok: boolean; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/sessions/${encodeURIComponent(sessionId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
  return res.json();
}

/** Branch a conversation into a new session seeded with its history: the
 *  parent is untouched, the child records its lineage, and session-scoped "Always
 *  allow" approvals do NOT come along. */
export async function forkSession(
  sessionId: string,
): Promise<{ ok: boolean; session_id?: string; title?: string; parent_session?: string; messages?: number; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/sessions/${encodeURIComponent(sessionId)}/fork`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
  });
  return res.json();
}

// P6, `POST /experimental/session/:id/background`: promote this session's
// running foreground `task` children to background. The waiting `task` call then returns
// `state="running"` (the child keeps working) and its completion is injected into the
// session like any background spawn's. `ok: false` with no running child is the normal
// race when the child just finished — the caller treats it as a no-op.
export async function promoteSessionTasks(
  sessionId: string,
): Promise<{ ok: boolean; session_id?: string; promoted?: string[]; error?: string }> {
  const res = await fetch(
    `${httpBase()}/v1/sessions/${encodeURIComponent(sessionId)}/tasks/promote`,
    { method: "POST", headers: { "Content-Type": "application/json" } },
  );
  return res.json();
}

export async function setSessionFlags(
  sessionId: string,
  flags: { pinned?: boolean; archived?: boolean },
): Promise<{ ok: boolean; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/sessions/${encodeURIComponent(sessionId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(flags),
  });
  return res.json();
}

export async function deleteSession(sessionId: string): Promise<{ ok: boolean; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/sessions/${encodeURIComponent(sessionId)}`, { method: "DELETE" });
  return res.json();
}

// One session's identity + environment binding.
// `environment` is the reduced row {id, name, user, host, port} — never identity_file.
export interface SessionSummary {
  ok: boolean;
  session_id?: string;
  workspace?: string;
  /** The POSIX path on the bound host; null locally. The remote readout's path half. */
  remote_workspace?: string | null;
  environment_id?: string | null;
  environment?: RemoteEnvironmentSummary | null;
  error?: string;
}

export async function getSessionSummary(sessionId: string): Promise<SessionSummary> {
  const res = await fetch(`${httpBase()}/v1/sessions/${encodeURIComponent(sessionId)}`);
  return res.json();
}

export interface SessionEnvironmentResult {
  ok: boolean;
  session_id?: string;
  environment_id?: string | null;
  workspace?: string;
  /** The bound host's POSIX workspace (null on a Local switch). */
  remote_workspace?: string | null;
  environment?: RemoteEnvironmentSummary | null;
  error?: string;
}

/** Bind, switch, or clear a session's SSH environment (PATCH /v1/sessions/{id}).
 *  `environmentId: null` clears to Local; a remote bind passes the absolute POSIX
 *  `workspace` on that host. A refused bind answers `{ok: false, error}` (400). */
export async function setSessionEnvironment(
  sessionId: string,
  environmentId: string | null,
  workspace?: string,
): Promise<SessionEnvironmentResult> {
  const body: Record<string, unknown> = { environment_id: environmentId };
  if (workspace !== undefined) body.workspace = workspace;
  const res = await fetch(`${httpBase()}/v1/sessions/${encodeURIComponent(sessionId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return res.json();
}

// The session's goal (`harness/goals.py`; GET/POST /v1/sessions/{id}/goal).
//
// The durable `phase` records what happened to the objective; `activation` is
// PROCESS-LOCAL (armed | disarmed) and says whether this harness may open another round.
// A restart disarms without rewriting the phase, so a reopened session shows an active
// goal with `activation: "disarmed"` ("Inactive Goal") until someone resumes it.
export type GoalPhase = "active" | "paused" | "blocked" | "complete";
export type GoalActivation = "armed" | "disarmed";

export interface GoalInfo {
  goal_id: string;
  objective: string;
  phase: GoalPhase;
  activation: GoalActivation;
  rounds_started: number;
  max_rounds: number;
  revision: number;
  note?: string;
  /** Present exactly while `phase === "blocked"`: a lower-kebab code + a sentence. */
  blocker?: { code: string; message: string } | null;
  /** Acceptance checks: commands that must pass after the last edit before `done` is
   *  accepted (harness/goals.py completion_gaps). */
  checks?: string[];
  /** Each check's standing, read from the transcript the way the done-gate reads it
   *  (harness/goal_proof.py check_states). Surface-only; absent with no checks. */
  check_status?: { command: string; state: "passed" | "failed" | "stale" | "unknown" | "not_run" | "variant" }[];
  /** The harness's own accounting: provider tokens the rounds spent, the cap on
   *  them (0 = none), and consecutive rounds that moved nothing — the goal is blocked
   *  `budget` / `no-progress` when either runs out. Absent from an older server. */
  token_budget?: number;
  tokens_used?: number;
  stalled_rounds?: number;
}

/** Read the session's goal, or null when none is set. */
export async function getSessionGoal(sessionId: string): Promise<GoalInfo | null> {
  const res = await fetch(`${httpBase()}/v1/sessions/${encodeURIComponent(sessionId)}/goal`);
  return (await res.json()).goal ?? null;
}

/** The human's goal controls (the GoalBar's buttons plus the composer's `/goal` command).
 *  `set` needs an `objective` (and takes optional `max_rounds` / `token_budget`); `edit`
 *  takes `objective`, `max_rounds` and/or `token_budget`; everything else takes no fields. A refusal is
 *  `{ok: false, error}`. */
export async function goalAction(
  sessionId: string,
  action: "set" | "pause" | "resume" | "edit" | "clear",
  fields: { objective?: string; max_rounds?: number; token_budget?: number } = {},
): Promise<{ ok: boolean; goal?: GoalInfo | null; error?: string; note?: string }> {
  const res = await fetch(`${httpBase()}/v1/sessions/${encodeURIComponent(sessionId)}/goal`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action, ...fields }),
  });
  return res.json();
}

export interface ArtifactInfo {
  path: string; // workspace-relative (the display/API identifier)
  abs_path?: string; // absolute — what "Copy path" copies
  name: string;
  kind: "markdown" | "html" | "image" | "code" | "text" | string;
  size: number;
  modified_at: number;
  // Which rail surface opened it — drives the viewer's breadcrumb ("Artifacts" vs
  // "Files"). Absent = artifacts.
  origin?: "artifacts" | "files";
}

export interface ArtifactContent {
  ok: boolean;
  error?: string;
  path: string;
  kind: string;
  content?: string;
  data_url?: string;
  truncated?: boolean;
  // Text reads carry the file's size and mtime (the editor's save token).
  size?: number;
  mtime_ms?: number;
  // What a text file's bytes were read as, and what the person may name it as instead.
  encoding?: string;
  encodings?: string[];
  // kind === "folder": a directory listing (models sometimes link a whole package dir).
  entries?: { name: string; dir: boolean; size: number }[];
}

export interface BrowserPreviewState {
  active: boolean;
  url?: string;
  title?: string;
  image?: string;
  reason?: string;
}

export async function getWorkspaceBranch(path: string): Promise<string | null> {
  try {
    const res = await fetch(
      `${httpBase()}/v1/workspace-branch?path=${encodeURIComponent(path)}`,
    );
    const data = await res.json();
    return data.branch ?? null;
  } catch {
    return null;
  }
}

export async function getBrowserPreview(): Promise<BrowserPreviewState> {
  const res = await fetch(`${httpBase()}/v1/browser-preview`);
  return await res.json();
}

export async function getArtifacts(sessionId: string): Promise<ArtifactInfo[]> {
  const res = await fetch(`${httpBase()}/v1/sessions/${encodeURIComponent(sessionId)}/artifacts`);
  return (await res.json()).artifacts ?? [];
}

export async function readArtifact(sessionId: string, path: string, encoding?: string): Promise<ArtifactContent> {
  const q = new URLSearchParams({ path });
  // The person named what this text file is saved as (the read's own guess was wrong).
  if (encoding) q.set("encoding", encoding);
  const res = await fetch(`${httpBase()}/v1/sessions/${encodeURIComponent(sessionId)}/artifacts/read?${q.toString()}`);
  return res.json();
}

// The preview door: a second local address that serves a file the person opened
// to look at. A page gets its session folder behind it; `single` grants one file alone (a
// video, a document's bytes). `internet` is the person's own yes, asked for in a dialog.
export interface PreviewGrant {
  ok: boolean;
  error?: string;
  url?: string;
  origin?: string;
  key?: string;
  internet?: boolean;
}

export async function previewGrant(
  sessionId: string,
  path: string,
  options: { internet?: boolean; single?: boolean } = {},
): Promise<PreviewGrant> {
  const res = await fetch(`${httpBase()}/v1/sessions/${encodeURIComponent(sessionId)}/preview/grant`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, internet: options.internet === true, single: options.single === true }),
  });
  if (!res.ok) return { ok: false, error: "This server cannot show files this way yet. Restart Reeve." };
  return res.json();
}

/** One version of a design page: what the check after that write recorded (phase 3d). */
export interface DesignVersion {
  n: number;
  call_id?: string;
  ts?: number;
  summary: string;
  issues: number;
  changes: string[];
  unasked: string[];
  thumb?: string | null;
}

export async function getDesignVersions(sessionId: string, path: string): Promise<DesignVersion[]> {
  const q = new URLSearchParams({ path });
  const res = await fetch(`${httpBase()}/v1/sessions/${encodeURIComponent(sessionId)}/design/versions?${q.toString()}`);
  if (!res.ok) return [];
  const body = await res.json();
  return body?.ok && Array.isArray(body.versions) ? body.versions : [];
}

/** One of the session's design pages, with what its last check said. */
export interface DesignFile {
  path: string;
  name: string;
  mtime_ms: number;
  /** How many checks this page's writes left; 0 for a page Reeve never wrote. */
  checks: number;
  summary: string;
  issues: number;
  thumb?: string | null;
  checked_at?: number | null;
  /** `pricing.a.html` is variant `a` of `pricing.html`. */
  variant_of?: string;
  letter?: string;
  /** Whether the person locked it. */
  lock?: DesignLockState;
}

export interface DesignFiles {
  files: DesignFile[];
  design_md: { path: string; colors: string[] } | null;
}

export async function getDesignFiles(sessionId: string): Promise<DesignFiles | null> {
  const res = await fetch(`${httpBase()}/v1/sessions/${encodeURIComponent(sessionId)}/design/files`);
  if (!res.ok) return null;
  const body = await res.json();
  return body?.ok && Array.isArray(body.files) ? { files: body.files, design_md: body.design_md ?? null } : null;
}

/** A picture of one area of a design page laid out at `width`, from Reeve's own browser (a mark). */
export async function getDesignShot(
  sessionId: string,
  path: string,
  width: number,
  area: { x: number; y: number; w: number; h: number },
): Promise<string | null> {
  const res = await fetch(`${httpBase()}/v1/sessions/${encodeURIComponent(sessionId)}/design/shot`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, width, area }),
  });
  if (!res.ok) return null;
  const body = await res.json();
  return body?.ok && typeof body.png === "string" ? body.png : null;
}

export type DesignExportKind = "pdf" | "png" | "zip" | "html";

/** A design page as a PDF, a picture, a zip or one HTML file, saved to Downloads by the server;
 *  with `deliver: "data"`, a picture (`png`) or the text (`html`) handed back for the clipboard. */
export async function exportDesign(
  sessionId: string,
  path: string,
  kind: DesignExportKind,
  options: { width?: number; internet?: boolean; deliver?: "file" | "data" } = {},
): Promise<{ ok: boolean; saved?: string; name?: string; folder?: string; png?: string; html?: string; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/sessions/${encodeURIComponent(sessionId)}/design/export`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, kind, width: options.width, internet: !!options.internet, deliver: options.deliver ?? "file" }),
  });
  if (!res.ok) return { ok: false, error: `the server answered ${res.status}` };
  return res.json();
}

/** Show a file Reeve saved to Downloads in its folder. */
export async function revealDownload(path: string): Promise<{ ok: boolean; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/downloads/reveal`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
  return res.ok ? res.json() : { ok: false };
}

export interface Editor {
  id: string;
  name: string;
}

/** The editors on this machine a file can be opened in. */
export async function getEditors(): Promise<Editor[]> {
  const res = await fetch(`${httpBase()}/v1/editors`);
  if (!res.ok) return [];
  const body = await res.json();
  return Array.isArray(body?.editors) ? body.editors : [];
}

export async function openInEditor(sessionId: string, path: string, editor: string): Promise<{ ok: boolean; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/sessions/${encodeURIComponent(sessionId)}/open-in`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, editor }),
  });
  return res.ok ? res.json() : { ok: false };
}

/** The Browser tab's page as Reeve's own browser loads it: a picture of its first screen, its look
 *  in words, or its HTML saved to Downloads. */
export async function browserCapture(
  url: string,
  kind: "view" | "brief" | "html",
  width?: number,
): Promise<{ ok: boolean; png?: string; brief?: string; title?: string; saved?: string; name?: string; folder?: string; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/browser/capture`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url, kind, width }),
  });
  if (!res.ok) return { ok: false, error: `the server answered ${res.status}` };
  return res.json();
}

export function previewRevoke(key: string): void {
  void fetch(`${httpBase()}/v1/preview/revoke`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ key }),
  }).catch(() => undefined);
}

// The file manager's mutations (the reference design's New file / New folder / Rename / Move). Paths
// are relative to a session root; the server confines every write to a writable root.
export interface FileEditResult {
  ok: boolean;
  path?: string;
  error?: string;
}

async function postFileEdit(sessionId: string, action: string, body: Record<string, string>): Promise<FileEditResult> {
  const res = await fetch(
    `${httpBase()}/v1/sessions/${encodeURIComponent(sessionId)}/files/${action}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
  );
  return res.json();
}

export function createSessionEntry(
  sessionId: string,
  path: string,
  kind: "file" | "folder",
): Promise<FileEditResult> {
  return postFileEdit(sessionId, "create", { path, kind });
}

export function renameSessionEntry(sessionId: string, path: string, name: string): Promise<FileEditResult> {
  return postFileEdit(sessionId, "rename", { path, name });
}

export function moveSessionEntry(sessionId: string, path: string, to: string): Promise<FileEditResult> {
  return postFileEdit(sessionId, "move", { path, to });
}

/** Whether a page outside this computer lets the Browser tab frame it (its headers, read by the
 *  server through the web guard; `harness/integrations/web/frameable.py`). */
export async function browserFrameCheck(
  url: string,
): Promise<{ ok: boolean; frameable?: boolean; reason?: string; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/browser/frame-check?url=${encodeURIComponent(url)}`);
  return res.json();
}

/** To the system's trash (Recycle Bin / Trash), never a permanent delete; the GUI asks first. */
export function deleteSessionEntry(sessionId: string, path: string): Promise<FileEditResult> {
  return postFileEdit(sessionId, "delete", { path });
}

export interface FileWriteResult extends FileEditResult {
  conflict?: boolean;
  size?: number;
  mtime_ms?: number;
}

export async function writeSessionEntry(
  sessionId: string,
  path: string,
  content: string,
  mtimeMs?: number,
  /** The encoding the file was shown in: the save writes the same back. */
  encoding?: string,
): Promise<FileWriteResult> {
  const res = await fetch(
    `${httpBase()}/v1/sessions/${encodeURIComponent(sessionId)}/files/write`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path, content, mtime_ms: mtimeMs, encoding }),
    },
  );
  return res.json();
}

/** Whether a design page is locked: the version the app is built from. */
export interface DesignLockState {
  locked: boolean;
  locked_at?: number | null;
  /** The page is no longer the version that was locked. */
  changed?: boolean;
  handoff?: string | null;
  accepted?: number;
}

/** What locking a page measured, for the lock dialog. */
export interface DesignLockSummary {
  page: string;
  widths: number[];
  colors: string[];
  colors_count: number;
  colors_unnamed: string[];
  design_md: string | null;
  fonts: string[];
  sizes: number[];
  spacing: number[];
  parts: string[];
  parts_count: number;
  states: number;
  opens: number;
  motion: string[];
  /** What the check still finds, which locking accepts. */
  accepted: string[];
  /** The project's app as Reeve read it from its files, and what of the design is new to it. */
  app?: {
    found: boolean;
    root?: string;
    stack?: string;
    theme?: string[];
    colors?: number;
    components?: number;
    component_dir?: string;
    colors_new?: number;
    fonts_new?: string[];
  };
}

/** The project a locked design is built into; null for a session with no project folder. */
export interface DesignBuildTarget {
  name: string;
  path: string;
}

export interface DesignLockAnswer {
  ok: boolean;
  error?: string;
  state?: DesignLockState;
  summary?: DesignLockSummary;
  target?: DesignBuildTarget | null;
  handoff?: string;
  lock_file?: string;
}

export async function getDesignLock(sessionId: string, path: string): Promise<DesignLockAnswer> {
  const q = new URLSearchParams({ path });
  const res = await fetch(`${httpBase()}/v1/sessions/${encodeURIComponent(sessionId)}/design/lock?${q.toString()}`);
  return res.ok ? res.json() : { ok: false, error: `the server answered ${res.status}` };
}

/** Measure a page for its lock (`preview`, nothing written), or lock it: the server writes the lock
 *  and the handoff. */
export async function lockDesign(sessionId: string, path: string, preview: boolean): Promise<DesignLockAnswer> {
  const res = await fetch(`${httpBase()}/v1/sessions/${encodeURIComponent(sessionId)}/design/lock`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, preview }),
  });
  return res.ok ? res.json() : { ok: false, error: `the server answered ${res.status}` };
}

/** One difference between the app and its design: a fix, a thing to check, or one the
 *  person kept as the app has it. `id` is stable while the difference is the same one. */
export interface DesignDifference {
  id: string;
  kind: "fix" | "check" | "accepted";
  text: string;
}

export interface DesignReviewAnswer {
  ok: boolean;
  error?: string;
  page?: string;
  url?: string;
  width?: number;
  findings?: DesignDifference[];
  widths?: number[];
  issues?: number;
  locked?: boolean;
  changed_since_lock?: boolean;
  design_png?: string | null;
  app_png?: string | null;
  app_error?: string;
  state?: DesignLockState & { kept?: number; reviewed?: { url: string; at: number; issues: number } | null };
}

/** The design and the running app as pictures at `width`; with `compare`, what differs. */
export async function reviewDesign(
  sessionId: string,
  body: { path: string; url: string; width: number; compare: boolean },
): Promise<DesignReviewAnswer> {
  const res = await fetch(`${httpBase()}/v1/sessions/${encodeURIComponent(sessionId)}/design/review`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return res.ok ? res.json() : { ok: false, error: `the server answered ${res.status}` };
}

/** Keep one difference as the app has it, or record that the app was reviewed and is done. */
export async function designReviewAct(
  sessionId: string,
  act: "keep" | "done",
  body: { path: string; id?: string; text?: string; url?: string; issues?: number },
): Promise<DesignReviewAnswer> {
  const res = await fetch(`${httpBase()}/v1/sessions/${encodeURIComponent(sessionId)}/design/review/${act}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return res.ok ? res.json() : { ok: false, error: `the server answered ${res.status}` };
}
