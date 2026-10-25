// Number formatting and range helpers for the Metrics tab. Pure functions: no React.

import type { OpsRange } from "../../api";

export function compact(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return "—";
  if (n >= 1_000_000_000) return `${(n / 1_000_000_000).toFixed(2)}B`;
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 10_000) return `${Math.round(n / 1000)}K`;
  if (n >= 1_000) return `${(n / 1000).toFixed(1)}K`;
  return String(n);
}

export function ms(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  if (value >= 60_000) {
    const m = Math.floor(value / 60_000);
    const s = Math.round((value % 60_000) / 1000);
    return `${m}m ${s}s`;
  }
  if (value >= 1000) return `${(value / 1000).toFixed(1)}s`;
  return `${Math.round(value)}ms`;
}

/** `value` is a 0..1 ratio. */
export function pct(value: number | null | undefined, digits = 1): string {
  if (value == null || !Number.isFinite(value)) return "—";
  return `${(value * 100).toFixed(digits)}%`;
}

export function share(part: number, whole: number): number {
  // A missing count (an older server, a row without a field) is zero, never NaN on screen.
  return whole > 0 && Number.isFinite(part) ? part / whole : 0;
}

export function dayLabel(day: string): string {
  return day.length >= 10 ? day.slice(5) : day;
}

