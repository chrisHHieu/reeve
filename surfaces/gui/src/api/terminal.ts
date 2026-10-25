import { fetch, httpBase, openWebSocket, wsBase } from "./base";

// The user's terminal panel (`/terminal`): a dedicated shell per session, owned by the
// harness (`harness/server/routes/terminal.py`). REST creates/attaches and closes it;
// live output and input ride the WS. This is deliberately NOT the agent's `terminal_*`
// tool shell: two readers over one pipe would steal each other's output.

export interface UserTerminalInfo {
  ok?: boolean;
  error?: string;
  id?: string;
  session_id?: string;
  /** The client-minted tab id this terminal belongs to. */
  terminal?: string;
  name?: string;
  cwd?: string;
  shell?: string;
  running?: boolean;
  exit_code?: number;
  /** Retained scrollback at attach time (bounded, like the tool's). */
  output?: string;
  /** Absolute position the scrollback covers; live chunks carry their own. */
  produced?: number;
  cols?: number;
  rows?: number;
}

export interface OpenTerminalOptions {
  /** The tab's terminal id ("t1", "t2", …) — several terminals can share a session. */
  terminal?: string;
  /** The shell menu's pick (`cmd` / `bash` / `powershell`). */
  shell?: string;
  /** The emulator's window size (xterm's fit reports it). */
  cols?: number;
  rows?: number;
}

/** Create or attach one of the session's terminals. The body carries the tab id, the
 *  menu's shell pick and the emulator's size; absent values mean the server's defaults. */
export async function openUserTerminal(
  sessionId: string,
  opts: OpenTerminalOptions = {},
): Promise<UserTerminalInfo> {
  const body: Record<string, unknown> = { terminal: opts.terminal || "1" };
  if (opts.shell) body.shell = opts.shell;
  if (opts.cols && opts.rows) {
    body.cols = opts.cols;
    body.rows = opts.rows;
  }
  const res = await fetch(
    `${httpBase()}/v1/sessions/${encodeURIComponent(sessionId)}/terminal`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    },
  );
  return res.json();
}

/** One launchable shell (`/_terminal/shells`): `id` is the remembered wire value, `name`
 *  is the menu label, `path` is what the backend spawns. */
export interface UserTerminalShell {
  id: string;
  name: string;
  path: string;
}

/** The shells this machine can launch — the guide menu's discovery (`launchShells`,
 *  TerminalGuide.tsx). Only what the backend can actually spawn is ever offered. */
export async function listUserTerminalShells(
  sessionId: string,
): Promise<{ ok?: boolean; error?: string; shells?: UserTerminalShell[] }> {
  const res = await fetch(
    `${httpBase()}/v1/sessions/${encodeURIComponent(sessionId)}/terminal/shells`,
  );
  return res.json();
}

/** Close (kill) one of the session's terminals. A later open starts a fresh one. */
export async function closeUserTerminal(
  sessionId: string,
  terminalId = "1",
): Promise<{ ok: boolean; closed?: boolean }> {
  const res = await fetch(
    `${httpBase()}/v1/sessions/${encodeURIComponent(sessionId)}/terminal?terminal=${encodeURIComponent(terminalId)}`,
    { method: "DELETE" },
  );
  return res.json();
}

/** The terminal channel's URL, authenticated like the session socket. */
export function userTerminalSocketUrl(sessionId: string, terminalId = "1"): string {
  return `${wsBase()}/ws/terminal/${encodeURIComponent(sessionId)}?terminal=${encodeURIComponent(terminalId)}`;
}

export function openUserTerminalSocket(sessionId: string, terminalId = "1"): WebSocket {
  return openWebSocket(userTerminalSocketUrl(sessionId, terminalId));
}
