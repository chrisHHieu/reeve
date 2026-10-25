// An isolated child's own copy of the repository: what it holds, and the person's
// two answers. `childId` is the CHILD session's id (`GET|POST /v1/sessions/{id}/worktree…`).

import { httpBase } from "./base";

export interface WorktreeFile {
  path: string;
  change: "created" | "modified" | "deleted";
  added: number;
  removed: number;
}

export interface ChildWorktree {
  path: string;
  branch: string;
  files: WorktreeFile[];
  /** The child is still working: its copy cannot be taken yet. */
  busy: boolean;
  error?: string;
}

export interface HandbackResult {
  ok: boolean;
  error?: string;
  files?: WorktreeFile[];
}

const url = (childId: string, verb = "") =>
  `${httpBase()}/v1/sessions/${encodeURIComponent(childId)}/worktree${verb}`;

export async function getChildWorktree(childId: string): Promise<ChildWorktree | null> {
  const res = await fetch(url(childId));
  return (await res.json()).worktree ?? null;
}

export async function applyChildWorktree(childId: string): Promise<HandbackResult> {
  return (await fetch(url(childId, "/apply"), { method: "POST" })).json();
}

export async function discardChildWorktree(childId: string): Promise<HandbackResult> {
  return (await fetch(url(childId, "/discard"), { method: "POST" })).json();
}
