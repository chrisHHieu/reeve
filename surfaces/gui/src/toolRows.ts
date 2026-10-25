// Pairing a finished tool call to the row that opened it.
//
// Extracted from App.tsx so it can be unit-tested without standing up the whole app
// (same reason as `itemsFromMessages`). The rule it encodes is not obvious: the engine
// dispatches read-only calls CONCURRENTLY (`asyncio.gather`, harness/engine.py), so
// several calls of the SAME NAME are pending at once and "the newest pending one with
// this name" hands results to the wrong rows.

import type { Attachment, DesignCheck, Item, SubagentMeta } from "./types";
import type { Presented } from "./humanize";

// One finished tool, as an object rather than a ninth positional argument. The list had
// reached eight, and every caller that forgot the newest one silently lost information —
// which is exactly how `presentation` reached App.tsx from the engine and then went
// nowhere for four items' worth of work.
export interface ToolOutcome {
  name: string;
  status: string;
  preview?: string;
  hidden?: number;
  standingRule?: string;
  presentation?: Presented;
  durationMs?: number;
  callId?: string;
  undoable?: boolean;
  endTs?: number;
  /** Model-visible media the call produced (`read_image`, an MCP screenshot). */
  attachments?: Attachment[];
  /** A delegated child behind this call: the row can offer its session (`↗ Open`). */
  runId?: string;
  stepCount?: number;
  /** What Reeve measured on a design page the call wrote. It was passed in a spread
   *  and never copied onto the row, so a live write showed no check line until a reload read it
   *  from the message (owner report 2026-10-20). */
  designCheck?: DesignCheck[];
}

/**
 * Append a chunk of a running tool's own output (`tool_output_delta`).
 *
 * Matched by call id where both ends have one, the same rule `updateLastTool` uses; with no id
 * on the wire the newest still-running call is the only candidate there is.
 *
 * A chunk for a call that already settled is dropped rather than merged: the result card
 * replaced the live text, so appending to it would show output the tool has already
 * summarised. Returning the SAME array in that case (and when nothing matched) is deliberate —
 * React bails out of the render, which matters when a fast command's chunks arrive after it
 * finished.
 */
export function appendToolOutput(items: Item[], callId: string | undefined, text: string): Item[] {
  if (!text) return items;
  for (let i = items.length - 1; i >= 0; i--) {
    const it = items[i];
    if (it.kind !== "tool" || it.status !== "…") continue;
    const hit = callId !== undefined && it.callId !== undefined ? it.callId === callId : true;
    if (!hit) continue;
    const copy = [...items];
    // Output is proof the call runs, so a row that missed its `tool_started` stops waiting.
    copy[i] = { ...it, queued: undefined, liveOutput: (it.liveOutput ?? "") + text };
    return copy;
  }
  return items;
}

/**
 * Apply `tool_started`: the engine dispatched the call. A batch's calls are all proposed
 * (screened, approved) before the first one runs, and calls that write run one after
 * another, so a proposed row is `queued` until this frame names it. Its clock restarts here,
 * so "running for 3s" means the tool's own time, not the time it spent next in line.
 *
 * Matched by call id only: a frame from a server that sends none cannot say which row
 * started, and guessing would put the wrong row in motion. Returns the same array when
 * nothing matched, so React bails out.
 */
export function markToolStarted(items: Item[], callId: string | undefined, ts: number): Item[] {
  if (!callId) return items;
  for (let i = items.length - 1; i >= 0; i--) {
    const it = items[i];
    if (it.kind !== "tool" || it.callId !== callId) continue;
    if (it.status !== "…" || !it.queued) return items;
    const copy = [...items];
    copy[i] = { ...it, queued: undefined, ts };
    return copy;
  }
  return items;
}

export function updateLastTool(items: Item[], done: ToolOutcome): Item[] {
  const copy = [...items];
  for (let i = copy.length - 1; i >= 0; i--) {
    const it = copy[i];
    if (it.kind !== "tool" || it.status !== "…") continue;
    // The id when both ends have one; the name only as the fallback for servers older
    // than the call_id on `tool_proposed`.
    const hit = done.callId && it.callId ? it.callId === done.callId : it.name === done.name;
    if (hit) {
      copy[i] = {
        ...it,
        queued: undefined,
        status: done.status,
        preview: done.preview,
        ...(done.hidden ? { hidden: done.hidden } : {}),
        ...(done.standingRule ? { standingRule: done.standingRule } : {}),
        ...(done.presentation ? { presentation: done.presentation } : {}),
        ...(done.callId ? { callId: done.callId } : {}),
        ...(done.undoable ? { undoable: true } : {}),
        // Tested against `undefined` rather than truthiness, unlike the flags above: a
        // duration of 0 is a real measurement, and this file's older habit of dropping
        // falsy values is how `hidden: 0` once became indistinguishable from "no field".
        ...(typeof done.durationMs === "number" ? { durationMs: done.durationMs } : {}),
        ...(done.endTs ? { endTs: done.endTs } : {}),
        ...(done.attachments?.length ? { attachments: done.attachments } : {}),
        ...(done.runId ? { runId: done.runId } : {}),
        ...(done.stepCount !== undefined ? { stepCount: done.stepCount } : {}),
        ...(done.designCheck?.length ? { designCheck: done.designCheck } : {}),
      };
      break;
    }
  }
  return copy;
}

