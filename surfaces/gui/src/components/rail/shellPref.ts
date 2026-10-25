// The remembered terminal shell — Reeve stores the menu's `id` (cmd / bash / powershell / sh) per device. The guide menu
// writes it, the terminal door and the tab read it, the server is the one that validates it.

export const RAIL_SHELL_KEY = "reeve:rail-shell:v1";

const KNOWN = new Set(["cmd", "bash", "powershell", "sh"]);

export function readShellPref(): string | null {
  try {
    const raw = localStorage.getItem(RAIL_SHELL_KEY);
    return raw && KNOWN.has(raw) ? raw : null;
  } catch {
    return null;
  }
}

export function writeShellPref(id: string): void {
  try {
    localStorage.setItem(RAIL_SHELL_KEY, id);
  } catch {
    /* best effort */
  }
}

/** The shell chip's label: a path or id, without an `.exe` suffix (`cmd.exe` → `cmd`). */
export function shellDisplayLabel(value: string | null | undefined): string {
  const raw = (value || "").trim();
  if (!raw) return "shell";
  const base = raw.split(/[\\/]/).pop() || raw;
  return base.replace(/\.exe$/i, "");
}
