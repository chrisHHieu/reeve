// The `task` calls of one turn as the DATA a delegation card draws (the reference design's subagent
// topology, rebuilt on Reeve's own wire).
//
// The card is a pure view of rows the transcript already holds: the `task` tool item, the
// `tool_metadata` frames folded onto it, the settle frame's
// `durationMs`/`endedAt`, and the presentation's `run_steps`. Nothing here talks to the
// server — a reloaded session renders the same card from the replayed rows alone.
//
// Kept apart from the component so the derivations are unit-testable without a DOM: what
// counts as finished, which status a node wears, which elapsed a group reports.

import type { ToolItem } from "./cards/cardModels";
import { toolState } from "./cards/cardModels";
import { agentColor, type AgentColorIndex } from "./subagents";

/** A child run's verdict, in the vocabulary the card's labels use.
 *
 *  `settled` is the gap between the child's turn ending and its result landing on the call:
 *  the settle frame is emitted first on purpose (`session_roots.py`), and a parallel batch
 *  gathers every result before recording any of them. The child has stopped; the
 *  verdict is not known yet, so the row says `Finished` and not `Completed`. */
export type DelegationOutcome = "running" | "settled" | "completed" | "failed" | "stopped";

export interface DelegationNode {
  tool: ToolItem;
  /** Stable row key: the call id, else the item id. */
  key: string;
  /** The definition name the call wrote (`explore`), and its display title (`Explore`). */
  agentId: string;
  title: string;
  /** The caller's `description` — the node's subtitle. */
  description: string;
  color: string;
  outcome: DelegationOutcome;
  background: boolean;
  /** The child's step counter: `run_steps` at settle, else the last live frame. */
  steps: number | null;
  /** The child's own token spend, once its result reported one. Null while it runs. */
  tokens: number | null;
  /** The model the child actually ran on (`deepseek:deepseek-flash`), when known. */
  model: string | null;
  /** The child's measured elapsed, ms — the settle frame's, else the call's own wall time. */
  durationMs: number | null;
  /** Epoch ms the child started at, when a frame or the row's own stamp said. What a running
   *  row's clock counts from. */
  startedAtMs: number | null;
  /** The live `↳ …` line while the child works; null once settled. */
  live: string | null;
  /** A failed call's own text; null unless the child failed. */
  failure: string | null;
  /** The child SESSION behind the call, once a frame or the result named it. */
  childId: string | null;
  /** Its work is waiting in its own copy of the repository: the row offers Apply
   *  and Discard. The server's fact (`useKeptWorktrees`), never derived from the transcript. */
  kept: boolean;
  /** In line for a child slot, with how full the session is; null once its child exists. */
  waiting: { running: number; limit: number } | null;
}

/** The nodes with `kept` set for the children the server says still hold a copy. The same
 *  array comes back when nothing is kept, so a memoized roster does not redraw. */
export function withKept(
  nodes: readonly DelegationNode[],
  kept: ReadonlySet<string>,
): readonly DelegationNode[] {
  if (kept.size === 0) return nodes;
  return nodes.map((node) =>
    node.childId !== null && kept.has(node.childId) && node.outcome !== "running"
      ? { ...node, kept: true }
      : node,
  );
}

export interface DelegationSummary {
  total: number;
  finished: number;
  running: number;
  failed: number;
  /** Interrupted / no-result children: the "finished with warnings" bucket. */
  stopped: number;
  /** Epoch ms bounds of the group's work, when any node carried timing. */
  startedAtMs: number | null;
  endedAtMs: number | null;
}

/**
 * The calls whose work is a delegated child session: every `task` spawn, and a `workflow` run
 * that went to the background — a runner child whose agents do the work, with the
 * same live frames, clock and settle a background `task` has. A workflow that ran in its own
 * call is a program, not a delegation, and stays its own row.
 *
 * A background run is known by its live frames, or — in a reopened session, which has none —
 * by the runner its result names (`run_id`, read into `runId`): a reload used to drop the run
 * from the roster (owner, 2026-10-13).
 */
export function isDelegationCall(item: ToolItem): boolean {
  return (
    item.name === "task" ||
    (item.name === "workflow" && (item.subagent?.background === true || Boolean(item.runId)))
  );
}

/** How each background child reported back, by its session id: the wake markers in the
 *  transcript (`wakeNotice.ts`). */
export type ChildReports = ReadonlyMap<string, "completed" | "failed">;

