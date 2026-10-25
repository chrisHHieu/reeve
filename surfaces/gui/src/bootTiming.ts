// How long the engine took to answer at the last few starts on this computer, so a slow start is
// said against this machine's own usual and never against a guess. Kept in this window's storage:
// it is a fact about this computer, and a start with nothing remembered says only how long it has
// waited.

const KEY = "reeve.boot.engine-ms";
/** The starts remembered. */
export const KEPT = 5;
/** The starts it takes before "usual" means anything. */
export const MIN_SAMPLES = 3;
/** Before this a start is never called slow, whatever the usual. */
export const SLOW_FLOOR_MS = 6_000;
/** How long the window waits for the engine's first answer before it says the start failed. */
export const GIVE_UP_MS = 20_000;

export function readBootTimes(): number[] {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) || "[]");
    return Array.isArray(raw) ? raw.filter((n): n is number => typeof n === "number" && n > 0).slice(-KEPT) : [];
  } catch {
    return [];
  }
}

export function rememberBootTime(ms: number): void {
  if (!(ms > 0)) return;
  try {
    localStorage.setItem(KEY, JSON.stringify([...readBootTimes(), Math.round(ms)].slice(-KEPT)));
  } catch {
    /* private mode / quota: a start is still a start, it is just not remembered */
  }
}

/** The usual start on this computer: the median of the ones remembered, once there are enough. */
export function usualBootMs(times: readonly number[]): number | null {
  if (times.length < MIN_SAMPLES) return null;
  const sorted = [...times].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : Math.round((sorted[mid - 1] + sorted[mid]) / 2);
}

/** Whether a start that has run `elapsedMs` is slow: past the floor, and past twice the usual
 *  where there is one. */
export function isSlow(elapsedMs: number, times: readonly number[]): boolean {
  const usual = usualBootMs(times);
  return elapsedMs > Math.max(SLOW_FLOOR_MS, usual === null ? 0 : usual * 2);
}

/** Seconds as the boot screen says them: one decimal under ten, whole above. */
export function seconds(ms: number): string {
  const s = Math.max(0, ms) / 1000;
  return s < 10 ? `${s.toFixed(1)} s` : `${Math.round(s)} s`;
}
