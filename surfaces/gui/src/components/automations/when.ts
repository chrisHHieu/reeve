// When something ran or will run, as short as it can be said: "today 21:21", "tomorrow 9:00",
// "Mon 9:00", "9 Oct 17:40". The list and the runs table say every time this one way, so two
// rows are compared by reading down a column.

const dayStart = (date: Date) => new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();

/** The time of day as the person's locale writes it ("21:21", "9:21 PM"). */
export function clock(epochSeconds: number, locale?: string): string {
  return new Date(epochSeconds * 1000).toLocaleTimeString(locale, { hour: "numeric", minute: "2-digit" });
}

/** The day, relative while that is shorter: today, tomorrow, yesterday, a weekday within the
 * week on either side, then the date (with the year once it is another year). */
export function dayWord(epochSeconds: number, now: Date = new Date(), locale?: string): string {
  const then = new Date(epochSeconds * 1000);
  const days = Math.round((dayStart(then) - dayStart(now)) / 86_400_000);
  if (days === 0) return "today";
  if (days === 1) return "tomorrow";
  if (days === -1) return "yesterday";
  if (Math.abs(days) < 7) return then.toLocaleDateString(locale, { weekday: "short" });
  if (then.getFullYear() === now.getFullYear()) return then.toLocaleDateString(locale, { day: "numeric", month: "short" });
  return then.toLocaleDateString(locale, { day: "numeric", month: "short", year: "numeric" });
}

export function whenText(epochSeconds: number | null | undefined, now: Date = new Date(), locale?: string): string {
  if (!epochSeconds) return "";
  return `${dayWord(epochSeconds, now, locale)} ${clock(epochSeconds, locale)}`;
}

/** How long a run took: "41s", "2m 23s", "1h 05m". Whole seconds: a run is not timed finer. */
export function took(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "";
  const whole = Math.round(seconds);
  if (whole < 60) return `${whole}s`;
  if (whole < 3600) return `${Math.floor(whole / 60)}m ${String(whole % 60).padStart(2, "0")}s`;
  return `${Math.floor(whole / 3600)}h ${String(Math.floor((whole % 3600) / 60)).padStart(2, "0")}m`;
}
