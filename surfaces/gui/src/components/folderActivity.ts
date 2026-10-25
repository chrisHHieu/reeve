// Whether another session is working in a folder right now (2026-10-14). Two agents in one
// checkout step on each other: a branch switched under a running turn changes the files it is
// editing, and two sessions' edits land in one diff. The branch picker asks this wherever it is
// opened (the composer's strip, the top bar's crumb, Review) and says so, and before a session's
// first message it starts on "in a new worktree" when the folder is busy.

import { createContext } from "react";
import { normalizeFolderPath } from "../paths";
import type { SessionInfo } from "../types";

/** The title of another session working in `path`, or null. */
export type FolderActivity = (path: string) => string | null;

export const FolderActivityContext = createContext<FolderActivity>(() => null);

const folderKey = (path: string) => {
  const key = normalizeFolderPath(path);
  return /^[a-z]:/.test(key) || key.startsWith("\\\\") ? key.replace(/\//g, "\\") : key;
};

/** The newest other session with a turn in flight in `path` (local folders only). */
export function otherWorkIn(sessions: readonly SessionInfo[], path: string, self: string): string | null {
  if (!path) return null;
  const key = folderKey(path);
  for (const s of sessions) {
    if (s.session_id === self || s.environment_id || s.liveness !== "working" || !s.workspace) continue;
    if (folderKey(s.workspace) === key) return s.title || s.session_id;
  }
  return null;
}
