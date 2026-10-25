import { fetch, httpBase } from "./base";

// -- the branch picker: the person's branch, per folder --------------------------

export interface LocalBranch {
  name: string;
  current: boolean;
  upstream: string | null;
  ahead: number;
  behind: number;
  /** The upstream was deleted on the remote. */
  gone: boolean;
  /** Age of its last commit ("2 hours ago"). */
  when: string;
  subject: string;
}

export interface RemoteBranch {
  /** `origin/feature/x` */
  name: string;
  /** `feature/x`: the local name a switch creates. */
  branch: string;
}

export interface Branches {
  is_git: boolean;
  root?: string;
  current?: string | null;
  detached?: boolean;
  /** Tracked files have uncommitted changes: they travel with a switch, or block it. */
  dirty?: boolean;
  local: LocalBranch[];
  remote: RemoteBranch[];
}

export async function getBranches(path: string): Promise<Branches> {
  const res = await fetch(`${httpBase()}/v1/workspaces/branches?path=${encodeURIComponent(path)}`);
  return res.json();
}

async function post<T>(route: string, body: unknown): Promise<T> {
  const res = await fetch(`${httpBase()}/v1/workspaces/${route}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return res.json();
}

/** Switch the folder to a local or remote branch, or create one from HEAD. */
export const switchBranch = (path: string, branch: string, create = false) =>
  post<{ ok: boolean; branch?: string; error?: string }>("switch-branch", { path, branch, create });

/** A new checkout of a branch beside the repository, for a session of its own. */
export const newWorktree = (path: string, branch: string, create = false) =>
  post<{ ok: boolean; path?: string; branch?: string; error?: string }>("worktree", { path, branch, create });
