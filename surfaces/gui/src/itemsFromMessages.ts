// A session's stored messages (GET /v1/sessions/{id}/messages) as the transcript's items, so a
// reopened session draws what the live one drew. Kept apart from App so it is tested on its own.
//
// A message a connector brought in carries a `source` sidecar and becomes that connector's
// card, whichever connector it is: the registry draws it, nothing here knows Slack.

import { isConnectorSource, type ConversationMessage } from "./api";
import type { Presented } from "./cards/types";
import { compactionNotice } from "./compactionNotice";
import { runRefOf } from "./runRef";
import { isReplayHiddenTool } from "./toolMeta";
import { attachedBy } from "./turns";
import type { Attachment, ChangesItem, DesignCheck, Item, NoticeItem, SentCommand, StepTokens, ToolItem, UserItem } from "./types";
import { addTokens, stepTokens } from "./usage";
import { fromParentAgent, jobStoppedNotice, restartNotice, wakeNotice } from "./wakeNotice";

/** The revert marker as the messages route returns it: the index where the model's
 *  view stops, and the transcript's length when it was set. */
export interface RevertMarker {
  boundary: number;
  staged_at: number;
}

/** The divider for a revert marker. The messages it hides from the model stay on screen. */
export function revertBoundaryItem(revert: RevertMarker): NoticeItem {
  const hidden = Math.max(0, (revert.staged_at ?? 0) - (revert.boundary ?? 0));
  const text = hidden === 1 ? "Reverted — the next message is hidden from the model" : `Reverted — the next ${hidden} messages are hidden from the model`;
  return { kind: "notice", tone: "info", boundary: "revert", hidden, text };
}

const nonEmpty = <T>(list: T[] | undefined): list is T[] => Array.isArray(list) && list.length > 0;

/** A turn's receipt from the engine's `changes` payload: the one reader for the live turn-end frame
 *  and for the `changes` notice a reload finds. Null when it names nothing. */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function changesItem(c: any): ChangesItem | null {
  if (!c || ![c.created, c.overwritten, c.did, c.deleted].some(nonEmpty)) return null;
  const item: ChangesItem = { kind: "changes" };
  for (const key of ["created", "overwritten", "did", "deleted"] as const) if (nonEmpty(c[key])) item[key] = c[key] as string[];
  if (nonEmpty(c.notes)) item.notes = c.notes.filter((p: unknown) => typeof p === "string");
  const check = c.check;
  if (check && typeof check.command === "string") {
    item.check = {
      command: check.command,
      ok: typeof check.ok === "boolean" ? check.ok : null,
      summary: typeof check.summary === "string" ? check.summary : "",
      editsSince: Number(check.edits_since) || 0,
      timedOut: !!check.timed_out,
    };
  }
  if (nonEmpty(c.stats)) {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    item.stats = c.stats.map((s: any) => ({
      path: s.path,
      added: s.added,
      removed: s.removed,
      ...(s.call_id ? { callId: s.call_id } : {}),
      ...(s.undoable ? { undoable: true } : {}),
      ...(s.source === "shell" ? { source: "shell" as const } : {}),
    }));
  }
  return item;
}

/** What the transcript knows of one call from the messages after it: its result and the sidecars
 *  the engine put on it. */
interface CallFacts {
  preview?: string;
  /** The call did not do what was asked (`_failed`); older sessions carry no mark. */
  failed?: boolean;
  /** It only looked (`_looks`), so it folds into "Explored". */
  looks?: boolean;
  /** The design check after a write (`_design_check`). */
  designCheck?: DesignCheck[];
  /** Results the person's privacy filters hid before the agent saw them (`_display`). */
  hidden?: number;
  /** The tool's own account of its result, card and all (`_presentation`). Replayed, not worked
   *  out again: a reopened session drew a plain row where the live one drew a diff. */
  presentation?: Presented;
  /** When the result landed; with the call's own stamp, a reload still has the call's span. */
  endTs?: number;
  /** The call's time on the engine's clock (`timing`), as the live frame's `duration_ms`. */
  durationMs?: number;
  /** What the engine put before the model for this call (the image `read_image` fetched). It comes
   *  in a user-role message, and drawn by role it showed the agent's screenshot as the person's. */
  attachments?: Attachment[];
}

