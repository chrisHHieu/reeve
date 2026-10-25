// Conditional formatting: a sheet's rules, and what they give a cell. The rules that can be
// judged from the cells' own values are: a comparison with a number or a text written in the
// rule, text that contains / begins / ends, blank and error cells, top and bottom N, above and
// below average, duplicates, colour scales and data bars. A rule that needs a formula worked
// out (or an icon set) is not judged; it is counted, so the sheet can say it is not drawn.
import { colourOf, type PartLook } from "./look";
import { readRange } from "./tables";
import { child, children, findAll, parseXml, type XmlNode } from "./xml";

export type Bar = { width: number; color: string };
export type Verdict = { part?: PartLook; bar?: Bar };
/** A cell as a rule sees it. */
export type Probe = { text: string; num: number | null; error: boolean };

export interface Rules {
  /** How many rules are not judged here. */
  unsupported: number;
  at(r: number, c: number, probe: Probe): Verdict | null;
}

type Range = [number, number, number, number];
type Stats = { sorted: number[]; mean: number; seen: Map<string, number> };
type Rule = { ranges: Range[]; priority: number; stop: boolean; judge(probe: Probe, stats: () => Stats | null): Verdict | null; kept?: Stats | null };

// A rule over more cells than this is not worked out: its sheet says so instead.
const CEILING = 400_000;

const NUMBER = /^[+-]?(\d+\.?\d*|\.\d+)(e[+-]?\d+)?$/i;

/** What is written in a rule as its operand: a number, or a text in quotes. Else nothing. */
function operand(formula: string | undefined): number | string | null {
  const text = (formula ?? "").trim();
  if (NUMBER.test(text)) return Number(text);
  if (text.length >= 2 && text.startsWith('"') && text.endsWith('"')) return text.slice(1, -1).replace(/""/g, '"');
  return null;
}

function compare(operator: string, probe: Probe, a: number | string, b: number | string | null): boolean | null {
  let side: number;
  let other = 0;
  if (typeof a === "number") {
    // Both seen in Excel itself: an empty cell is compared as 0 (it met "less than 4" and
    // "not equal to 4"), and a text is after every number ("n/a" met "greater than 5").
    const value = probe.num ?? (probe.text === "" ? 0 : null);
    if (value === null) {
      side = 1;
      other = 1;
    } else {
      side = value < a ? -1 : value > a ? 1 : 0;
      if (typeof b === "number") other = value < b ? -1 : value > b ? 1 : 0;
    }
  } else {
    const mine = probe.text.toLowerCase();
    const theirs = a.toLowerCase();
    side = mine < theirs ? -1 : mine > theirs ? 1 : 0;
    if (typeof b === "string") other = mine < b.toLowerCase() ? -1 : mine > b.toLowerCase() ? 1 : 0;
  }
  switch (operator) {
    case "lessThan":
      return side < 0;
    case "lessThanOrEqual":
      return side <= 0;
    case "greaterThan":
      return side > 0;
    case "greaterThanOrEqual":
      return side >= 0;
    case "equal":
      return side === 0;
    case "notEqual":
      return side !== 0;
    case "between":
      return b === null ? null : side >= 0 && other <= 0;
    case "notBetween":
      return b === null ? null : side < 0 || other > 0;
    default:
      return null;
  }
}

/** Where a stop of a colour scale or the end of a data bar is, among the values it is over. */
function mark(cfvo: XmlNode | undefined, stats: Stats, end: "min" | "max"): number | null {
  const { sorted } = stats;
  if (!sorted.length) return null;
  const lowest = sorted[0];
  const highest = sorted[sorted.length - 1];
  const type = cfvo?.attrs.type ?? end;
  const value = Number(cfvo?.attrs.val);
  if (type === "min" || type === "autoMin") return lowest;
  if (type === "max" || type === "autoMax") return highest;
  if (!Number.isFinite(value)) return null;
  if (type === "num") return value;
  if (type === "percent") return lowest + ((highest - lowest) * value) / 100;
  if (type === "percentile") {
    const at = ((sorted.length - 1) * value) / 100;
    const low = Math.floor(at);
    const high = Math.min(sorted.length - 1, low + 1);
    return sorted[low] + (sorted[high] - sorted[low]) * (at - low);
  }
  return null;
}

