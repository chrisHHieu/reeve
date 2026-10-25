// Paths as the sidecar sends them: in the system's own form, so a Windows one has backslashes.

/** A path's last part, for showing a folder by its name. Both separators count, or
 *  "C:\Users\X\proj" would be one long name. */
export const baseName = (p: string) => p.split(/[\\/]/).filter(Boolean).pop() || p;

/** Search-box text that is a folder someone could open: an absolute POSIX path, `~`, or a Windows
 *  drive or UNC path. A plain word ("exports") filters the known folders instead. */
export function looksLikeFolderPath(value: string): boolean {
  const text = value.trim();
  return text.startsWith("/") || text === "~" || text.startsWith("~/") || text.startsWith("\\\\") || /^[A-Za-z]:[\\/]/.test(text);
}

/** One spelling per folder, so the current folder keeps its check mark however it was typed: the
 *  trailing separators go, and a Windows path is lowercased, case not counting there. */
export function normalizeFolderPath(value: string): string {
  const text = value.trim();
  if (!text) return "";
  const bare = text.replace(/[\\/]+$/, "") || text;
  return /^[A-Za-z]:/.test(bare) || bare.startsWith("\\\\") ? bare.toLowerCase() : bare;
}