function callFacts(messages: ConversationMessage[]): Record<string, CallFacts> {
  const facts: Record<string, CallFacts> = {};
  const of = (id: string) => (facts[id] ??= {});
  for (const m of messages) {
    if (m.role === "tool" && m.tool_call_id) {
      const call = of(m.tool_call_id);
      call.preview = typeof m.content === "string" ? m.content : JSON.stringify(m.content);
      if (m._failed) call.failed = true;
      if (m._looks) call.looks = true;
      const checks = (m as { _design_check?: unknown })._design_check;
      if (nonEmpty(checks as unknown[])) call.designCheck = checks as DesignCheck[];
      const hidden = Number(m._display?.hidden_by_filters || 0);
      if (hidden > 0) call.hidden = hidden;
      if (m._presentation) call.presentation = m._presentation;
      if (typeof m.ts === "number") call.endTs = m.ts;
      const timing = (m as { timing?: { started?: unknown; ended?: unknown } }).timing;
      if (timing && typeof timing.started === "number" && typeof timing.ended === "number" && timing.ended >= timing.started)
        call.durationMs = Math.round((timing.ended - timing.started) * 1000);
      continue;
    }
    const owner = attachedBy(m);
    const found = owner ? userItemFromContent(m.content).attachments : undefined;
    if (owner && nonEmpty(found)) of(owner).attachments = [...(of(owner).attachments ?? []), ...found];
  }
  return facts;
}

const parseArgs = (text: unknown): any => {
  try {
    return JSON.parse((text as string) || "{}");
  } catch {
    return {};
  }
};

/** The tool a call ran, and its arguments. A call through the discovery bridge is stored as the
 *  model wrote it, `tool_call{name, arguments}`, with the real tool run inside: that tool is the
 *  row, as it was live, not "Used tool_call" with the call as JSON (GUI tools review, B3). */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function calledTool(tc: any): { name: string; args: any } {
  const name = tc.function?.name;
  const args = parseArgs(tc.function?.arguments);
  if (name !== "tool_call" || typeof args?.name !== "string" || !args.name) return { name, args };
  const inner = args.arguments;
  return { name: args.name, args: typeof inner === "string" ? parseArgs(inner) : inner && typeof inner === "object" ? inner : {} };
}

/** A stored notice as its line, or null for the ones the transcript does not draw. */
function noticeItem(m: ConversationMessage): Item | null {
  switch (m.kind) {
    // Older sessions carry `tools_changed`, no longer written; it fell to the error line and
    // replayed an MCP connect as an error with Retry. An old transcript reads like a new one.
    case "tools_changed":
    // A change of model or thinking level is not a line of the chat (owner, 2026-10-13): the
    // composer and the answer's footer say which model answered.
    case "model_switch":
    case "thinking_switch":
      return null;
    case "compacted":
      // Pruning aged tool output is bookkeeping, quiet here (owner, 2026-09-23); the session log
      // keeps it. A fold the model made is a step of its turn, told by its `fold_context` row, and
      // a divider there cut the turn in two (owner, 2026-10-05).
      if (m.reason === "pruned" || m.reason === "folded") return null;
      // The divider, with the summary that now stands for what was folded behind it.
      return compactionNotice(m);
    case "changes":
      return changesItem(m.changes);
    // A turn picked up after the process ended, and a background command that ended with it:
    // quiet lines, never an error with Retry.
    case "restart":
      return restartNotice(m as unknown as Record<string, unknown>);
    case "job_stopped":
      return jobStoppedNotice(m as unknown as Record<string, unknown>);
    // Said as what happened and not as a fault: "Interrupted." was read as a crash (owner,
    // 2026-10-26). Not "Stopped by you": a parent agent can stop its own subagent.
    case "interrupted":
      return { kind: "notice", tone: "warn", text: "Stopped." };
    // Not retriable: running the turn again cannot start a server.
    case "mcp_error":
      return mcpNoticeItem(m);
    // A folder that was let in already has memory or a board. Letting it in happens inside a turn,
    // so the line goes inside it; on its own it split one reloaded turn into two (owner, 2026-09-28).
    case "project_presence":
      return { kind: "notice", tone: "info", text: m.text || "", inline: true };
    // The goal driver telling the person how it went: a round limit, a pause. Retry is no fix.
    case "goal":
      return { kind: "notice", tone: "info", text: m.text || "Goal updated" };
    // An error; the transcript offers Retry when it is the last line.
    default:
      return { kind: "notice", tone: "warn", text: "Error: " + (m.text || "unknown"), retriable: true };
  }
}

