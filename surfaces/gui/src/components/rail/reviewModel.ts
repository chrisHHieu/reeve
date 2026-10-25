// What Review shows and how: the scopes, the per-file rows merged from git's list, the
// "Viewed" marks and the tree the right column draws. Pure, so the pane stays layout.

import type { ChangeFile, GitScope } from "../../api";
import type { RecordedChange } from "../../reviewChanges";
import { isGeneratedPath } from "./changeStatus";

/** Reeve's own work (`last-turn`, `session`) reads from the transcript and works outside
 *  git. The rest is git's: the working tree (`uncommitted`), its two halves (`staged`,
 *  `unstaged`), the branch against its base, or one commit (`commit:<sha>`). */
export type ReviewScope = "last-turn" | "session" | GitScope;

export const isGitScope = (scope: ReviewScope): scope is GitScope => scope !== "last-turn" && scope !== "session";
export const isCommitScope = (scope: ReviewScope): scope is `commit:${string}` => scope.startsWith("commit:");
/** History is looked at, never changed from here. */
export const isReadOnlyScope = (scope: ReviewScope) => scope === "branch" || isCommitScope(scope);
/** The scopes whose files can be staged, reverted and committed. */
export const isWorkingScope = (scope: ReviewScope) => isGitScope(scope) && !isReadOnlyScope(scope);

export const REEVE_SCOPES: { id: "last-turn" | "session"; label: string; hint: string }[] = [
  { id: "last-turn", label: "Last turn", hint: "what Reeve just did" },
  { id: "session", label: "This session", hint: "every turn, with Undo \u00b7 works outside git" },
];

export const GIT_SCOPES: { id: "uncommitted" | "staged" | "unstaged"; label: string; hint: string }[] = [
  { id: "uncommitted", label: "Uncommitted", hint: "everything since the last commit, by anyone" },
  { id: "staged", label: "Staged", hint: "what the next commit will hold" },
  { id: "unstaged", label: "Unstaged", hint: "edited, not staged yet" },
];

/** A scope's name on the bar. `base` names the branch `branch` compares with. */
export function scopeLabel(scope: ReviewScope, base?: string): string {
  if (isCommitScope(scope)) return `Commit ${scope.slice("commit:".length, "commit:".length + 7)}`;
  if (scope === "branch") return `Against ${base || "the base branch"}`;
  return [...REEVE_SCOPES, ...GIT_SCOPES].find((s) => s.id === scope)?.label ?? scope;
}

/** Git's working tree in a git folder, Reeve's own work elsewhere. It does not move with the
 *  counts: a commit that empties the tree must not throw the reader into another scope. */
export function defaultScope(isGit: boolean): ReviewScope {
  return isGit ? "uncommitted" : "session";
}

/** The newest turn's changes: "what did Reeve just do". */
export function lastTurnChanges(recorded: RecordedChange[]): RecordedChange[] {
  const turns = recorded.map((change) => change.turn ?? 0);
  if (turns.length === 0) return [];
  const last = Math.max(...turns);
  return recorded.filter((change) => (change.turn ?? 0) === last);
}

/** One row per path. Git lists a file staged and edited again twice; a reader sees one file
 *  that is fully, partly or not staged. */
export interface ReviewFile {
  path: string;
  status: string;
  add: number;
  del: number;
  staged: "all" | "some" | "none";
  generated: boolean;
  /** A change of this size in this state: a Viewed mark stops applying when it moves. */
  signature: string;
  /** The file as it is on disk (`size:mtime`): an open card reads its diff again when this moves,
   *  not whenever the list was read. Empty when the server did not say (then every re-read counts). */
  revision: string;
  /** Git would call it binary: its card shows facts, starts closed, and never carries bytes. */
  binary: boolean;
  /** Bytes on disk, when the server said. */
  size?: number;
  /** What a Viewed mark is filed under: the path, in the scope a person lives in (the
   *  working tree), and the path under its scope elsewhere, so a commit's mark is its own. */
  viewKey: string;
}

export function mergeChanges(changes: ChangeFile[], viewPrefix = ""): ReviewFile[] {
  const byPath = new Map<string, ChangeFile[]>();
  for (const change of changes) byPath.set(change.path, [...(byPath.get(change.path) ?? []), change]);
  const files: ReviewFile[] = [];
  for (const [path, entries] of byPath) {
    const staged = entries.filter((entry) => entry.staged).length;
    const add = entries.reduce((sum, entry) => sum + entry.add, 0);
    const del = entries.reduce((sum, entry) => sum + entry.del, 0);
    const status = entries.find((entry) => !entry.staged)?.status ?? entries[0].status;
    files.push({
      path,
      status,
      add,
      del,
      staged: staged === 0 ? "none" : staged === entries.length ? "all" : "some",
      generated: isGeneratedPath(path),
      signature: `${status}:${add}:${del}`,
      // Both sides of a staged-and-edited file count: either moving is a change to read.
      revision: entries.map((entry) => entry.revision ?? "").join("|").replace(/^\|+$/, ""),
      binary: entries.some((entry) => entry.binary),
      size: entries.find((entry) => entry.size !== undefined)?.size,
      viewKey: `${viewPrefix}${path}`,
    });
  }
  // Source first, tool caches and dependencies last: the person's code is what they review.
  return files.sort((a, b) => Number(a.generated) - Number(b.generated) || a.path.localeCompare(b.path));
}

/** `345 KB`, `1.2 MB`: a file's size as a person reads it. */
export function formatSize(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

// -- Viewed marks (per session, this device) ---------------------------------------------

const viewedKey = (sessionId: string) => `reeve.review.viewed.${sessionId}`;

/** path → the signature it was marked viewed at. */
export function readViewed(sessionId: string): Record<string, string> {
  try {
    const raw = localStorage.getItem(viewedKey(sessionId));
    const parsed = raw ? JSON.parse(raw) : {};
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

export function writeViewed(sessionId: string, viewed: Record<string, string>): void {
  try {
    localStorage.setItem(viewedKey(sessionId), JSON.stringify(viewed));
  } catch {
    /* a convenience, never a requirement */
  }
}

export const isViewed = (viewed: Record<string, string>, file: ReviewFile) =>
  viewed[file.viewKey] === file.signature;

/** The prefix for a scope's Viewed marks: none for the working tree, so marks made before
 *  scopes existed still hold. */
export const viewPrefixFor = (scope: ReviewScope) => (scope === "uncommitted" ? "" : `${scope}|`);
