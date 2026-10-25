// Background shell jobs of a session (`run_shell` with `run_in_background`, or a foreground
// command the executor moved to the background when its budget ran out).
//
// The engine has owned these records since the beginning (`harness/tools/shell_background.py`:
// one process per job, a reader thread, a cross-platform process-tree kill) — what was
// missing is a way for the person watching to SEE them. The model reads them through
// `shell_task_output` / `shell_task_kill`; this is the same registry, for the human.
//
// The list is PUSHED: the session socket carries the whole roster as a `jobs` frame
// when a job joins it, when one ends and while one prints, and `LiveSession` holds it. The
// route below is read ONCE when a socket opens — the roster as it stands, for a client that
// was not connected when the frames went out. It is not polled: a panel that asked on a clock
// showed a job the agent started mid-turn only when the turn was over.
//
// Every call goes through this package's own `fetch` (api/base.ts), which attaches the launch
// token: the raw global fetch gets a 401 from the real server (caught by reading the server
// log after a restart, 2026-10-03 — a hermetic e2e cannot see it, because its route mock
// answers everything).

import { fetch, httpBase } from "./base";

/** One job, as `GET /v1/sessions/{id}/jobs` reports it. */
export interface JobRow {
  /** `bg-3` — the id the model also uses (`shell_task_output`'s `task_id`). */
  id: string;
  /** What was asked for, as written. */
  command: string;
  cwd?: string;
  /** The dialect it was written in (`pwsh`, `bash`, …), when the executor knew it. */
  shell?: string;
  status: "running" | "finished";
  /** Epoch SECONDS, when the job started. */
  started_at?: number;
  /** Epoch SECONDS, once it finished. */
  ended_at?: number | null;
  /** The process's own code once it finished; null while it runs. */
  exit_code?: number | null;
  duration_ms?: number | null;
  /** The engine stopped it, which the exit code cannot say: `taskkill` reports 1 on Windows
   *  and a signal on POSIX, and a job that had already finished reports its own 0 — which
   *  would read as "it worked". */
  killed?: boolean;
  /** How much the job has printed so far, in characters. */
  output_chars?: number;
  /** The last line it printed that said something, cut to a row's length and still carrying
   *  whatever colour codes the command wrote (`jobView.lastLine` strips them). */
  last_line?: string;
}

export async function getSessionJobs(sessionId: string): Promise<JobRow[]> {
  const res = await fetch(`${httpBase()}/v1/sessions/${encodeURIComponent(sessionId)}/jobs`);
  const body = await res.json().catch(() => ({}));
  return Array.isArray(body?.jobs) ? (body.jobs as JobRow[]) : [];
}

/** The tail of one job's output, for a row that is expanded.
 *
 *  It is a TAIL, and a READ: the engine's `background_output` (the model's tool) owns the
 *  incremental cursor, so a panel polling this must not consume bytes out of it — the route
 *  reads the same buffer without touching the cursor. `truncated` says the job has produced
 *  more than what came back, which the panel admits rather than passing off as the whole log. */
export interface JobOutput {
  ok: boolean;
  id?: string;
  status?: "running" | "finished";
  exit_code?: number | null;
  output?: string;
  truncated?: boolean;
  total_chars?: number;
  error?: string;
}

export async function getJobOutput(sessionId: string, id: string, tail = 4000): Promise<JobOutput> {
  const query = new URLSearchParams({ id, tail: String(tail) });
  const res = await fetch(
    `${httpBase()}/v1/sessions/${encodeURIComponent(sessionId)}/jobs/output?${query}`,
  );
  return res.json().catch(() => ({ ok: false, error: "the server did not answer" }));
}

/** Stop one job's process tree. The route calls the same `kill()` the model's
 *  `shell_task_kill` does, so the two paths cannot drift. */
export async function stopSessionJob(
  sessionId: string,
  id: string,
): Promise<{ ok: boolean; error?: string }> {
  const res = await fetch(
    `${httpBase()}/v1/sessions/${encodeURIComponent(sessionId)}/jobs/kill`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id }),
    },
  );
  return res.json().catch(() => ({ ok: false, error: "the server did not answer" }));
}

/** Say that this client is (or is no longer) WATCHING one job.
 *
 *  The engine sends a job's `job_output` frames only while somebody observes it, so a row
 *  that opens says so and a row that closes takes it back. Reference-counted on the server:
 *  two windows on one job do not cancel each other. A job that has already finished answers
 *  `{ok: false}` with a sentence — there is nothing left to stream, and the anchor read
 *  (`getJobOutput`) has all of it. */
export async function observeSessionJob(
  sessionId: string,
  id: string,
  on: boolean,
): Promise<{ ok: boolean; observing?: number; error?: string }> {
  const res = await fetch(
    `${httpBase()}/v1/sessions/${encodeURIComponent(sessionId)}/jobs/observe`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id, on }),
    },
  );
  return res.json().catch(() => ({ ok: false, error: "the server did not answer" }));
}
