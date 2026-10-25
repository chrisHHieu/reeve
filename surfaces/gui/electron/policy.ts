// The desktop shell's decisions that do not touch Electron, kept apart so they are tested alone:
// the sidecar's restart budget, the tray's line, what closing the window does, and
// which links may leave the app.

/** Restart budget: three attempts in a row, the delays growing. A run that survives
 *  `HEALTHY_UPTIME_MS` earns a fresh budget, so an app up for a week is not out of restarts. Past
 *  the budget the shell stops and says so: an endless respawn is worse than an honest failure. */
export const MAX_RESTARTS = 3;
export const HEALTHY_UPTIME_MS = 60_000;
export const RESTART_DELAYS_MS = [500, 1000, 2000] as const;

export type SidecarPhase = "started" | "crashed" | "restarting" | "stopped";

export interface SidecarState {
  state: SidecarPhase;
  attempt: number;
  max_attempts: number;
  /** Why it stopped, or the exit code of a crash. */
  reason: string | null;
}

/** The next restart after `attempt` restarts in a row: its number and its delay, or null when the
 *  budget is spent. */
export function nextRestart(attempt: number): { attempt: number; delayMs: number } | null {
  if (attempt >= MAX_RESTARTS) return null;
  const delayMs = RESTART_DELAYS_MS[Math.min(attempt, RESTART_DELAYS_MS.length - 1)];
  return { attempt: attempt + 1, delayMs };
}

/** The tray's status line for what the app last said is running. */
export function trayLine(running: number, needsYou: number, titles: string): string {
  const parts: string[] = [];
  if (running > 0) parts.push(`${running} task${running === 1 ? "" : "s"} running`);
  if (needsYou > 0) parts.push(`${needsYou} need${needsYou === 1 ? "s" : ""} you`);
  if (!parts.length) return "Nothing running";
  const head = parts.join(" · ");
  return titles ? `${head} — ${titles}` : head;
}

export type CloseBehavior = "ask" | "tray" | "quit";

/** What closing the window does, from the stored preference: "ask" until the person has chosen. */
export function closeBehavior(stored: unknown): CloseBehavior {
  return stored === "tray" || stored === "quit" ? stored : "ask";
}

/** The schemes a link may be handed to the OS for: exactly the ones whose default application the
 *  OS may be asked for. Anything else is not ours to hand out. */
export function externalAllowed(url: string): boolean {
  return /^(?:https?|mailto|tel):/i.test(url);
}

/** Whether the Browser tab's page may load an address: the web, nothing on this machine. */
export function webAddress(url: string): boolean {
  try {
    const parsed = new URL(url);
    return parsed.protocol === "http:" || parsed.protocol === "https:";
  } catch {
    return false;
  }
}

/** The last `count` non-empty lines of a log, for the details a person copies when the server did
 *  not start: what it printed last is usually why. */
export function lastLines(text: string, count: number): string {
  const lines = text.split(/\r?\n/).filter((line) => line.trim() !== "");
  return lines.slice(-count).join("\n");
}