export function itemsFromMessages(messages: ConversationMessage[], revert?: RevertMarker | null): Item[] {
  const all = messages || [];
  const items: Item[] = [];
  const facts = callFacts(all);

  // What a step spent rides the row it made, its answer or else its first call. A step that made
  // no row (a hidden call) passes its spend to the next row, or at the turn's end to the turn's
  // last row, so the footer counts every step, as the live path does.
  let unclaimed: StepTokens | undefined;
  const settle = () => {
    for (let i = items.length - 1; unclaimed && i >= 0; i--) {
      const item = items[i];
      if (item.kind === "user") break;
      if (item.kind !== "assistant" && item.kind !== "tool") continue;
      item.tokens = addTokens(item.tokens, unclaimed);
      break;
    }
    unclaimed = undefined;
  };
  const stamp = (m: ConversationMessage) => (typeof m.ts === "number" ? { ts: m.ts } : {});

  all.forEach((m, index) => {
    // The revert divider goes before the first hidden message: what is above it the model sees.
    if (revert && index === revert.boundary) items.push(revertBoundaryItem(revert));
    // What the engine attached for a call went to that call's row already.
    if (attachedBy(m)) return;

    if (m.role === "user") {
      if (!m._steer) settle();
      // The engine's own message to the model, such as a finished background child reporting.
      // The person never wrote it, so it is never their bubble: one quiet line says
      // what reported back. Taken in while a turn ran, it is a note inside that turn.
      const wake = wakeNotice(m.source, typeof m.ts === "number" ? m.ts : undefined);
      if (wake) return void items.push(m._steer ? { ...wake, inline: true } : wake);
      // The model reads the framed content; the card is drawn from the sidecar.
      if (isConnectorSource(m.source)) return void items.push({ kind: "connector", source: m.source });
      // Taken into the running turn: a note inside it. As a bubble it split the turn in two
      // where the message was picked up.
      if (m._steer) {
        const said = typeof m._display === "string" && m._display ? m._display : userItemFromContent(m.content).text;
        return void items.push({ kind: "steer", text: said, ...stamp(m), ...(fromParentAgent(m.source) ? { from: "agent" as const } : {}) });
      }
      const user = userItemFromContent(m.content, (m as { _attached?: unknown })._attached);
      // `_display` is the line the person typed (`/skill …`); `content` is its framing for the model.
      if (typeof m._display === "string" && m._display) user.text = m._display;
      // The slash command it went with: the bubble draws the command, not its template.
      const command = (m as { _command?: unknown })._command as SentCommand | undefined;
      if (command && typeof command === "object" && typeof command.name === "string" && command.name) user.command = command;
      if (typeof m.ts === "number") user.ts = m.ts;
      if (user.text || user.attachments?.length) items.push(user);
      return;
    }

    if (m.role === "assistant") {
      const model = typeof m.usage?.model === "string" && m.usage.model ? m.usage.model : undefined;
      let spent = addTokens(unclaimed, stepTokens(m.usage));
      unclaimed = undefined;
      if (m.content || m.reasoning) {
        items.push({ kind: "assistant", text: m.content || "", ...(spent ? { tokens: spent } : {}), ...stamp(m), ...(m.reasoning ? { reasoning: m.reasoning } : {}), ...(model ? { model } : {}) });
        spent = undefined;
      }
      for (const tc of m.tool_calls || []) {
        const { name, args } = calledTool(tc);
        // The plan dock and the plan file show these, so they get no row; the messages keep them
        // and both still work. Asks are not among them (toolMeta): a reload has no card for one.
        if (isReplayHiddenTool(name)) continue;
        const call = facts[tc.id] ?? {};
        // A delegated call's run, from its presentation or (older sessions) its result text: what
        // opens the row and what its "· N steps" counts.
        const run = runRefOf({ presentation: call.presentation, preview: call.preview, name });
        const row: ToolItem = {
          kind: "tool",
          id: tc.id,
          // The id undo and Review name an edit by; without it a reopened session undid nothing.
          callId: tc.id,
          name,
          args,
          status: call.failed ? "error" : "ok",
          preview: call.preview,
          ...(call.presentation ? { presentation: call.presentation } : {}),
          ...(run ? { runId: run.runId } : {}),
          ...(run?.steps !== undefined ? { stepCount: run.steps } : {}),
          ...(call.hidden ? { hidden: call.hidden } : {}),
          ...(nonEmpty(call.attachments) ? { attachments: call.attachments } : {}),
          ...(call.designCheck ? { designCheck: call.designCheck } : {}),
          ...(call.looks ? { looks: true } : {}),
          // From the first stamp to the last, the turn's head says how long it worked.
          ...stamp(m),
          ...(call.endTs !== undefined ? { endTs: call.endTs } : {}),
          ...(call.durationMs !== undefined ? { durationMs: call.durationMs } : {}),
          ...(model ? { model } : {}),
          ...(spent ? { tokens: spent } : {}),
        };
        items.push(row);
        spent = undefined;
      }
      unclaimed = spent;
      return;
    }

    if (m.role === "notice") {
      const line = noticeItem(m);
      if (line) items.push(line);
    }
    // A system message is not drawn; a tool result went to its call's row.
  });
  settle();
  return items;
}

