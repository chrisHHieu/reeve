// The line that opens a turn the ENGINE started.
//
// A finished background subagent, or a background command that ended or printed the line the
// agent waited for, is delivered to the session as a user-role message the model reads
// (`source.synthetic`). The person never wrote it, so it is not their bubble. But
// drawing nothing left the turn it starts with no boundary: its answer was stacked onto the
// previous turn's as a second answer, under the first one's "Worked for", and the first
// answer's hover row was drawn over it (owner-hit 2026-10-13). This line is that boundary: it
// says who spoke, in one quiet sentence, and the turn after it is a turn of its own.
//
// The sidecar says which thing reported back (`title`: the child's task, or the command) and
// how it ended, so the line names it: "Subagent finished · Write the tests · 7 steps". An
// older message has neither and gets the plain sentence.

import type { NoticeItem } from "./types";

/** What reported back, for the marker: which kind, its name (the child's task, or the
 *  command), the words for how it ended, a detail ("7 steps", "exit 0"), and the child
 *  session to open. */
export type WakeFacts = NonNullable<NoticeItem["wakeFacts"]>;

const text = (value: unknown): string | undefined => (typeof value === "string" && value ? value : undefined);

function factsOf(source: Record<string, unknown>, kind: string): WakeFacts {
  const title = text(source.title);
  if (kind === "background_task") {
    const failed = source.state === "error";
    const steps = typeof source.steps === "number" ? source.steps : null;
    // A background workflow reports back as the run it is: its name without the
    // " workflow" its title carries, and how many agents did the work. It read "Subagent
    // finished" over a runner whose own transcript is one call (owner, 2026-10-13).
    const workflow = source.workflow === true;
    const agents = typeof source.agents === "number" ? source.agents : null;
    const shown = workflow && title ? title.replace(/ workflow$/, "") : title;
    const detail = workflow
      ? agents !== null
        ? `${agents} agent${agents === 1 ? "" : "s"}`
        : null
      : steps !== null
        ? `${steps} step${steps === 1 ? "" : "s"}`
        : null;
    const noun = workflow ? "Workflow" : "Subagent";
    return {
      kind: "task",
      verb: failed ? `${noun} failed` : `${noun} finished`,
      ...(text(source.session_id) ? { sessionId: text(source.session_id) as string } : {}),
      ...(shown ? { title: shown } : {}),
      ...(detail ? { detail } : {}),
      ...(failed ? { failed: true } : {}),
    };
  }
  if (kind === "background_shell") {
    const exit = typeof source.exit_code === "number" ? source.exit_code : null;
    const matched = source.state === "matched";
    return {
      kind: "shell",
      verb: matched ? "Background command said what it was waited for" : "Background command finished",
      ...(title ? { title } : {}),
      ...(!matched && exit !== null ? { detail: `exit ${exit}` } : {}),
      ...(!matched && exit !== null && exit !== 0 ? { failed: true } : {}),
    };
  }
  if (kind === "restart") return restartFacts(source);
  return { kind: "other", verb: "Something running in the background reported back" };
}

/** Reeve picked this turn up after the process that ran it ended: how far it had got,
 *  and the background commands the restart stopped. */
function restartFacts(source: Record<string, unknown>): WakeFacts {
  const steps = typeof source.steps === "number" ? source.steps : 0;
  const jobs = Array.isArray(source.jobs) ? source.jobs.filter((j): j is string => typeof j === "string" && !!j) : [];
  const parts = [
    steps > 0 ? `${steps} step${steps === 1 ? " was" : "s were"} already done` : "picked up where it left off",
    ...(jobs.length ? [jobs.length === 1 ? `${jobs[0]} was stopped` : `${jobs.length} background commands were stopped`] : []),
  ];
  return { kind: "restart", verb: "Reeve restarted", detail: parts.join(" · ") };
}

/** A persisted `restart` notice: the line a picked-up turn shows where its calls went on, or the
 *  turn Reeve gave up on after restarting too many times. */
export function restartNotice(m: Record<string, unknown>): NoticeItem {
  if (m.given_up === true)
    return { kind: "notice", tone: "warn", text: text(m.text) ?? "Reeve restarted too many times during this turn." };
  const facts = restartFacts(m);
  return { kind: "notice", tone: "info", text: [facts.verb, facts.detail].join(" · "), wakeFacts: facts, inline: true };
}

/** A persisted `job_stopped` notice: a background command the end of Reeve's process stopped,
 *  with what it had printed (kept at a graceful quit). */
export function jobStoppedNotice(m: Record<string, unknown>): NoticeItem {
  const command = text(m.command);
  const output = text(m.tail);
  const facts: WakeFacts = {
    kind: "shell",
    verb: "Background task stopped",
    ...(command ? { title: command } : {}),
    detail: "Reeve quit while it ran",
    failed: true,
    ...(output ? { output } : {}),
  };
  return {
    kind: "notice",
    tone: "info",
    text: [facts.verb, command, facts.detail].filter(Boolean).join(" · "),
    wakeFacts: facts,
    ...(typeof m.ts === "number" && m.ts > 0 ? { ts: m.ts } : {}),
  };
}

/** In a subagent's session: a message from the agent that started it (`message_task`, ADR
 *  0180). It is that agent speaking, as its brief did, not something reporting back. */
export function fromParentAgent(source: unknown): boolean {
  return (source as { synthetic?: unknown } | null | undefined)?.synthetic === "parent_message";
}

/** What a synthetic `source` sidecar is drawn as, or null when the source is not one. */
export function wakeNotice(source: unknown, ts?: number): NoticeItem | null {
  const kind = (source as { synthetic?: unknown } | null | undefined)?.synthetic;
  if (typeof kind !== "string" || !kind || fromParentAgent(source)) return null;
  const from = source as Record<string, unknown>;
  const id = text(from.session_id) ?? text(from.task_id) ?? "";
  const facts = factsOf(from, kind);
  // The sentence a surface without the marker still reads (and the one a test can find).
  const plain =
    facts.kind === "task" && !facts.title
      ? "A subagent finished and reported back"
      : facts.kind === "shell" && !facts.title
        ? "A background command reported back"
        : [facts.verb, facts.title, facts.detail].filter(Boolean).join(" · ");
  return {
    kind: "notice",
    tone: "info",
    text: plain,
    wake: `${kind}:${id}`,
    wakeFacts: facts,
    ...(typeof ts === "number" && ts > 0 ? { ts } : {}),
  };
}
