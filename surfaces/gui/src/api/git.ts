import { fetch, httpBase } from "./base";

// -- git changes (the rail's Review tab) --------------------------------------

export interface ChangeFile {
  path: string; // workspace-relative
  status: "M" | "A" | "D" | "R" | string;
  add: number;
  del: number;
  /** True when the file has changes in the index; the working-tree
   *  side is !staged. A file staged and edited again reports true — git lists it twice. */
  staged?: boolean;
  /** Bytes on disk; absent for a deleted file and past the server's look-at limit. */
  size?: number;
  /** An untracked file git would call binary: Review shows its facts, never its bytes. */
  binary?: boolean;
  /** `size:mtime` of the file: moves when the file does, so a diff is read again for a changed
   *  file and not whenever the list was read. */
  revision?: string;
}

export interface SessionChanges {
  is_git: boolean;
  files: ChangeFile[];
  workspace: string | null;
  /** The scope these files are of (the server echoes what was asked). */
  scope?: string;
  /** The branch a `branch` scope compares with. */
  base?: string;
  /** Why a scope is empty, when it is not just "nothing changed". */
  note?: string;
  error?: string;
}

/** What Review compares, besides Reeve's own work: the working tree against HEAD (the
 *  default), the index, the edits not yet staged, the branch against its base, or one commit
 *  (`commit:<sha>`). */
export type GitScope = "uncommitted" | "staged" | "unstaged" | "branch" | `commit:${string}`;

/** The workspace's changes in a scope (read-only), backing the Review tab. */
export async function getSessionChanges(sessionId: string, scope: GitScope = "uncommitted"): Promise<SessionChanges> {
  const q = scope === "uncommitted" ? "" : `?${new URLSearchParams({ scope }).toString()}`;
  const res = await fetch(`${httpBase()}/v1/sessions/${encodeURIComponent(sessionId)}/changes${q}`);
  return res.json();
}

/** One changed file's patch, or its content when it is untracked (read-only). */
export async function getChangeDiff(
  sessionId: string,
  path: string,
  scope: GitScope = "uncommitted",
): Promise<{
  ok: boolean;
  path?: string;
  patch?: string;
  untracked?: boolean;
  content?: string;
  /** The file is binary: no content was sent, only `size`. */
  binary?: boolean;
  /** Bytes on disk, sent with `binary` and with a `truncated` preview. */
  size?: number;
  /** `content` is the start of a long file, cut at a line. */
  truncated?: boolean;
  error?: string;
}> {
  const q = new URLSearchParams({ path });
  if (scope !== "uncommitted") q.set("scope", scope);
  const res = await fetch(
    `${httpBase()}/v1/sessions/${encodeURIComponent(sessionId)}/changes/diff?${q.toString()}`,
  );
  return res.json();
}

// -- Source Control actions: the PERSON's git writes from Review -------------

export interface ScmResult {
  ok: boolean;
  error?: string;
  /** The new commit's short hash (commit only). */
  commit?: string;
}

