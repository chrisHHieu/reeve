// The session event log, as the browser sees it — and the bridge for sessions older than
// the recorder.
//
// `GET /v1/sessions/{id}/events` returns what the engine witnessed (harness/session_log.py).
// Sessions that ran before that existed have no log at all, and the honest options were a
// blank page or a second, weaker builder reading `messages`. Both are wrong: a blank page
// throws away a session someone still wants to understand, and a second builder is two
// implementations of one thing, which is the rework this project keeps refusing to create.
//
// So the fallback is a TRANSLATION, not an implementation. `eventsFromMessages` turns an
// old message list into the same event stream a new session records, and everything
// downstream has exactly one input. What it cannot invent — step boundaries, requests,
// retries, the tool catalog — is simply absent, and absent is a thing the ledger already
// knows how to render.

export interface SessionEvent {
  seq: number;
  /** Unix seconds, from the engine's own clock. */
  t: number;
  type: string;
  [key: string]: unknown;
}

/** Timing sidecar shapes, as `harness/engine.py` records them. */
export interface StepTiming {
  dispatched?: number;
  first_token?: number;
  completed?: number;
}
export interface CallTiming {
  started?: number;
  ended?: number;
}

export const num = (v: unknown): number | null =>
  typeof v === "number" && Number.isFinite(v) ? v : null;

export const str = (v: unknown): string => (typeof v === "string" ? v : "");

/** A message's text, whether plain or content-parts; an image becomes a marker. */
export function textOf(content: unknown): string {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return content == null ? "" : String(content);
  const parts: string[] = [];
  for (const p of content) {
    if (!p || typeof p !== "object") continue;
    const part = p as { type?: string; text?: string };
    if (part.type === "text" && part.text) parts.push(part.text);
    else if (part.type === "image_url") parts.push("[image]");
  }
  return parts.join("\n");
}

/** `todo_write`'s name, mirrored from harness/tools/todo.py. Declared here because this
 *  is the module that names event types; todoPlan.ts re-exports it as `TODO_TOOL`. */
export const TODO_WRITE = "todo_write";

/** The list a `todo_write` call carried, or null when its arguments held none. */
function parseTodoArgs(raw: string): unknown | null {
  try {
    const args = JSON.parse(raw || "{}") as { todos?: unknown; items?: unknown };
    // `items` is the pre-rename key, still accepted by the tool for replayed histories.
    const written = args?.todos ?? args?.items;
    return Array.isArray(written) ? written : null;
  } catch {
    return null; // unparseable args aren't a plan
  }
}

/**
 * Call ids whose tool result message PROVES the call failed — the engine's `{error, …}`
 * payload, and nothing else.
 *
 * Absence of a result is not evidence of failure, and the difference decides whether a
 * plan survives replay. A transcript can be saved between a call and its result (the
 * checkpoint rule in harness/events.py), and a turn can be interrupted there; treating
 * either as a failure would drop a plan that did land. So the translation carries every
 * `todo_write` call forward EXCEPT the ones this set names.
 */
function failedCalls(messages: readonly Record<string, unknown>[]): Set<string> {
  const failed = new Set<string>();
  for (const m of messages || []) {
    if (str(m.role) !== "tool") continue;
    const id = str(m.tool_call_id);
    if (!id) continue;
    try {
      const parsed = JSON.parse(textOf(m.content)) as Record<string, unknown> | null;
      if (typeof parsed === "object" && parsed !== null && "error" in parsed) failed.add(id);
    } catch {
      // A rendered (non-JSON) result is a result, not a failure.
    }
  }
  return failed;
}

/**
 * Translate an old message list into the event stream a session would have recorded.
 *
 * Deliberately incomplete, and the gaps are the point: there is no `step/start` because a
 * message list does not say where a step began, no `request/header` because the tool
 * catalog was never stored, no `llm/retry` because a retry left no trace. Inventing any of
 * them would put a confident wrong answer where the ledger can currently say "not
 * recorded" — which is the whole reason the log was built.
 */
export function eventsFromMessages(messages: readonly Record<string, unknown>[]): SessionEvent[] {
  const events: SessionEvent[] = [];
  let seq = 0;
  let turn = 0;
  // Read ahead once: `todo/write` is emitted beside the call that carried the list, and
  // whether to emit it at all depends on a result message that comes later.
  const failed = failedCalls(messages);
  const push = (type: string, t: number, data: Record<string, unknown>) => {
    events.push({ seq: seq++, t, type, ...data });
  };

  for (const m of messages || []) {
    const role = str(m.role);
    const t = num(m.ts) ?? 0;

    if (role === "system") {
      // The only header an old session can prove: the prompt it was given. No `tools`,
      // because nothing recorded them — so the Schema tab will say so rather than guess.
      push("request/header", t, { reason: "initial", system: textOf(m.content), turn: 0 });
      continue;
    }
    if (role === "notice") {
      push("notice", t, { notice_kind: str(m.kind), text: str(m.text), turn, ...m });
      continue;
    }
    if (role === "user") {
      const attached = str(m._attached_by);
      if (attached) {
        push("context/attached", t, { call_id: attached, content: textOf(m.content), turn });
        continue;
      }
      turn += 1;
      push("turn/start", t, { turn });
      push("user/message", t, {
        turn,
        content: textOf(m._display ?? m.content),
        ...(m.source ? { source: m.source } : {}),
      });
      continue;
    }
    if (role === "assistant") {
      push("assistant/message", t, {
        turn,
        text: textOf(m.content),
        ...(m.reasoning ? { reasoning: str(m.reasoning) } : {}),
        ...(m.usage ? { usage: m.usage } : {}),
        ...(m.timing ? { timing: m.timing } : {}),
        tool_calls: ((m.tool_calls as unknown[]) || []).map((tc) => {
          const call = tc as { id?: string; function?: { name?: string } };
          return { call_id: call.id, name: call.function?.name };
        }),
      });
      for (const tc of (m.tool_calls as unknown[]) || []) {
        const call = tc as { id?: string; function?: { name?: string; arguments?: string } };
        const args = call.function?.arguments ?? "";
        push("tool/call", t, {
          turn,
          call_id: call.id,
          name: call.function?.name,
          arguments: args,
        });
        // The plan the call carried, as the event a new session records for it. No `step`,
        // for the reason every gap in this file exists: a message list does not say which
        // step a call belonged to, and absent beats invented.
        if (call.function?.name === TODO_WRITE && !failed.has(str(call.id))) {
          const written = parseTodoArgs(args);
          if (written !== null) {
            push("todo/write", t, { turn, call_id: call.id, todos: written });
          }
        }
      }
      continue;
    }
    if (role === "tool") {
      push("tool/result", t, {
        turn,
        call_id: str(m.tool_call_id),
        content: textOf(m.content),
        ...(m.timing ? { timing: m.timing } : {}),
      });
    }
  }
  return events;
}