const blend = (from: string, to: string, t: number) => {
  const mix = (at: number) => {
    const a = parseInt(from.slice(at, at + 2), 16);
    const b = parseInt(to.slice(at, at + 2), 16);
    return Math.round(a + (b - a) * t)
      .toString(16)
      .padStart(2, "0");
  };
  return ("#" + mix(1) + mix(3) + mix(5)).toUpperCase();
};

function make(rule: XmlNode, ranges: Range[], parts: PartLook[], theme: readonly string[]): Rule | null {
  const { type, operator } = rule.attrs;
  const part = parts[Number(rule.attrs.dxfId)] as PartLook | undefined;
  const base = { ranges, priority: Number(rule.attrs.priority) || 0, stop: rule.attrs.stopIfTrue === "1" || rule.attrs.stopIfTrue === "true" };
  const when = (test: (probe: Probe, stats: () => Stats | null) => boolean | null): Rule | null =>
    part ? { ...base, judge: (probe, stats) => (test(probe, stats) ? { part } : null) } : null;
  const formulas = children(rule, "formula").map((formula) => formula.text);

  if (type === "cellIs") {
    const a = operand(formulas[0]);
    const b = formulas.length > 1 ? operand(formulas[1]) : null;
    if (a === null || (formulas.length > 1 && (b === null || typeof b !== typeof a))) return null;
    return when((probe) => compare(operator ?? "", probe, a, b));
  }
  const needle = (rule.attrs.text ?? "").toLowerCase();
  if (type === "containsText") return when((probe) => needle !== "" && probe.text.toLowerCase().includes(needle));
  if (type === "notContainsText") return when((probe) => !probe.text.toLowerCase().includes(needle));
  if (type === "beginsWith") return when((probe) => needle !== "" && probe.text.toLowerCase().startsWith(needle));
  if (type === "endsWith") return when((probe) => needle !== "" && probe.text.toLowerCase().endsWith(needle));
  if (type === "containsBlanks") return when((probe) => probe.text.trim() === "");
  if (type === "notContainsBlanks") return when((probe) => probe.text.trim() !== "");
  if (type === "containsErrors") return when((probe) => probe.error);
  if (type === "notContainsErrors") return when((probe) => !probe.error);
  if (type === "duplicateValues" || type === "uniqueValues") {
    const twice = type === "duplicateValues";
    return when((probe, stats) => {
      const found = stats();
      if (!found || probe.text === "") return null;
      return (found.seen.get(probe.text.toLowerCase()) ?? 0) > 1 === twice;
    });
  }
  if (type === "top10") {
    const rank = Math.max(1, Number(rule.attrs.rank ?? 10) || 10);
    const percent = rule.attrs.percent === "1";
    const bottom = rule.attrs.bottom === "1";
    return when((probe, stats) => {
      const found = stats();
      if (!found || probe.num === null || !found.sorted.length) return null;
      const count = Math.min(found.sorted.length, percent ? Math.max(1, Math.floor((found.sorted.length * rank) / 100)) : rank);
      return bottom ? probe.num <= found.sorted[count - 1] : probe.num >= found.sorted[found.sorted.length - count];
    });
  }
  if (type === "aboveAverage") {
    if (rule.attrs.stdDev) return null;
    const above = rule.attrs.aboveAverage !== "0";
    const equal = rule.attrs.equalAverage === "1";
    return when((probe, stats) => {
      const found = stats();
      if (!found || probe.num === null || !found.sorted.length) return null;
      return above ? (equal ? probe.num >= found.mean : probe.num > found.mean) : equal ? probe.num <= found.mean : probe.num < found.mean;
    });
  }
  if (type === "colorScale") {
    const scale = child(rule, "colorScale");
    const stops = children(scale, "cfvo");
    const colours = children(scale, "color").map((color) => colourOf(color, theme));
    if (stops.length < 2 || stops.length !== colours.length || colours.some((colour) => !colour)) return null;
    if (stops.some((stop) => stop.attrs.type === "formula")) return null;
    return {
      ...base,
      judge: (probe, stats) => {
        const found = stats();
        if (!found || probe.num === null) return null;
        const marks = stops.map((stop, at) => mark(stop, found, at === 0 ? "min" : "max"));
        if (marks.some((value) => value === null)) return null;
        const at = marks as number[];
        let fill = colours[colours.length - 1] as string;
        if (probe.num <= at[0]) fill = colours[0] as string;
        else {
          for (let stop = 1; stop < at.length; stop++) {
            if (probe.num <= at[stop]) {
              const span = at[stop] - at[stop - 1];
              fill = blend(colours[stop - 1] as string, colours[stop] as string, span > 0 ? (probe.num - at[stop - 1]) / span : 1);
              break;
            }
          }
        }
        return { part: { fill } };
      },
    };
  }
  if (type === "dataBar") {
    const bar = child(rule, "dataBar");
    const ends = children(bar, "cfvo");
    const color = colourOf(child(bar, "color"), theme);
    if (ends.length !== 2 || !color || ends.some((end) => end.attrs.type === "formula")) return null;
    const least = Number(bar?.attrs.minLength ?? 10);
    const most = Number(bar?.attrs.maxLength ?? 90);
    return {
      ...base,
      judge: (probe, stats) => {
        const found = stats();
        if (!found || probe.num === null) return null;
        const low = mark(ends[0], found, "min");
        const high = mark(ends[1], found, "max");
        if (low === null || high === null) return null;
        const share = high > low ? Math.min(1, Math.max(0, (probe.num - low) / (high - low))) : 1;
        return { bar: { width: Math.min(100, Math.max(0, least + share * (most - least))) / 100, color } };
      },
    };
  }
  return null;
}

