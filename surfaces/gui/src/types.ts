import type { Presented } from "./humanize";
// Type-only, so api.ts may import these types back without a runtime cycle.
import type { MessageSource } from "./api";
import type { RemoteEnvironmentSummary } from "./api/remote-environments";

// The shapes the GUI shares between its socket, its API calls and its transcript: the frames a
// session's socket sends, a session's row, what a turn spent, and the items a transcript is drawn
// from. Names and values here are the server's; a field the server may not send is optional.

// -- Socket frames ------------------------------------------------------------------------------

export type EventType =
  // A turn, as it streams.
  | "ready"
  | "inbound"
  | "turn_start"
  | "assistant_delta"
  | "reasoning_delta"
  | "tool_call_delta"
  | "assistant_message"
  | "iteration_end"
  | "turn_end"
  | "turn_done"
  | "error"
  | "input_rejected"
  | "interrupted"
  // A stream ended mid-answer and the engine is sending the same request again (an SDK only
  // retries a request that failed before its first chunk).
  | "retrying"
  // A sentence typed while the turn runs was taken into it. The bubble is drawn from this, so a
  // refused one leaves nothing behind.
  | "steering_queued"
  // A tool call, from proposal to result.
  | "tool_proposed"
  | "tool_started"
  | "tool_output_delta"
  | "tool_finished"
  // A spawned child's progress on its parent's socket: its session id, latest tool and count.
  | "tool_metadata"
  // What waits on the person.
  | "permission_required"
  | "tool_requested"
  | "question_requested"
  | "plan_proposed"
  // A setting another view changed; each carries the new value so every open view agrees.
  | "model_changed"
  | "thinking_changed"
  | "mode_changed"
  | "plan_changed"
  | "plugins_reloaded"
  // Compaction: started, done, or (a `/compact` between turns) ended with nothing folded.
  | "compacting"
  | "compacted"
  | "compact_failed"
  // The goal driver: an outcome said to the person, or a change made from the goal bar.
  | "goal"
  // A goal armed or disarmed in this process with no change of phase (a restart disarms all).
  | "goal_activation"
  // The revert marker set or cleared; null when cleared.
  | "revert"
  // A background command's output as it is read. Only the roster says a job ended.
  | "job_output"
  // The whole jobs roster, replacing what the client holds.
  | "jobs";

export interface WsEvent {
  type: EventType;
  data: any;
}

// -- Decisions ----------------------------------------------------------------------------------

/** An answer to an approval card. `always_task` is kept on the automation that raised it and is
 *  offered on an automation run's card only; `modify` carries the person's edited arguments, which
 *  the engine checks again as a new call. */
export type ApprovalDecision = "once" | "deny" | "always_tool" | "always_command" | "always_domain" | "always_task" | "modify";

/** An `ask_user` option: a plain answer, or one with a line under it, a Recommended tag, or a
 *  preview (any preview gives the card its side pane). */
export type QuestionOption = string | { label: string; description?: string; recommended?: boolean; preview?: string };

/** One step of an `ask_user` call that asks several things; its answer is keyed by `header`, else
 *  by `question`. */
export interface GroupedQuestion {
  question: string;
  header?: string;
  options?: QuestionOption[];
  allow_text?: boolean;
  multi?: boolean;
}

export interface TodoItem {
  content: string;
  status: "pending" | "in_progress" | "done";
}

/** A line comment made in Review or a file tab, sent with the next message. */
export interface ReviewComment {
  id: string;
  file: string;
  line: number;
  text: string;
  origin?: "review" | "file";
}

// -- What a turn spends -------------------------------------------------------------------------

/** Tokens by billing class, for one step or a run of them; the turn's footer adds them up. */
export interface StepTokens {
  input: number;
  output: number;
  cache_read: number;
  cache_write: number;
}

/** One request's tokens, on an assistant message's `usage` and on `assistant_message`. */
export interface TurnUsage extends StepTokens {
  model?: string | null;
}

/** The engine's guess at what the latest request was made of (`context_breakdown`, chars/4 per
 *  part). Any part may be missing. */
