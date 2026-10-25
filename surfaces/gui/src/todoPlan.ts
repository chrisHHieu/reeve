// The lifetime of the standing plan (`todo_write`), as one rule.
//
// The plan on screen is the LATEST list the model wrote, until it writes another or
// explicitly clears (an empty `todos` list is a clear). There is NO turn boundary in the
// rule (owner call 2026-09-04): "the plan belongs to the turn that wrote it" cleared on
// every `turn_start`, and a turn that died early — a provider error, a mid-turn stop —
// never wrote its own, so the strip went blank and nothing brought the plan back, live or
// on reload. A new task supersedes the old the moment the model writes its new plan (the
// planning prompt makes that the first call of multi-step work), and can clear the strip
// itself; a boundary is not a write.
//
// The fold's input is the SESSION EVENT LOG. That is the separation ported
// (`packages/todo/tool-todo`, where `todo/write` is a log-only event — never one of the
// three model-visible surface types — and the UI renders a projection over the log): the
// plan a person reads has its own record, rather than being re-derived from the message
// list the model is fed. `harness/engine.py` writes that record when a `todo_write` call
// lands.
//
// A session older than the recorder has no log, and the answer is the one this codebase
// already settled on for the event log (trajectoryEvents.ts): TRANSLATE it, don't write a
// second fold. `planFrom` takes an (events, messages) pair and falls back to the
// translation, so there is one rule with two ways in rather than two rules.
//
// Two paths reach that rule and they have to agree, or reopening a session shows a
// different plan than watching it did:
//   live   — `tool_finished(todo_write, ok)` sets.
//   replay — fold the events (or, degraded, translate the messages).
// Both key off a call that LANDED, not one that was proposed: `tool_proposed` fires before
// the tool runs, so keying the live path off the proposal put a plan on screen that a
// failed write never wrote, and it vanished on the next reload — the disagreement above.
// The translation is the one place the two are not identical, and deliberately: live has a
// status to read, a stored transcript may have been saved between the call and its result,
// so it drops a plan only when a result PROVES the write failed (trajectoryEvents.ts).
//
// Pure on purpose (cf. modeNotice/streamBlocks): the caller owns the state, so the rule is
// testable without rendering the app.

import type { ConversationMessage } from "./api";
import { TODO_WRITE, eventsFromMessages } from "./trajectoryEvents";
import type { SessionEvent } from "./trajectoryEvents";
import type { TodoItem } from "./types";

/** The tool whose calls establish the plan. One home for the name (trajectoryEvents.ts). */
export const TODO_TOOL = TODO_WRITE;

// Models sometimes pass todo items as bare strings instead of {content, status} objects (the
// backend tool normalizes them the same way; the GUI reads the raw proposal args, so mirror it).
export function normalizeTodos(raw: unknown): TodoItem[] {
  if (!Array.isArray(raw)) return [];
  const statuses = new Set(["pending", "in_progress", "done"]);
  return raw.map((entry: any) => {
    if (entry && typeof entry === "object") {
      const status = entry.status === "completed" ? "done" : entry.status; // common model alias
      return {
        content: String(entry.content ?? ""),
        status: statuses.has(status) ? status : "pending",
      };
    }
    return { content: String(entry ?? ""), status: "pending" as const };
  });
}

/**
 * The plan a tool call establishes, or `null` when the call doesn't establish one.
 *
 * `null` and `[]` are different answers: `[]` is the model explicitly emptying its list and
 * must replace what's on screen; `null` (another tool, or args that aren't a list) leaves
 * the standing plan alone rather than wiping it on a malformed write.
 */
export function planFromTodoWrite(name: unknown, args: any): TodoItem[] | null {
  if (name !== TODO_TOOL) return null;
  // `todos` is current; `items` reads histories from before the rename (the old key breaks
  // Together's GLM-5.2 chat template — see harness/tools/todo.py).
  const raw = args?.todos ?? args?.items;
  return Array.isArray(raw) ? normalizeTodos(raw) : null;
}

/** The standing plan after folding a session's event log — the one rule. */
export function planFromEvents(events: readonly SessionEvent[]): TodoItem[] {
  let plan: TodoItem[] = [];
  for (const e of events || []) {
    // No `turn/start` boundary here (see the header): a turn that never wrote — errored,
    // stopped, or a one-line ask — must not wipe the plan that is still standing.
    if (e.type !== "todo/write") continue;
    // Same `null` vs `[]` distinction as `planFromTodoWrite`: an empty list is the model
    // clearing its plan, a missing/malformed one is not a plan at all.
    const written = (e as { todos?: unknown }).todos;
    if (Array.isArray(written)) plan = normalizeTodos(written);
  }
  return plan;
}

/**
 * The standing plan for a stored session — the real log when there is one, the translated
 * message list when there isn't.
 */
export function planFrom(
  events: readonly SessionEvent[],
  messages?: readonly Record<string, unknown>[],
): TodoItem[] {
  const degraded = (events?.length ?? 0) === 0 && (messages?.length ?? 0) > 0;
  return planFromEvents(degraded ? eventsFromMessages(messages ?? []) : events ?? []);
}

/** The replay half of the rule, for callers holding only a message list. */
export function planFromMessages(messages: ConversationMessage[]): TodoItem[] {
  return planFrom([], (messages || []) as unknown as Record<string, unknown>[]);
}
