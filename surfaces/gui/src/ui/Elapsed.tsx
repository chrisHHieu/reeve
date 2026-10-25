// A duration that ticks while the thing it measures is still running.
//
// Every tool row in the transcript used to show its duration only after it settled, so the
// one moment a reader actually wants the number — a command that has been quiet for a while,
// a fetch that might be stuck — was the one moment it was missing. The tick is 200ms: fast
// enough that the tenths digit looks alive, slow enough that a running row is not a repaint
// loop. It stops the instant `running` goes false, and the parent's own `durationMs` takes
// over then (a measured value beats a wall clock).

import { useEffect, useState } from "react";

/** `1.2s` under ten seconds, then whole seconds, then `2m 03s`. */
export function formatElapsed(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "";
  if (seconds < 10) return `${seconds.toFixed(1)}s`;
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds % 60);
  return `${minutes}m ${String(rest).padStart(2, "0")}s`;
}

/**
 * The `Locale.duration` (`tui/src/util/locale.ts`), ported verbatim: `123ms` under
 * a second, `12.4s` under a minute, then `1m 3s` / `2h 5m` / `3d 4h`.
 *
 * A separate formatter from `formatElapsed` on purpose: `formatElapsed` is the row's
 * ticking wall clock (one decimal under ten seconds so the digit looks alive), while this
 * is a measured span printed once when the work settled — its own vocabulary, and
 * the vocabulary the settled task line is specified in.
 */
export function formatDuration(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return "";
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  if (ms < 3_600_000) {
    const minutes = Math.floor(ms / 60_000);
    const seconds = Math.floor((ms % 60_000) / 1000);
    return `${minutes}m ${seconds}s`;
  }
  if (ms < 86_400_000) {
    const hours = Math.floor(ms / 3_600_000);
    const minutes = Math.floor((ms % 3_600_000) / 60_000);
    return `${hours}h ${minutes}m`;
  }
  const days = Math.floor(ms / 86_400_000);
  const hours = Math.floor((ms % 86_400_000) / 3_600_000);
  return `${days}d ${hours}h`;
}

export function Elapsed({
  from,
  running = true,
  className,
  after = 0,
}: {
  /** Epoch milliseconds the work started at. */
  from: number;
  running?: boolean;
  className?: string;
  /** Nothing is drawn before this many seconds: a call that returns at once never
   *  flashes a clock. */
  after?: number;
}) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!running) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 200);
    return () => window.clearInterval(timer);
  }, [running, from]);
  const seconds = Math.max(0, (now - from) / 1000);
  if (seconds < after) return null;
  return (
    <span
      className={className ? `ui-elapsed ${className}` : "ui-elapsed"}
      data-testid="elapsed"
      data-running={running || undefined}
    >
      {formatElapsed(seconds)}
    </span>
  );
}
