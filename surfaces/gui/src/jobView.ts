// A background job, in the words and numbers a row shows — pure functions, kept out of the
// component file on purpose.
//
// `JobRow.tsx` renders the row; it must not also export helpers, because a module that mixes
// component and non-component exports cannot Fast Refresh (Vite falls back to a full page
// reload, which is what the dev server warned about on 2026-10-03). Same split as
// `delegations.ts` beside the delegation card.

import type { JobOutput, JobRow as JobRowData } from "./api/jobs";
import { outputAfter, type JobOutputBuffer } from "./live/jobOutput";

/** The two-press Stop's state, shared by the strip and the chip. */
export type KillPhase = "idle" | "armed" | "pending" | "failed";

/** The command, shortened for a row that must not grow. */
export function shortCommand(command: string, limit = 120): string {
  const one = command.replace(/\s+/g, " ").trim();
  return one.length > limit ? one.slice(0, limit - 1) + "…" : one;
}

/** One job's outcome, in the words the row shows.
 *
 *  `killed` outranks the exit code on purpose — a job stopped by the kill path reports a
 *  Windows 1 or a POSIX signal number, and reading that as "exit 1" would report a failure
 *  where the truth is that somebody pressed Stop. */
export function jobOutcome(job: JobRowData): string {
  if (job.status === "running") return "running";
  if (job.killed === true) return "stopped";
  if (job.exit_code === null || job.exit_code === undefined) return "finished";
  return `exit ${job.exit_code}`;
}

/** How a job stands, as the row's mark and colour say it. `stopped` is not `failed`: somebody
 *  pressed Stop, and the code the kill left behind says nothing about the command. */
export type JobTone = "running" | "ok" | "failed" | "stopped";

export function jobTone(job: JobRowData): JobTone {
  if (job.status === "running") return "running";
  if (job.killed === true) return "stopped";
  if (job.exit_code === null || job.exit_code === undefined || job.exit_code === 0) return "ok";
  return "failed";
}

// CSI sequences (colours, cursor moves, erase) and OSC sequences (titles, hyperlinks). A row
// shows one line of plain text; the full parser (`primitives/ansi.ts`) is for the log.
const ESCAPES = /\u001b\[[0-9;?]*[ -/]*[@-~]|\u001b\][^\u0007\u001b]*(?:\u0007|\u001b\\)/g;

/** The last thing the job printed, as one line of plain text — what a row shows without
 *  being opened. Empty when it has said nothing yet. */
export function lastLine(job: JobRowData): string {
  return (job.last_line ?? "").replace(ESCAPES, "").replace(/\s+/g, " ").trim();
}

/** Running jobs, oldest first: the order they were started in, so a row does not move while
 *  the reader is looking at it. */
export function liveJobs(jobs: readonly JobRowData[]): JobRowData[] {
  return jobs
    .filter((job) => job.status === "running")
    .sort((a, b) => (a.started_at ?? 0) - (b.started_at ?? 0));
}

/** Finished jobs, the most recently finished first. */
export function settledJobs(jobs: readonly JobRowData[]): JobRowData[] {
  return jobs
    .filter((job) => job.status !== "running")
    .sort((a, b) => (b.ended_at ?? b.started_at ?? 0) - (a.ended_at ?? a.started_at ?? 0));
}

const ROW_FACTS = [
  "id",
  "status",
  "exit_code",
  "killed",
  "started_at",
  "ended_at",
  "duration_ms",
  "last_line",
  "output_chars",
  "command",
] as const;

/** Two rosters a row would draw the same way. Lets a pushed frame that changed nothing keep
 *  the array the views already hold. */
export function sameJobs(a: readonly JobRowData[], b: readonly JobRowData[]): boolean {
  if (a === b) return true;
  if (a.length !== b.length) return false;
  return a.every((job, index) => ROW_FACTS.every((key) => job[key] === b[index][key]));
}

/** What an open row's log shows. */
export interface JobLogView {
  /** `reading`: the anchor has not answered yet. `failed`: it could not be read and the
   *  stream has nothing either. `ready`: `text` is the log, possibly empty. */
  state: "reading" | "failed" | "ready";
  text: string;
  /** The text does not begin where the job did. The log says so above itself. */
  cut: boolean;
  error?: string;
}

/** The line printed where text is known to be missing INSIDE the log. */
export const LOG_GAP = "… output dropped …";

/** How much of a log is drawn. The terminal card parses every line it is given on each new
 *  frame; the end of a long build is what a reader watches, and the rest is in the spill file
 *  the model reads. */
export const LOG_RENDER_CHARS = 48_000;

/**
 * An open row's log: the anchor (the tail route's backlog) followed by what the socket
 * delivered beyond it, bounded for drawing.
 *
 * Frames that overlap the anchor are cut by position, so observing before anchoring never
 * shows a line twice. A hole between the two is printed as a line of its own; a head that is
 * missing (a cut tail, a stream that started late, a log longer than is drawn) is `cut`.
 */
export function logText(anchor: JobOutput | null, buffer?: JobOutputBuffer): JobLogView {
  if (anchor === null) return { state: "reading", text: "", cut: false };
  const failed = anchor.ok === false;
  // A failed anchor still leaves whatever the socket delivered; it just has no backlog.
  const backlog = failed ? "" : (anchor.output ?? "");
  const after = outputAfter(buffer, failed ? 0 : (anchor.total_chars ?? 0));
  if (failed && after.text === "") {
    return {
      state: "failed",
      text: "",
      cut: false,
      error: anchor.error || "This job's output could not be read.",
    };
  }
  let cut = anchor.truncated === true;
  let text = backlog;
  if (after.gap) {
    if (text === "") cut = true; // nothing before the hole: it is a missing head
    else text += (text.endsWith("\n") ? "" : "\n") + LOG_GAP + "\n";
  }
  text += after.text;
  if (text.length > LOG_RENDER_CHARS) {
    const from = text.length - LOG_RENDER_CHARS;
    const line = text.indexOf("\n", from);
    // From the next whole line, so the first row drawn is not half of one.
    text = text.slice(line === -1 ? from : line + 1);
    cut = true;
  }
  return { state: "ready", text, cut };
}

/** A job's elapsed, ms: the measured duration once it settled, else now − its start. */
export function jobElapsedMs(job: JobRowData, nowMs: number): number | null {
  if (typeof job.duration_ms === "number" && job.duration_ms >= 0) return job.duration_ms;
  if (typeof job.started_at !== "number") return null;
  if (job.status === "running") return Math.max(0, nowMs - job.started_at * 1000);
  const ended = typeof job.ended_at === "number" ? job.ended_at * 1000 : null;
  return ended === null ? null : Math.max(0, ended - job.started_at * 1000);
}