/**
 * Apply one of the parent's `tool_metadata` frames (step C) to the running task
 * row that spawned the child.
 *
 * The manager sends them while a child works: the first frame carries the child session id
 * with `toolcalls: 0` plus the part metadata (`model`/`background`/`jobId`, P2), later
 * ones carry the child's latest tool and its summary, a `retry` object while a provider
 * call goes again, and a settle frame with the child's elapsed. They DECORATE a row and
 * never settle it — `tool_finished` is the only settle path, and a metadata frame that
 * arrived after the result must not reopen work that is already done.
 *
 * Matched by call id, the same rule `updateLastTool` uses; the newest running row is the
 * fallback for a server that sent no id (the `task` tool reads its own id from the
 * engine's dispatch capture, so today both ends always have one).
 */
export function updateToolMetadata(
  items: Item[],
  callId: string | undefined,
  metadata: Record<string, unknown> | undefined,
): Item[] {
  if (!metadata) return items;
  const next: Partial<SubagentMeta> = {};
  if (typeof metadata.session_id === "string" && metadata.session_id)
    next.sessionId = metadata.session_id;
  if (typeof metadata.tool === "string" && metadata.tool) next.tool = metadata.tool;
  if (typeof metadata.title === "string" && metadata.title) next.title = metadata.title;
  if (typeof metadata.toolcalls === "number" && Number.isFinite(metadata.toolcalls))
    next.toolcalls = metadata.toolcalls;
  if (typeof metadata.model === "string" && metadata.model) next.model = metadata.model;
  if (typeof metadata.background === "boolean") next.background = metadata.background;
  if (typeof metadata.job_id === "string" && metadata.job_id) next.jobId = metadata.job_id;
  if (typeof metadata.started_at === "number" && Number.isFinite(metadata.started_at))
    next.startedAt = metadata.started_at;
  if (typeof metadata.ended_at === "number" && Number.isFinite(metadata.ended_at))
    next.endedAt = metadata.ended_at;
  if (typeof metadata.duration_ms === "number" && Number.isFinite(metadata.duration_ms))
    next.durationMs = metadata.duration_ms;
  if (metadata.phase === "start" || metadata.phase === "thinking" || metadata.phase === "writing")
    next.phase = metadata.phase;
  if (typeof metadata.tokens === "number" && Number.isFinite(metadata.tokens) && metadata.tokens >= 0)
    next.tokens = metadata.tokens;
  const waiting = metadata.waiting as { running?: unknown; limit?: unknown } | null | undefined;
  if (waiting && typeof waiting === "object") {
    const running = Number(waiting.running);
    const limit = Number(waiting.limit);
    if (Number.isFinite(running) && Number.isFinite(limit) && limit > 0) next.waiting = { running, limit };
  }
  // `retry` is a three-state field: absent (leave alone), an object (the child is going
  // again), or null (the same request produced something — drop the line). The explicit
  // clear is what stops one failed attempt's message standing for the rest of the run.
  let clearRetry = false;
  if ("retry" in metadata) {
    const retry = metadata.retry as { attempt?: unknown; message?: unknown } | null;
    if (retry && typeof retry === "object") {
      const attempt = Number(retry.attempt);
      next.retry = {
        attempt: Number.isFinite(attempt) ? attempt : 0,
        message: typeof retry.message === "string" ? retry.message : "",
      };
    } else if (retry === null) {
      clearRetry = true;
    }
  }
  if (Object.keys(next).length === 0 && !clearRetry) return items;
  const copy = [...items];
  for (let i = copy.length - 1; i >= 0; i--) {
    const it = copy[i];
    // A background child outlives the call that spawned it: the row SETTLES the moment
    // the spawn returns (`state="running"`), while the child keeps working and the
    // manager keeps streaming its frames. Dropping those frames froze the card at spawn
    // time — "Completed · 366ms · 0 steps" for a child that was still on an 8s sleep
    // (owner-hit 2026-09-20). Foreground rows keep the old rule: settled means settled.
    const backgroundChild = it.kind === "tool" && it.subagent?.background === true;
    if (it.kind !== "tool" || (it.status !== "…" && !backgroundChild)) continue;
    const hit = callId !== undefined && it.callId !== undefined ? it.callId === callId : true;
    if (!hit) continue;
    const merged: SubagentMeta = { ...(it.subagent ?? {}), ...next };
    if (clearRetry) delete merged.retry;
    copy[i] = { ...it, subagent: merged };
    return copy;
  }
  return items;
}
