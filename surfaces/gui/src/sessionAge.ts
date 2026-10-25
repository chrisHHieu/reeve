// When a session was last active, as short as it can be said and still be told apart from
// the row above it: "now", "12m", then the time today, "Yesterday", the weekday within a week,
// the day and month within the year, the month and year before that. The sidebar shows it at
// the right edge of every row, so three sessions with one title are three rows a person can
// tell apart.

const day = (date: Date) => new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();

/** The time of day, as the person's own locale writes it ("9:14", "9:14 AM"). */
export function clockTime(iso: string | null | undefined, locale?: string): string {
  const then = iso ? new Date(iso) : null;
  if (!then || Number.isNaN(then.getTime())) return "";
  return then.toLocaleTimeString(locale, { hour: "numeric", minute: "2-digit" });
}

export function sessionAge(iso: string | null | undefined, now: Date = new Date(), locale?: string): string {
  const then = iso ? new Date(iso) : null;
  if (!then || Number.isNaN(then.getTime())) return "";
  const seconds = Math.max(0, Math.floor((now.getTime() - then.getTime()) / 1000));
  if (seconds < 60) return "now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  const days = Math.round((day(now) - day(then)) / 86_400_000);
  if (days <= 0) return clockTime(iso, locale);
  if (days === 1) return "Yesterday";
  if (days < 7) return then.toLocaleDateString(locale, { weekday: "short" });
  if (then.getFullYear() === now.getFullYear()) return then.toLocaleDateString(locale, { day: "numeric", month: "short" });
  return then.toLocaleDateString(locale, { month: "short", year: "numeric" });
}
