// Numbers as a visual writes them: grouped the way its own text is written, a unit after, and
// "—" for a value that cannot be computed. Axis ticks are compact and land on round steps.
//
// The grouping follows the visual, not the machine: a Vietnamese answer on an English Windows
// read "13,495,889 đ" and "10.5%" beside prose that wrote "10,5%", and its axis said "4B" for
// 4 tỷ (the owner's report, 2026-10-09). A visual whose own words are Vietnamese is written
// "13.495.889 đ", "10,5%" and "4 tỷ"; any other keeps the reader's locale.

import { createContext, useContext } from "react";

/** A BCP 47 tag, or undefined for the reader's own locale. */
export type NumberLocale = string | undefined;

// Letters only Vietnamese uses among the scripts a visual is written in (đ, and the vowels with
// a tone or a hook): one is enough to say the visual's words are Vietnamese.
const VIETNAMESE = /[đĐăĂơƠưƯẠ-ỹ]/;

/** The locale a visual's numbers are written in, read from its own words. */
export function localeOf(value: unknown): NumberLocale {
  let text = "";
  try {
    text = JSON.stringify(value) ?? "";
  } catch {
    return undefined;
  }
  return VIETNAMESE.test(text) ? "vi-VN" : undefined;
}

/** The locale of the visual being drawn; set once by the frame around its blocks. */
export const VisualLocale = createContext<NumberLocale>(undefined);

export const useVisualLocale = () => useContext(VisualLocale);

export function formatValue(value: number, options: { unit?: string; decimals?: number; locale?: NumberLocale } = {}): string {
  if (!Number.isFinite(value)) return "—";
  const decimals = options.decimals ?? (Math.abs(value) >= 100 ? 0 : Math.abs(value) >= 1 ? 2 : 4);
  const text = value.toLocaleString(options.locale, { maximumFractionDigits: decimals, minimumFractionDigits: 0 });
  if (!options.unit) return text;
  return options.unit === "%" ? `${text}%` : `${text} ${options.unit}`;
}

// Vietnamese money is read in tỷ, triệu and nghìn; Intl's compact Vietnamese ("4 T", "250 N")
// is not what anyone writes.
const VI_SCALES: [number, string][] = [
  [1e9, " tỷ"],
  [1e6, " tr"],
  [1e3, "k"],
];

export function formatAxis(value: number, locale?: NumberLocale): string {
  if (!Number.isFinite(value)) return "";
  if (Math.abs(value) < 10_000) return value.toLocaleString(locale, { maximumFractionDigits: 2 });
  if (locale === "vi-VN") {
    const [scale, word] = VI_SCALES.find(([n]) => Math.abs(value) >= n)!;
    return `${(value / scale).toLocaleString(locale, { maximumFractionDigits: 1 })}${word}`;
  }
  return value.toLocaleString(locale, { notation: "compact", maximumFractionDigits: 1 });
}

/** A round step that splits `span` into about `parts` parts: 1, 2 or 5 times a power of ten,
 *  whichever is nearest, so an axis gets four to six ticks rather than two or ten. */
export function niceStep(span: number, parts = 4): number {
  const raw = Math.abs(span) / parts || 1;
  const power = 10 ** Math.floor(Math.log10(raw));
  const m = raw / power;
  return (m < 1.5 ? 1 : m < 3 ? 2 : m < 7 ? 5 : 10) * power;
}

/** The axis for values between `low` and `high`: round ends, ticks at round steps, zero kept. */
export function axisFor(low: number, high: number): { min: number; max: number; ticks: number[] } {
  let min = Math.min(0, low);
  let max = Math.max(0, high);
  if (!Number.isFinite(min) || !Number.isFinite(max)) return { min: 0, max: 1, ticks: [0, 1] };
  if (min === max) max = min + 1;
  const step = niceStep(max - min);
  min = Math.floor(min / step) * step;
  max = Math.ceil(max / step) * step;
  const ticks: number[] = [];
  for (let t = min; t <= max + step / 2; t += step) ticks.push(Number(t.toPrecision(12)));
  return { min, max, ticks };
}

/** Where two series cross, as fractional indexes between the points either side: the place a
 *  comparison changes its answer. Points where they are equal (both start at zero) are not
 *  crossings; only a change of which one is above is. */
export function crossings(a: readonly number[], b: readonly number[], n: number): number[] {
  const scale = Math.max(...[...a.slice(0, n), ...b.slice(0, n)].filter(Number.isFinite).map(Math.abs), 0);
  const tolerance = scale * 1e-9;
  const out: number[] = [];
  let last: { at: number; gap: number } | null = null;
  for (let i = 0; i < n; i++) {
    const gap = a[i] - b[i];
    if (!Number.isFinite(gap) || Math.abs(gap) <= tolerance) continue;
    if (last && Math.sign(gap) !== Math.sign(last.gap)) {
      out.push(last.at + (last.gap / (last.gap - gap)) * (i - last.at));
    }
    last = { at: i, gap };
  }
  return out;
}

export const reducedMotion = () =>
  typeof window !== "undefined" &&
  typeof window.matchMedia === "function" &&
  window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/** The colour of the i-th series, group or slice: ten hues from the theme's own roles (visual.css). */
export const vizColor = (i: number) => `var(--viz-${(i % 10) + 1})`;