export interface ContextBreakdown {
  messages?: number;
  mcp_tools?: number;
  system_tools?: number;
  skills?: number;
  instructions?: number;
  system_prompt?: number;
  meta_context?: number;
}

/** A session's spend by model. `context` is what the latest request holds, its output included,
 *  since that is sent again next time; `prompt` is the request alone, which the breakdown is
 *  measured against. */
export interface SessionUsage {
  byModel: Record<string, TurnUsage>;
  context: number;
  prompt: number;
  breakdown?: ContextBreakdown;
}

/** GET /v1/sessions/{id}/metrics: the server's count over the session's event log. A server
 *  without the route leaves the section unshown. */
export interface SessionMetrics {
  events: number;
  wall_seconds?: number | null;
  turns?: { started: number; ended: number; tokens_avg?: number | null };
  steps?: number;
  llm?: { calls: number; retries: number; retry_rate?: number | null; errors: number; error_rate?: number | null };
  tokens?: { total: number; cache_read_share?: number | null };
  latency?: { ttft_ms_avg?: number | null; step_ms_avg?: number | null };
  tools?: { calls: number; results: number; error_rate?: number | null; timeout_or_error?: number; duration_ms_avg?: number | null };
  compactions?: number;
}

// -- Sessions -----------------------------------------------------------------------------------

export interface SessionInfo {
  session_id: string;
  title?: string;
  workspace: string;
  /** The folder on the host, for a session bound to a remote environment; null locally. A remote
   *  readout reads this, never `workspace`. */
  remote_workspace?: string | null;
  /** The folder is a plain chat's temporary one, not a project. */
  temp_workspace?: boolean;
  environment_id?: string | null;
  environment?: RemoteEnvironmentSummary | null;
  agent: string;
  model: string;
  mode: string;
  updated_at: string | null;
  messages: number;
  pinned?: boolean;
  archived?: boolean;
  /** Prompts waiting on the person, the kind of the oldest, and what it asks: "Needs you". */
  attention?: number;
  attention_kind?: string | null;
  attention_title?: string | null;
  /** An automation run's conversation: off the sidebar, unless it waits on the person. */
  automation?: { id: string; title: string } | null;
  /** In a turn, waiting on a timer it set, or neither. */
  liveness?: "working" | "sleeping" | "idle";
  /** When the timer fires (ISO), while sleeping. */
  sleeping_until?: string | null;
  /** The channels the session listens to. */
  subscriptions?: string[];
  /** A session a platform mention started: the platform ("slack") and where ("#general · T0ABCD"). */
  origin?: string;
  origin_label?: string;
  /** The work queue's facts, for sessions active in the last week: the branch (null
   *  when detached, absent outside a repository), a linked worktree, and what the session changed. */
  branch?: string | null;
  worktree?: boolean;
  work?: SessionWork;
}

export interface SessionWork {
  files: number;
  added: number;
  removed: number;
  /** How many of the files are still uncommitted; absent outside a repository. */
  open?: number;
  /** The last check a turn ran; `stale` once a file was edited after it. */
  check?: { ok: boolean; summary: string; stale: boolean };
}

// -- What a message carries ---------------------------------------------------------------------

/** The slash command a message was sent with: a built-in, a project's own (`prompt`,
 *  its text the expanded template) or a skill, and what was typed after the name. */
export interface SentCommand {
  name: string;
  args?: string;
  description?: string;
  kind?: "prompt" | "skill";
  /** Where a defined command comes from ("this project", "yours", a plugin). */
  source?: string;
}

/** A file sent with a message. One the model cannot take as is (`file`) is saved in the session's
 *  scratch and named to it by path, never dropped. */
export interface Attachment {
  kind: "image" | "text" | "pdf" | "file";
  name: string;
  mime?: string;
  /** The bytes of anything but a text file. */
  data_url?: string;
  text?: string;
  /** From the message's `_attached` row, when the bytes are not here to measure. */
  size?: number;
  lines?: number;
  /** The copy in the session's attachments folder, which the card opens. */
  path?: string;
}