async function scmPost(sessionId: string, action: string, body: unknown): Promise<ScmResult> {
  const res = await fetch(`${httpBase()}/v1/sessions/${encodeURIComponent(sessionId)}/scm/${action}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return res.json();
}

export const scmStage = (sessionId: string, paths: string[]) => scmPost(sessionId, "stage", { paths });
export const scmUnstage = (sessionId: string, paths: string[]) => scmPost(sessionId, "unstage", { paths });
/** Throws away working-tree changes to exactly these paths — the caller confirms first. */
export const scmDiscard = (sessionId: string, paths: string[]) => scmPost(sessionId, "discard", { paths });
/** Commits the staged changes, or everything when `all` (nothing staged). */
export const scmCommit = (sessionId: string, message: string, all = false) =>
  scmPost(sessionId, "commit", { message, all });
/** Fast-forward the branch to its upstream; a pull that needs a merge is refused. */
export const scmPull = (sessionId: string) => scmPost(sessionId, "pull", {});

export interface PullRequestCheck {
  name: string;
  state: "passed" | "failed" | "running";
  url: string | null;
}

export interface PullRequest {
  number: number;
  title: string;
  url: string;
  state: string;
  draft: boolean;
  head: string;
  base: string;
  /** APPROVED / CHANGES_REQUESTED / REVIEW_REQUIRED, or "" */
  review: string;
  checks: {
    passed: number;
    failed: number;
    running: number;
    verdict: "passed" | "failed" | "running" | "none";
    items: PullRequestCheck[];
  };
}

/** The branch's open pull request, read through the person's GitHub CLI. `available` is
 *  false (with the reason) when gh is missing or signed out, or there is no GitHub remote. */
export async function getPullRequest(
  sessionId: string,
): Promise<{ available: boolean; reason?: string; pr?: PullRequest | null }> {
  const res = await fetch(`${httpBase()}/v1/sessions/${encodeURIComponent(sessionId)}/scm/pr`);
  return res.json();
}

/** Open a pull request for the current branch (pushed first if it has no upstream). */
export const createPullRequest = (
  sessionId: string,
  pr: { title: string; body: string; base: string; draft: boolean },
) => scmPost(sessionId, "pr", pr) as Promise<ScmResult & { url?: string }>;

/** Push the current branch (publishing it with -u when it has no upstream). */
export const scmPush = (sessionId: string) =>
  scmPost(sessionId, "push", {}) as Promise<ScmResult & { remote?: string; branch?: string }>;

/** A commit message for the staged change, or for everything with `all` (one model call). */
export const scmSuggestMessage = (sessionId: string, all: boolean) =>
  scmPost(sessionId, "suggest-message", { all }) as Promise<ScmResult & { message?: string }>;

/** One thing Reeve's review raised, on a line the diff shows (new side). */
export interface ReviewFinding {
  path: string;
  line: number;
  severity: "bug" | "risk" | "nit";
  body: string;
}

/** Reeve reads a scope's diff and raises findings (one model call). */
export const scmReview = (sessionId: string, scope: GitScope) =>
  scmPost(sessionId, "review", { scope }) as Promise<ScmResult & { findings?: ReviewFinding[]; truncated?: boolean }>;

/** One hunk of one file: stage or revert it from the working tree's diff, unstage it from the
 *  index's. `header` is the hunk's `@@` line as drawn, so a file that moved on is refused. */
export const scmHunk = (
  sessionId: string,
  hunk: { path: string; index: number; header: string; action: "stage" | "unstage" | "revert" },
) => scmPost(sessionId, "hunk", hunk);

export interface ScmCommit {
  hash: string;
  short: string;
  author: string;
  when: string;
  subject: string;
  /** `HEAD -> main`, `origin/main`, `tag: v1` … */
  refs: string[];
}

export interface ScmLog {
  is_git: boolean;
  branch?: string;
  /** Commits ahead of the upstream, when there is one. */
  ahead?: number | null;
  commits: ScmCommit[];
  /** A full page came back, so older commits may be asked for. */
  more?: boolean;
  /** The branch a pull request from here would target (main, master, or the remote default). */
  base?: string;
  /** Where Push goes; absent when the repository has no remote (no push buttons then). */
  remote?: string | null;
  /** Commits the last fetch knows are waiting upstream. */
  behind?: number | null;
  /** `origin/main`, or null when the branch was never pushed. */
  upstream?: string | null;
}

export async function getScmLog(sessionId: string, limit = 50): Promise<ScmLog> {
  const res = await fetch(`${httpBase()}/v1/sessions/${encodeURIComponent(sessionId)}/scm/log?limit=${limit}`);
  return res.json();
}

/** Show a workspace file in the OS file manager (server-side, confined to the workspace). */
export async function revealPath(
  sessionId: string,
  path: string,
): Promise<{ ok: boolean; path?: string; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/sessions/${encodeURIComponent(sessionId)}/reveal`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
  if (!res.ok) throw new Error(`reveal path failed: ${res.status}`);
  return res.json();
}

/** Put back the files one tool call changed (harness/tools/undo.py). */
/** The calls this session can still undo — the transcript does not record it, so a reopened
 *  session's rows ask the server (its undo store checks the disk). */
export async function getUndoableCalls(sessionId: string): Promise<string[]> {
  const res = await fetch(`${httpBase()}/v1/sessions/${encodeURIComponent(sessionId)}/undoable`);
  const body = await res.json();
  return Array.isArray(body?.call_ids) ? body.call_ids : [];
}

export async function undoToolCall(
  sessionId: string,
  callId: string,
): Promise<{ ok: boolean; restored?: string[]; failed?: string[]; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/sessions/${encodeURIComponent(sessionId)}/undo`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ call_id: callId }),
  });
  return res.json();
}

/** On-demand compaction: condense the history now,
 *  even below the automatic threshold. Refused mid-turn; reports what it saved or why
 *  nothing could be done. */
export async function compactNow(
  sessionId: string,
): Promise<{
  ok: boolean;
  error?: string;
  /** The server already told every view of the session how it ended. */
  announced?: boolean;
  text?: string;
  reason?: string;
  saved_tokens?: number;
  now_tokens?: number;
  summarizer_tokens?: number;
}> {
  const res = await fetch(
    `${httpBase()}/v1/sessions/${encodeURIComponent(sessionId)}/compact`,
    { method: "POST", headers: { "Content-Type": "application/json" } },
  );
  return res.json();
}

/** The server's fold over the session event log: retries, latency, tool efficiency. */
export async function getSessionMetrics(
  sessionId: string,
): Promise<import("../types").SessionMetrics> {
  const res = await fetch(
    `${httpBase()}/v1/sessions/${encodeURIComponent(sessionId)}/metrics`,
  );
  return await res.json();
}

/** Show the artifact in the OS file manager ("reveal") or open it with its default app ("open"). */
export async function revealArtifact(
  sessionId: string,
  path: string,
  mode: "reveal" | "open" = "reveal",
): Promise<{ ok: boolean; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/sessions/${encodeURIComponent(sessionId)}/artifacts/reveal`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, mode }),
  });
  return res.json();
}