/**
 * The nodes with what their own report said. A reopened session has no settle frames, so a
 * background child read "Completed" from its spawn call, which only ever says it STARTED — a
 * child that failed afterwards looked finished. Its report is the one record of how it ended.
 * The same array comes back when no report changes anything.
 */
export function withReports(nodes: readonly DelegationNode[], reports: ChildReports): readonly DelegationNode[] {
  if (reports.size === 0) return nodes;
  let changed = false;
  const out = nodes.map((node) => {
    const said = node.childId !== null ? reports.get(node.childId) : undefined;
    if (said !== "failed" || node.outcome !== "completed") return node;
    changed = true;
    return { ...node, outcome: "failed" as const, failure: node.failure ?? "Failed after it went to the background" };
  });
  return changed ? out : nodes;
}

/** A workflow call's `meta` block (object or JSON text args), or an empty one. */
function workflowMeta(args: unknown): Record<string, unknown> {
  let parsed = args;
  if (typeof parsed === "string") {
    try {
      parsed = JSON.parse(parsed);
    } catch {
      return {};
    }
  }
  const meta = parsed && typeof parsed === "object" ? (parsed as Record<string, unknown>).meta : null;
  return meta && typeof meta === "object" ? (meta as Record<string, unknown>) : {};
}

/** A background workflow's line in the roster: its own sentence, else its name. */
function workflowLabel(tool: ToolItem): string {
  const meta = workflowMeta(tool.args);
  const description = typeof meta.description === "string" ? meta.description.trim() : "";
  const name = typeof meta.name === "string" ? meta.name.trim() : "";
  return description || (name ? `${name} workflow` : "workflow run");
}

/** `explore` → `Explore`. The agent id is a slug; the card shows it as a name. */
export function agentTitle(raw: unknown): string {
  const id = typeof raw === "string" && raw.trim() ? raw.trim() : "task";
  return id.charAt(0).toUpperCase() + id.slice(1);
}

function argString(args: Record<string, unknown>, key: string): string {
  const value = args[key];
  return typeof value === "string" ? value.trim() : "";
}

/** The description the model wrote — the subtitle. Falls back to the prompt's first line. */
export function delegationDescription(args: Record<string, unknown>): string {
  const description = argString(args, "description");
  if (description) return description;
  const prompt = argString(args, "prompt").split("\n")[0];
  return prompt.length > 90 ? prompt.slice(0, 89) + "…" : prompt;
}

/** The child's task, as the breadcrumb names it: the description, else the prompt's first
 *  line, else `<agent> run` — a task always has one of the three. */
export function delegationLabel(tool: ToolItem): string {
  if (tool.name === "workflow") return workflowLabel(tool);
  const args = (tool.args || {}) as Record<string, unknown>;
  const description = argString(args, "description");
  if (description) return description;
  const prompt = argString(args, "prompt").split("\n")[0];
  if (prompt) return prompt.length > 80 ? prompt.slice(0, 79) + "…" : prompt;
  const agent = argString(args, "subagent_type");
  return agent ? `${agent} run` : "task run";
}

/** `(attempt N)` retry lines stay readable inside the narrow node column. */
function truncate(text: string, len: number): string {
  return text.length <= len ? text : text.slice(0, len - 1) + "…";
}

/** The failure text a failed node shows: the presenter's detail, else the result's error. */
export function failureText(tool: ToolItem): string {
  const detail = tool.presentation?.detail;
  if (typeof detail === "string" && detail.trim()) return detail.trim();
  const preview = typeof tool.preview === "string" ? tool.preview : "";
  try {
    const parsed = JSON.parse(preview);
    if (parsed && typeof parsed === "object" && typeof parsed.error === "string") {
      return parsed.error;
    }
  } catch {
    // Not JSON: a replay's model-facing text is shown whole.
  }
  return preview;
}

/**
 * Whether the child's turn has ENDED while its call has not returned yet.
 *
 * The settle frame (`ended_at`) is emitted before the tool result on purpose, so a running
 * row learns the child's elapsed while it still owns the row (`session_roots.py`, P2). With
 * one foreground spawn that gap is milliseconds. In a parallel batch it is the rest of the
 * batch: no result of the chunk is recorded until every call in it finished, so a
 * two-child fan-out waits for the slower child, and the gap can be minutes. Reading the
 * call's status alone left the finished child's row saying "Running" the whole time, while
 * every fact the client held said the child had stopped (owner-hit 2026-10-18).
 */