/** "2026-10-07" → "Oct 7", in the reader's language: how a chart's axis names a day. */
export function shortDay(day: string): string {
  const [y, m, d] = day.split("-").map(Number);
  if (!y || !m || !d) return day;
  return new Date(y, m - 1, d).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/** Round axis stops from 0 to past `max`: 0, 5M, 10M… never 0, 5.5M, 11M. */
export function niceTicks(max: number, count = 4): number[] {
  if (!Number.isFinite(max) || max <= 0) return [0, 1];
  const raw = max / count;
  const power = 10 ** Math.floor(Math.log10(raw));
  const step = ([1, 2, 2.5, 5, 10].find((f) => f * power >= raw) ?? 10) * power;
  const top = Math.ceil(max / step) * step;
  const out: number[] = [];
  for (let value = 0; value <= top + step / 2; value += step) out.push(Number(value.toPrecision(12)));
  return out;
}

export function agoLabel(stamp: number): string {
  const s = Math.max(0, Math.round((Date.now() - stamp) / 1000));
  if (s < 5) return "just now";
  if (s < 60) return `${s}s ago`;
  return `${Math.round(s / 60)}m ago`;
}

const RANGE_DAYS: Partial<Record<OpsRange, number>> = { today: 1, "7d": 7, "30d": 30 };

/** The whole range, one slot per day, so a quiet day reads as the gap it is instead of a
 *  chart that only shows the days that had traffic. "all" returns the days it was given. */
export function rangeDays(range: OpsRange, seen: string[]): string[] {
  const span = RANGE_DAYS[range];
  const known = [...new Set(seen)].sort();
  if (span == null) return known.slice(-60);
  const out: string[] = [];
  const cursor = new Date();
  cursor.setHours(12, 0, 0, 0); // midday: immune to DST shifting the date backwards
  cursor.setDate(cursor.getDate() - (span - 1));
  for (let i = 0; i < span; i += 1) {
    out.push(
      `${cursor.getFullYear()}-${String(cursor.getMonth() + 1).padStart(2, "0")}-${String(
        cursor.getDate(),
      ).padStart(2, "0")}`,
    );
    cursor.setDate(cursor.getDate() + 1);
  }
  const extra = known.filter((day) => !out.includes(day)); // clock skew: shown, not dropped
  return [...extra, ...out].slice(-Math.max(span, known.length));
}

export type Tone = "ok" | "warn" | "bad" | "none";

/** Which side of a threshold a value sits. `null` (no data) stays neutral. */
export function band(
  value: number | null | undefined,
  good: number,
  poor: number,
  higherIsBetter = true,
): Tone {
  if (value == null) return "none";
  const ok = higherIsBetter ? value >= good : value <= good;
  const bad = higherIsBetter ? value < poor : value > poor;
  return ok ? "ok" : bad ? "bad" : "warn";
}

/** A provider's colour, stable across renders and palettes (tokens only). */
// Red last: on a usage chart it reads as "error", so it is only reached past seven providers.
const SERIES = [
  "bg-accent",
  "bg-tealInk",
  "bg-warnInk",
  "bg-ok",
  "bg-ink/40",
  "bg-accent/45",
  "bg-tealInk/45",
  "bg-danger/60",
];

export function seriesClass(index: number): string {
  return SERIES[index % SERIES.length];
}

/** The same order as `seriesClass`, as colours a chart can paint with (SVG fill/stroke take
 *  `var(--token)`, so every palette and both themes follow). */
const SERIES_COLOR = [
  "var(--accent)",
  "var(--teal-ink)",
  "var(--warn-ink)",
  "var(--ok)",
  "color-mix(in srgb, var(--ink) 40%, transparent)",
  "color-mix(in srgb, var(--accent) 45%, transparent)",
  "color-mix(in srgb, var(--teal-ink) 45%, transparent)",
  "color-mix(in srgb, var(--danger) 60%, transparent)",
];

export function seriesColor(index: number): string {
  return SERIES_COLOR[index % SERIES_COLOR.length];
}

/** Style A's colours (2026-10-14): one hue, from the accent down, mixed into the panel so a dark
 *  theme gets dark tints. Neighbours differ in lightness, not hue alone, and past the sixth part
 *  every part is the same neutral grey (a hover card names them). */
const TINT = [100, 74, 54, 38, 26, 17];

export function tintColor(index: number): string {
  if (index >= TINT.length) return "color-mix(in srgb, var(--ink) 14%, var(--panel))";
  return `color-mix(in srgb, var(--accent) ${TINT[index]}%, var(--panel))`;
}

/** How a number moved against the span before: `+18%`, `−6%`, `+3 pt` for a ratio. Null when
 *  there is no span before, or nothing in it to compare with. */
export function change(
  now: number | null | undefined,
  before: number | null | undefined,
  kind: "count" | "ratio" = "count",
): { text: string; up: boolean } | null {
  if (now == null || before == null || !Number.isFinite(now) || !Number.isFinite(before)) return null;
  if (kind === "ratio") {
    const points = Math.round((now - before) * 100);
    if (points === 0) return { text: "same", up: true };
    return { text: `${points > 0 ? "+" : "−"}${Math.abs(points)} pt`, up: points > 0 };
  }
  if (before <= 0) return null;
  const ratio = (now - before) / before;
  const rounded = Math.round(ratio * 100);
  if (rounded === 0) return { text: "same", up: true };
  return { text: `${rounded > 0 ? "+" : "−"}${Math.abs(rounded)}%`, up: rounded > 0 };
}

/** "opencode-go:deepseek-v4.1-flash" → "deepseek-v4.1-flash". */
export function modelName(full: string, provider: string | null | undefined): string {
  const prefix = provider ? `${provider}:` : "";
  return prefix && full.startsWith(prefix) ? full.slice(prefix.length) : full;
}

/** A tool error as a sentence: the JSON envelope and escaped dashes a result carries on
 *  the wire are noise in a list meant for reading. */
export function readableReason(reason: string): string {
  let text = (reason || "").trim();
  if (text.startsWith("{")) {
    try {
      const parsed = JSON.parse(text) as { error?: unknown };
      if (typeof parsed.error === "string") return parsed.error.replace(/^Error:\s*/, "");
    } catch {
      // clipped mid-string by the log's field cap: strip the envelope by hand below
    }
    const match = text.match(/^\{\s*"error"\s*:\s*"(.*)$/s);
    if (match) text = match[1].replace(/(?<!\\)"\s*,\s*"[a-z_]+"\s*:.*$/s, "");
  }
  return text
    .replace(/\\u2014/g, "—")
    .replace(/\\u2013/g, "–")
    .replace(/\\"/g, '"')
    .replace(/^Error:\s*/, "");
}
