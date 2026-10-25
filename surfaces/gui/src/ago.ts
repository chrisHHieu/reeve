// How long ago a moment was, in the few words a list row has room for: "just now", "5m ago",
// "3h ago", "2d ago". One function so every "Last used" and every message time reads the same.

export interface SinceOptions {
  /** Below this many seconds it is "just now" (90 by default). */
  justNow?: number;
  /** Hours are counted up to this many before days take over (48 by default). */
  hoursUntilDays?: number;
  /** From this many days on, the date itself is shown instead (never, by default). */
  dateFromDays?: number;
  /** "Now", in epoch milliseconds; a test passes its own. */
  now?: number;
}

/** `epochSeconds` as a label, or null when there is no moment to describe. */
export function sinceLabel(epochSeconds: number | null | undefined, options: SinceOptions = {}): string | null {
  if (!epochSeconds || !Number.isFinite(epochSeconds)) return null;
  const { justNow = 90, hoursUntilDays = 48, dateFromDays = Infinity, now = Date.now() } = options;
  const seconds = Math.max(0, Math.floor(now / 1000 - epochSeconds));
  if (seconds < justNow) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < hoursUntilDays) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return days < dateFromDays ? `${days}d ago` : new Date(epochSeconds * 1000).toLocaleDateString();
}