export function childSettled(tool: ToolItem): boolean {
  return tool.status === "…" && tool.subagent?.endedAt !== undefined;
}

/**
 * Whether the CHILD is still working.
 *
 * The spawn call settling is not the child ending: a `background=true` spawn returns
 * `state="running"` immediately, so the row wears `status: "ok"` while the child keeps
 * going until its settle frame arrives with `ended_at` (the manager's `_watch`).
 * Reading the call's status alone showed background children as "Completed · 366ms ·
 * 0 steps" for an 8-second job (owner-hit 2026-09-20).
 */
/** The call is in line for a child slot (the session runs its `subagent_max_parallel`): no
 *  child exists yet, so it has no model, no live line of its own and nothing to open. A queued
 *  call has already returned and is a background row until its child starts. */
export function delegationWaiting(tool: ToolItem): { running: number; limit: number } | null {
  const meta = tool.subagent;
  return delegationRunning(tool) && meta?.waiting && !meta.sessionId ? meta.waiting : null;
}

export function delegationRunning(tool: ToolItem): boolean {
  if (tool.status === "…") return !childSettled(tool);
  return tool.subagent?.background === true && tool.subagent.endedAt === undefined;
}

/**
 * The node's live line, in the order the facts arrive:
 *
 *   · while a provider call is going again — `↳ Retrying (attempt N) · <message>`;
 *   · between its tools — `↳ Thinking` / `↳ Writing`, the newest phase frame: a
 *     child spends most of its time there, and the row used to keep naming a tool call that
 *     had ended a minute ago;
 *   · while a tool runs — `↳ <tool> <title>`, the latest frame;
 *   · while the child works with only a count — `↳ N toolcalls` / `↳ N steps`.
 */
export function delegationLiveLine(tool: ToolItem): string | null {
  const meta = tool.subagent;
  // Its turn ended while the call still waits for the rest of the batch: say that, rather
  // than naming a tool call that finished minutes ago.
  if (childSettled(tool)) return "↳ Finished its turn — waiting for the result";
  if (!delegationRunning(tool)) return null;
  const waiting = delegationWaiting(tool);
  if (waiting) return `↳ Starts when one of the ${waiting.running} running finishes`;
  if (meta?.retry)
    return `↳ Retrying (attempt ${meta.retry.attempt}) · ${truncate(meta.retry.message, 60)}`;
  if (meta?.phase === "thinking") return "↳ Thinking";
  if (meta?.phase === "writing") return "↳ Writing";
  // A background workflow's phase: the phase's own title is the line.
  if (meta?.tool === "workflow") return meta.title ? `↳ ${meta.title}` : "↳ Working";
  if (meta?.tool) return meta.title ? `↳ ${meta.tool} ${meta.title}` : `↳ ${meta.tool}`;
  const steps = tool.stepCount ?? meta?.toolcalls;
  if (typeof steps === "number") return `↳ ${steps} step${steps === 1 ? "" : "s"}`;
  return null;
}

/** The child's measured elapsed, ms: the settle frame first, the call's own server clock
 *  second. NEVER the receipt stamps (`ts`/`endTs`): those are the browser's clock around the
 *  wait — they charge the child for an approval it sat on, and their float seconds render as
 *  `254.99987602233887ms`. */
export function nodeDurationMs(tool: ToolItem): number | null {
  const meta = tool.subagent;
  if (typeof meta?.durationMs === "number" && meta.durationMs > 0) return meta.durationMs;
  if (typeof meta?.startedAt === "number" && typeof meta?.endedAt === "number") {
    const ms = Math.round((meta.endedAt - meta.startedAt) * 1000);
    if (ms > 0) return ms;
  }
  if (typeof tool.durationMs === "number" && tool.durationMs > 0) return tool.durationMs;
  return null;
}

/**
 * The child's own token spend, when its result reported it.
 *
 * A finished `task` returns `{task_id, status, tokens, steps, report, …}`
 * (`harness/tools/task.py` `_foreground_result`), so the number is a FACT for a settled
 * child and absent for a running one — a background spawn returns only
 * `{task_id, status: "running", report}`. That asymmetry is the point: a roster that
 * invented a live figure, or showed `0`, would be lying about the one number a reader uses
 * to decide whether a child is worth its cost. While a child runs, the row shows its step
 * count and its live line instead.
 *
 * Parsed the way `runRef.ts` parses a run id: the object first (whole on replay), then the
 * raw text (a result preview is the first 300 characters of the JSON, and `tokens` sits
 * before the report, so it survives there).
 *
 * A child that is STILL RUNNING has no result, and has the figure its frames told the row
 * after each of its steps — the spend so far, from the same fold. The result wins
 * once there is one.
 */