/** What the design check after a write found on one page. */
export interface DesignCheck {
  summary: string;
  lines: string[];
  issues: number;
  /** The page, as the write named it. */
  path?: string;
  /** What the write changed, part by part, and which of those nobody asked for. */
  changes?: string[];
  unasked?: string[];
  thumb?: string;
}

/** A spawned child as its parent sees it while it runs, from `tool_metadata` frames keyed by the
 *  `task` call. Drawn only: `tool_finished` settles the row. */
export interface SubagentMeta {
  /** The child's session id, on the first frame, before any tool ran. */
  sessionId?: string;
  /** Its latest tool call (wire name) and that call's own summary. */
  tool?: string;
  title?: string;
  /** Tool calls started so far. */
  toolcalls?: number;
  model?: string;
  /** Spawned detached; drawn on the card. */
  background?: boolean;
  jobId?: string;
  /** Epoch seconds, from the first frame and the settling one. */
  startedAt?: number;
  endedAt?: number;
  /** Its time from first message to last answer, at settle. */
  durationMs?: number;
  /** A provider call being sent again, while it stands. */
  retry?: { attempt: number; message: string };
  /** In a tool, thinking, or writing its answer: the newest frame's word. */
  phase?: "start" | "thinking" | "writing";
  /** What it has spent so far, after each step; the result carries the final figure. */
  tokens?: number;
  /** Waiting for a child slot: `running` of the session's `limit` are taken. Its first frame ends it. */
  waiting?: { running: number; limit: number };
}

// -- Transcript items ---------------------------------------------------------------------------
// `ts` is unix seconds: the server's stamp on the message, or the client's on a live item. Sessions
// saved before the server stamped messages have none.

export type UserItem = { kind: "user"; text: string; attachments?: Attachment[]; ts?: number; command?: SentCommand };

/** A message taken into a running turn: drawn inside that turn, which it neither opened nor split.
 *  The engine's `_steer` sidecar is what a reload reads. `from: "agent"` in a subagent's session:
 *  the agent that started it said this (`message_task`). */
export type SteerItem = { kind: "steer"; text: string; ts?: number; from?: "agent" };

/** A message a connector brought in, drawn as that connector's card. */
export type ConnectorItem = { kind: "connector"; source: MessageSource };

export type AssistantItem = {
  kind: "assistant";
  text: string;
  ts?: number;
  reasoning?: string;
  /** The message also called tools, so its text leads into them and is not the answer. Set live;
   *  a reloaded turn is grouped by position. */
  withCalls?: boolean;
  /** The model that wrote it, which the answer's footer names. */
  model?: string;
  /** What its step spent, with any earlier step that left no row. */
  tokens?: StepTokens;
};

export type ToolItem = {
  kind: "tool";
  id: string;
  name: string;
  args: any;
  status: string;
  preview?: string;
  /** Results the person's privacy filters removed before the agent saw them. */
  hidden?: number;
  /** The automation's rule that allowed the call ("tool → target"). */
  standingRule?: string;
  /** What the tool said of its result (`_presentation`, replayed on reload), and of the call
   *  itself, which is what the row draws while the call runs. */
  presentation?: Presented;
  callPresentation?: Presented;
  callId?: string;
  undoable?: boolean;
  /** The call's own time on the server's clock; a gap between two stamps would count the
   *  approval it waited on. */
  durationMs?: number;
  /** What the engine put before the model for this call, the image `read_image` fetched. It comes
   *  in a user-role message, but it is this step's, not something the person sent. */
  attachments?: Attachment[];
  ts?: number;
  endTs?: number;
  queued?: boolean;
  liveOutput?: string;
  runId?: string;
  stepCount?: number;
  subagent?: SubagentMeta;
  model?: string;
  designCheck?: DesignCheck[];
  tokens?: StepTokens;
  looks?: boolean;
};