/** A server that would not start, as one quiet line with the error behind a disclosure and a way to
 *  Connectors. A notice stored before the `server` field existed gives its name in its text. */
function mcpNoticeItem(m: ConversationMessage): NoticeItem {
  const text = typeof m.text === "string" ? m.text : "";
  const server = (m.server && String(m.server)) || (text.match(/MCP server [“"]([^”"]+)[”"]/) || [])[1];
  if (!server) return { kind: "notice", tone: "warn", text: text || "An MCP server failed to start" };
  // An old notice ended with a pointer to Settings, which the button now is.
  const detail = text.replace(/\s*—\s*see Settings ▸ Connectors\s*$/u, "");
  return { kind: "notice", tone: "warn", text: `MCP server “${server}” didn’t start — its tools are unavailable here`, server, detail: detail || undefined };
}

/** How the server opens the part that stands for an attached file (`llm/attachments.py`,
 *  ATTACHED_TEXT_PREFIX): a text file inline, its head and path, or a saved file's handle. */
const ATTACHED_PREFIX = "[Attached file: ";

/** One row of a message's `_attached` sidecar. */
interface AttachedRow {
  kind?: string;
  name?: string;
  mime?: string;
  size?: number;
  lines?: number;
  path?: string;
}

const ATTACHMENT_KINDS = ["image", "pdf", "text", "file"] as const;

/** A person's message as drawn: their words, and what they attached.
 *
 *  The content is what the model read, so a text file is a block of it and a picture has no name.
 *  `attached` (the `_attached` rows) says what each attachment was, in order; a picture's bytes
 *  still come from its content part. A message from before the rows is read from its content alone:
 *  its pictures, its PDFs by file name, its files by the line that opens each, none of them the
 *  person's words. */
export function userItemFromContent(content: any, attached?: unknown): UserItem {
  if (typeof content === "string") return { kind: "user", text: content };
  if (!Array.isArray(content)) return { kind: "user", text: "" };

  const words: string[] = [];
  const pictures: string[] = [];
  const seen: Attachment[] = [];
  for (const part of content) {
    if (!part || typeof part !== "object") continue;
    if (part.type === "text" && part.text) {
      const body = String(part.text);
      if (body.startsWith(ATTACHED_PREFIX)) {
        const name = body.slice(ATTACHED_PREFIX.length, body.indexOf("]"));
        if (name) seen.push({ kind: "text", name });
      } else if (!/^\[\d+ more attachment\(s\) were not included/.test(body)) {
        words.push(body);
      }
    } else if (part.type === "image_url") {
      const url = part.image_url?.url;
      if (typeof url === "string" && url.startsWith("data:image/")) {
        pictures.push(url);
        seen.push({ kind: "image", name: "image", data_url: url });
      }
    } else if (part.type === "file" && part.file) {
      seen.push({ kind: "pdf", name: String(part.file.filename || "attachment.pdf") });
    }
  }
  const text = words.join("\n\n");
  const rows = Array.isArray(attached) ? (attached as AttachedRow[]).filter((r) => r && typeof r === "object") : [];
  if (!rows.length) return { kind: "user", text, attachments: seen };

  // Each picture row takes the next picture's bytes. One the route could not take became a line of
  // text, and is then drawn by its name as a file.
  let next = 0;
  const attachments = rows.map((row): Attachment => {
    const kind = ATTACHMENT_KINDS.find((k) => k === row.kind) ?? "file";
    const a: Attachment = { kind, name: String(row.name || "attachment") };
    if (row.mime) a.mime = row.mime;
    if (typeof row.size === "number") a.size = row.size;
    if (typeof row.lines === "number") a.lines = row.lines;
    if (row.path) a.path = row.path;
    if (kind === "image") {
      const url = pictures[next++];
      if (url) a.data_url = url;
      else a.kind = "file";
    }
    return a;
  });
  return { kind: "user", text, attachments };
}