export function nodeTokens(tool: ToolItem): number | null {
  const settled = resultTokens(tool);
  if (settled !== null) return settled;
  const told = tool.subagent?.tokens;
  return typeof told === "number" && told > 0 ? Math.round(told) : null;
}

function resultTokens(tool: ToolItem): number | null {
  const preview = typeof tool.preview === "string" ? tool.preview : "";
  try {
    const parsed = JSON.parse(preview) as unknown;
    if (parsed && typeof parsed === "object") {
      const tokens = (parsed as { tokens?: unknown }).tokens;
      if (typeof tokens === "number" && Number.isFinite(tokens) && tokens >= 0) {
        return Math.round(tokens);
      }
    }
  } catch {
    // Not JSON: a replayed session's model-facing text, or a preview cut mid-object.
  }
  const match = /"tokens"\s*:\s*(\d+)/.exec(preview);
  return match ? Number(match[1]) : null;
}

function nodeStartMs(tool: ToolItem): number | null {
  const meta = tool.subagent;
  if (typeof meta?.startedAt === "number") return meta.startedAt * 1000;
  if (typeof tool.ts === "number" && tool.ts > 0) return tool.ts * 1000;
  return null;
}

function nodeEndMs(tool: ToolItem): number | null {
  const meta = tool.subagent;
  if (typeof meta?.endedAt === "number") return meta.endedAt * 1000;
  if (typeof tool.endTs === "number" && tool.endTs > 0) return tool.endTs * 1000;
  const start = nodeStartMs(tool);
  const duration = nodeDurationMs(tool);
  return start !== null && duration !== null ? start + duration : null;
}

/** The child's step counter: `run_steps` (the presentation) wins over the live frames. */
export function nodeSteps(tool: ToolItem): number | null {
  const steps = tool.stepCount ?? tool.subagent?.toolcalls;
  return typeof steps === "number" ? steps : null;
}

/**
 * The model the child runs on, as the node's chip shows it.
 *
 * The live frame's `model` is the RESOLVED route the manager wrote onto the child row
 * (`deepseek:deepseek-flash` after a provider/model override), so it wins. A replayed
 * session that never stored the frames still has the call's own override arguments —
 * the requested route — and that is the honest fallback. A provider-only override
 * (the model id comes from the agent's definition, which the client cannot see) is
 * omitted rather than half-shown.
 */
export function nodeModel(tool: ToolItem): string | null {
  const live = tool.subagent?.model;
  if (typeof live === "string" && live.trim()) return live.trim();
  const args = (tool.args || {}) as Record<string, unknown>;
  const provider = argString(args, "provider");
  const model = argString(args, "model");
  if (model.includes(":")) return model;
  if (provider && model) return `${provider}:${model}`;
  return model || null;
}

export function delegationOutcome(tool: ToolItem): DelegationOutcome {
  // Before `delegationRunning`: a settled child's call is still open, and the verdict rides
  // the result that has not come back yet.
  if (childSettled(tool)) return "settled";
  if (delegationRunning(tool)) return "running";
  const state = toolState(tool);
  if (state === "ok") return "completed";
  if (state === "stopped") return "stopped";
  return "failed";
}

/** A node's status word: a call still in line for a slot is Waiting, not Running. */
export function nodeStatusLabel(node: Pick<DelegationNode, "outcome" | "waiting">): string {
  return node.waiting ? "Waiting" : delegationStatusLabel(node.outcome);
}

export function delegationStatusLabel(outcome: DelegationOutcome): string {
  switch (outcome) {
    case "running":
      return "Running";
    case "settled":
      // Its turn ended; whether it succeeded arrives with the result.
      return "Finished";
    case "completed":
      return "Completed";
    case "stopped":
      return "Stopped";
    default:
      return "Failed";
  }
}

/** Steps as the node's own line: PI's `N steps`, not `N toolcalls`. */
export function stepsLabel(steps: number): string {
  return `${steps} step${steps === 1 ? "" : "s"}`;
}

