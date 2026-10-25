// The work queue's groups (owner, 2026-10-13): the sessions by what each needs from
// the person. A session is in exactly one group, the most pressing that applies:
//
//   needs   it waits on the person (an approval, a question)
//   running a turn is in flight, or it will wake itself
//   review  it changed files that git still lists as uncommitted (`work.open`)
//   today   it was active today and has nothing waiting
//   earlier everything else not archived
//
// The queue lists the first three at its top; the rest are listed in their project, under it
// (owner, 2026-10-14, direction A: the folder chips hid every project with nothing live, and one
// "Earlier" mixed every project's past by time). `today` and `earlier` still mark a row.
//
// "review" is read from the server's facts and never guessed here: a session in a folder that is
// not a git repository, or one older than the server reads, has no `open` and is never in it.

import type { SessionInfo } from "../../types";

export type QueueGroup = "needs" | "running" | "review" | "today" | "earlier";

export const QUEUE_ORDER: QueueGroup[] = ["needs", "running", "review", "today", "earlier"];

const isInternal = (s: SessionInfo) => s.session_id.startsWith("__");

export function groupOf(s: SessionInfo, now: Date = new Date()): QueueGroup {
  if ((s.attention || 0) > 0) return "needs";
  if (s.liveness === "working" || s.liveness === "sleeping") return "running";
  if ((s.work?.open || 0) > 0) return "review";
  if (s.updated_at && sameDay(new Date(s.updated_at), now)) return "today";
  return "earlier";
}

function sameDay(a: Date, b: Date) {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

/** The folder a session works in, as the filter names it: none for a plain chat's temporary folder. */
export function projectOf(s: SessionInfo): string {
  if (s.temp_workspace || !s.workspace) return "";
  return (s.environment_id ? `${s.environment_id}:` : "") + s.workspace;
}

/** The groups the queue lists at its top: what the person is needed for, what runs, what waits
 *  for review. Everything else is listed in its project (owner, 2026-10-14, direction A). */
export const LIVE_GROUPS: QueueGroup[] = ["needs", "running", "review"];
export const isLive = (group: QueueGroup) => LIVE_GROUPS.includes(group);

/** One project under the queue: every session of one folder, newest first. */
export interface QueueProject {
  /** `projectOf`, or "" for the conversations with no folder. */
  key: string;
  /** All its sessions (not archived), live ones included: the count. */
  sessions: SessionInfo[];
  /** The ones not in a live group: what opening the project lists, pinned first, then newest. */
  past: SessionInfo[];
  /** The live groups it has a session in, in queue order: its marks. */
  live: QueueGroup[];
  /** Its newest session's `updated_at`. */
  latest: string;
}

/** Every project, the one with the newest session first; the conversations with no folder are
 *  apart (`loose`), since a chat's temporary folder is no project. */
export function queueByProject(sessions: SessionInfo[], now: Date = new Date()): { projects: QueueProject[]; loose: QueueProject | null } {
  const byKey = new Map<string, QueueProject>();
  const order = (a: SessionInfo, b: SessionInfo) => Number(!!b.pinned) - Number(!!a.pinned) || newestFirst(a, b);
  for (const s of [...sessions].sort(newestFirst)) {
    if (isInternal(s) || s.archived) continue;
    const key = projectOf(s);
    let project = byKey.get(key);
    if (!project) {
      project = { key, sessions: [], past: [], live: [], latest: s.updated_at || "" };
      byKey.set(key, project);
    }
    project.sessions.push(s);
    const group = groupOf(s, now);
    if (isLive(group)) {
      if (!project.live.includes(group)) project.live.push(group);
    } else project.past.push(s);
  }
  for (const project of byKey.values()) {
    project.past.sort(order);
    project.live.sort((a, b) => QUEUE_ORDER.indexOf(a) - QUEUE_ORDER.indexOf(b));
  }
  const loose = byKey.get("") ?? null;
  byKey.delete("");
  return { projects: [...byKey.values()], loose };
}

const newestFirst = (a: SessionInfo, b: SessionInfo) => (b.updated_at || "").localeCompare(a.updated_at || "");

export interface Queue {
  groups: Record<QueueGroup, SessionInfo[]>;
  archived: SessionInfo[];
}

/** The sessions in their groups, newest first in each, pinned ones first. An automation's run
 *  conversation is listed only while it waits on the person (the server sends it only then). */
export function buildQueue(sessions: SessionInfo[], now: Date = new Date()): Queue {
  const groups: Record<QueueGroup, SessionInfo[]> = { needs: [], running: [], review: [], today: [], earlier: [] };
  const archived: SessionInfo[] = [];
  const order = (a: SessionInfo, b: SessionInfo) => Number(!!b.pinned) - Number(!!a.pinned) || newestFirst(a, b);
  for (const s of [...sessions].sort(order)) {
    if (isInternal(s) && !s.automation) continue;
    if (s.archived) {
      archived.push(s);
      continue;
    }
    groups[groupOf(s, now)].push(s);
  }
  return { groups, archived };
}