/**
 * The rules of one sheet, from the XML after its cells. `valueAt` reads a cell, for the rules
 * that are about a cell's place among the others (a colour scale, a top ten).
 */
export function readRules(tail: string, parts: PartLook[], theme: readonly string[], valueAt: (r: number, c: number) => Probe | null): Rules {
  const rules: Rule[] = [];
  let unsupported = 0;
  const blocks = tail.match(/<(?:\w+:)?conditionalFormatting\b[\s\S]*?<\/(?:\w+:)?conditionalFormatting>/g) || [];
  for (const block of blocks) {
    const node = parseXml(block).kids[0];
    const found = findAll(node, "cfRule");
    const sqref = node?.attrs.sqref;
    if (!sqref) {
      // The newer form, kept beside the older one for a data bar: the older one is judged.
      // Anything else in it has no older form, and is not judged.
      unsupported += found.filter((rule) => rule.attrs.type !== "dataBar").length;
      continue;
    }
    const ranges = sqref
      .split(/\s+/)
      .map((ref) => readRange(ref))
      .filter((range): range is Range => !!range);
    for (const rule of found) {
      const made = ranges.length ? make(rule, ranges, parts, theme) : null;
      if (made) rules.push(made);
      else unsupported += 1;
    }
  }
  // The first rule in priority is the one whose word stands.
  rules.sort((a, b) => a.priority - b.priority);

  const statsOf = (rule: Rule): Stats | null => {
    if (rule.kept !== undefined) return rule.kept;
    let cells = 0;
    for (const [r0, r1, c0, c1] of rule.ranges) cells += (r1 - r0 + 1) * (c1 - c0 + 1);
    if (cells > CEILING) return (rule.kept = null);
    const sorted: number[] = [];
    const seen = new Map<string, number>();
    let sum = 0;
    for (const [r0, r1, c0, c1] of rule.ranges) {
      for (let r = r0; r <= r1; r++) {
        for (let c = c0; c <= c1; c++) {
          const probe = valueAt(r, c);
          if (!probe || probe.text === "") continue;
          const key = probe.text.toLowerCase();
          seen.set(key, (seen.get(key) ?? 0) + 1);
          if (probe.num !== null) {
            sorted.push(probe.num);
            sum += probe.num;
          }
        }
      }
    }
    sorted.sort((a, b) => a - b);
    return (rule.kept = { sorted, mean: sorted.length ? sum / sorted.length : 0, seen });
  };

  return {
    unsupported,
    at(r, c, probe) {
      let part: PartLook | undefined;
      let bar: Bar | undefined;
      let met = false;
      for (const rule of rules) {
        if (!rule.ranges.some(([r0, r1, c0, c1]) => r >= r0 && r <= r1 && c >= c0 && c <= c1)) continue;
        const said = rule.judge(probe, () => statsOf(rule));
        if (!said) continue;
        met = true;
        // An earlier rule's word is kept; a later one adds only what is not yet said.
        if (said.part) part = { ...said.part, ...part };
        bar ??= said.bar;
        if (rule.stop) break;
      }
      return met ? { part, bar } : null;
    },
  };
}