export type ApprovalItem = {
  kind: "approval";
  name: string;
  args: any;
  reason: string;
  category?: string;
  /** The tool's own account of the call, preferred over humanize.ts's per-tool switch. */
  presentation?: Presented;
  /** The target an "Allow every time" rule would pin, on an automation run's card. */
  standingTarget?: string;
  /** web_search: the provider the query would go to, named in the grant. */
  searchProvider?: string;
  /** The call would run a file the agent made or downloaded this session. Engine-written, from a
   *  fixed vocabulary, never file contents. */
  provenance?: string;
  /** What refused: the session's reach, which no press can grant, or a person's consent. */
  axis?: "boundary" | "consent";
  /** No grant below Full access clears it, and the card says so. */
  humanOnly?: boolean;
  /** The call had started when the process ended and may have taken effect; the card
   *  asks whether to run it again. */
  uncertain?: boolean;
  /** Folders outside the session the call reaches; approving adds them. */
  reach?: { path: string; writable: boolean }[];
  resolved?: ApprovalDecision;
};

/** The turn's receipt, from the calls the engine saw, never from the model's account of them. A
 *  turn that changed nothing has none. */
export type ChangesItem = {
  kind: "changes";
  created?: string[];
  overwritten?: string[];
  did?: string[];
  /** Removed files; only a command removes one, so these come from git. */
  deleted?: string[];
  /** Lines added and removed per file, from the turn-start snapshot, and how to undo the last
   *  write. `source: "shell"`: a command changed the file without naming it, read from git. */
  stats?: { path: string; added: number; removed: number; callId?: string; undoable?: boolean; source?: "shell" }[];
  /** The turn's last check as the engine read its exit; `ok` null when the exit was unreadable. */
  check?: { command: string; ok: boolean | null; summary: string; editsSince: number; timedOut: boolean };
  /** The agent's own notes (its memory folders), said apart and not counted. */
  notes?: string[];
};

export type ToolRequestItem = {
  kind: "toolreq";
  tool: string;
  reason: string;
  installable?: boolean;
  version?: string;
  summary?: string;
  source?: string;
  resolved?: "installed" | "skipped";
};

export type PlanRequestItem = {
  kind: "planreq";
  plan: string;
  /** The session's plan file (`update_plan`), when there is one. */
  path?: string;
  resolved?: "approved" | "rejected";
};

/** An `ask_user` prompt, answered in the conversation. */
export type QuestionItem = {
  kind: "question";
  question: string;
  options?: QuestionOption[];
  allow_text?: boolean;
  multi?: boolean;
  header?: string;
  questions?: GroupedQuestion[];
  resolved?: string;
};

/** What the transcript says on its own behalf: a status line, a command's output, a divider. */
export type NoticeItem = {
  kind: "notice";
  tone: "info" | "warn";
  text: string;
  retriable?: boolean;
  /** The line opening a turn the engine started: what reported back, as `<kind>:<id>`, and its
   *  facts for the marker (wakeNotice.ts). `restart`: a turn picked up after its process ended. */
  wake?: string;
  wakeFacts?: {
    kind: "task" | "shell" | "restart" | "other";
    sessionId?: string;
    title?: string;
    verb: string;
    detail?: string;
    failed?: boolean;
    /** What a background command had printed when it was stopped. */
    output?: string;
  };
  ts?: number;
  /** Belongs inside the running turn (a provider retry); flushing the turn on it split one turn
   *  into two right where the provider failed. */
  inline?: boolean;
  /** A slash command's output, drawn as its own block with its lines kept; `title` heads it. */
  command?: boolean;
  /** Drawn as the divider, its label between two rules: compaction's marker, the row its own
   *  toggle and `detail` its summary. */
  divider?: boolean;
  /** The revert marker: the same divider, where the model's view stops; `hidden` is how
   *  many messages it hides. Nothing folds. */
  boundary?: "revert";
  hidden?: number;
  /** What a compaction freed ("48.2k freed"), and the same as "from → to" when both are known. */
  freed?: string;
  span?: string;
  /** Makes the line a block: a heading and paragraphs. */
  title?: string;
  /** An MCP server's error: the server, and the full text behind a disclosure. */
  server?: string;
  detail?: string;
};

export type Item =
  | UserItem
  | SteerItem
  | ConnectorItem
  | AssistantItem
  | ToolItem
  | ApprovalItem
  | ChangesItem
  | ToolRequestItem
  | PlanRequestItem
  | QuestionItem
  | NoticeItem;