export function delegationNodes(
  tools: readonly ToolItem[],
  colors: AgentColorIndex,
): DelegationNode[] {
  return tools.map((tool) => {
    const args = (tool.args || {}) as Record<string, unknown>;
    const workflow = tool.name === "workflow";
    const agentId = workflow ? "workflow" : argString(args, "subagent_type") || "task";
    const outcome = delegationOutcome(tool);
    const description = workflow ? workflowLabel(tool) : delegationDescription(args);
    return {
      tool,
      key: tool.callId ?? tool.id,
      // The lookup is case-insensitive: the definition's name and the `subagent_type`
      // written into the call can differ in case (TaskRow's rule, kept).
      agentId,
      title: agentTitle(agentId),
      description,
      color: agentColor(agentId, colors[agentId.toLowerCase()]),
      outcome,
      // A workflow that names its runner went to the background (its `background` defaults on).
      background: tool.subagent?.background === true || args.background === true || (workflow && Boolean(tool.runId)),
      steps: nodeSteps(tool),
      tokens: nodeTokens(tool),
      model: nodeModel(tool),
      durationMs: nodeDurationMs(tool),
      startedAtMs: nodeStartMs(tool),
      live: delegationLiveLine(tool),
      failure: outcome === "failed" ? failureText(tool) : null,
      childId: tool.subagent?.sessionId ?? tool.runId ?? null,
      kept: false,
      waiting: delegationWaiting(tool),
    };
  });
}

/**
 * The roster's reading order: what is running first, in the order it was started (a row must
 * not move while it is being watched), then what settled, the most recent first. The
 * transcript's own order put the thirteenth child's running row under twelve finished ones.
 */
export function rosterOrder(nodes: readonly DelegationNode[]): DelegationNode[] {
  const running = nodes.filter((node) => node.outcome === "running");
  const settled = nodes.filter((node) => node.outcome !== "running").reverse();
  return [...running, ...settled];
}

/** `2 running · 11 finished · 1 with issues` — every part that has something to say. */
export function rosterHeadline(summary: DelegationSummary): string {
  const issues = summary.failed + summary.stopped;
  return [
    summary.running > 0 ? `${summary.running} running` : "",
    summary.finished > 0 ? `${summary.finished} finished` : "",
    issues > 0 ? `${issues} with issues` : "",
  ]
    .filter(Boolean)
    .join(" · ");
}

export function delegationSummary(nodes: readonly DelegationNode[]): DelegationSummary {
  let startedAtMs: number | null = null;
  let endedAtMs: number | null = null;
  for (const node of nodes) {
    const start = nodeStartMs(node.tool);
    if (start !== null && (startedAtMs === null || start < startedAtMs)) startedAtMs = start;
    const end = nodeEndMs(node.tool);
    if (end !== null && (endedAtMs === null || end > endedAtMs)) endedAtMs = end;
  }
  return {
    total: nodes.length,
    finished: nodes.filter((n) => n.outcome !== "running").length,
    running: nodes.filter((n) => n.outcome === "running").length,
    failed: nodes.filter((n) => n.outcome === "failed").length,
    stopped: nodes.filter((n) => n.outcome === "stopped").length,
    startedAtMs,
    endedAtMs,
  };
}

/**
 * The group's elapsed, ms — the wall clock that actually held the children: earliest start
 * to latest end (PI's `min start / max end`). While any child still runs the end is `nowMs`,
 * so the metric ticks; without timing on either end it is null and the metric is omitted
 * rather than invented.
 */
export function delegationElapsedMs(
  summary: DelegationSummary,
  nowMs: number,
): number | null {
  if (summary.startedAtMs === null) return null;
  const end = summary.running > 0 ? nowMs : summary.endedAtMs;
  if (end === null || end <= summary.startedAtMs) return null;
  // Rounded: meta start/end are epoch seconds, so the difference carries float noise and a
  // sub-second group would otherwise read `254.99987602233887ms`.
  return Math.round(end - summary.startedAtMs);
}

/** The card's header word, in PI's vocabulary (issues outrank warnings, as there). */
export function delegationHeaderLabel(summary: DelegationSummary): string {
  const noun = summary.total === 1 ? "Subagent" : "Subagents";
  if (summary.running > 0) return `${noun} working`;
  if (summary.failed > 0) return `${noun} finished with issues`;
  if (summary.stopped > 0) return `${noun} finished with warnings`;
  return `${noun} completed`;
}
