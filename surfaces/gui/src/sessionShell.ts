// The session-level shell facts, shared with every tool card under it.
//
// A `run_shell` row sits many components deep (Transcript → TurnGroup → ToolCallRow → ToolRow
// → TerminalBlock), inside group rows too, and the thing it needs — which machine the session
// runs on — belongs to the SESSION, not to any of them. Threading it as a prop would mean
// touching every intermediate signature for one leaf; a context is the same shape the
// connector index (`connectorTools.ts`) already uses for the same reason.
//
// The value is provided once per session surface by `App` (from `activeEnvironment` and
// `platformOS()`) and is stable across streaming renders, so no row re-renders because of it.

import { createContext, useContext } from "react";
import type { RemoteEnvironmentSummary } from "./api/remote-environments";

export interface SessionShell {
  /** The bound SSH environment; null = this machine. */
  environment: RemoteEnvironmentSummary | null;
  /** The controller's own OS, as `platformOS()` reports it: "windows" | "macos" | "linux". */
  platform: string;
  /** The conversation on show: an answer's `file.py:42` is looked up in its folder
   *  (markdown/folderFiles.ts). Absent where no conversation is behind the text. */
  sessionId?: string;
  /** The shell a command that names none runs in, as the session's `ready` frame says (a
   *  Setting, pinned when the session was built). Absent on older servers and remote worlds. */
  shell?: string;
}

/** Without a provider the row is local and the OS unknown, which reads as POSIX bash — the
 *  pre-remote behaviour every isolated card test already depends on. */
export const LOCAL_SESSION_SHELL: SessionShell = { environment: null, platform: "" };

export const SessionShellContext = createContext<SessionShell>(LOCAL_SESSION_SHELL);

export function useSessionShell(): SessionShell {
  return useContext(SessionShellContext);
}
