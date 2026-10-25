// The blocks of a visual as the GUI draws them, read leniently: the same reader takes
// the harness's checked blocks from the result card and the model's own arguments while the call
// is still being written, where a block can be half there. Whatever is missing is left out, and
// each block draws what it has.

export interface Series {
  name: string;
  values: number[];
}

export interface ChartSpec {
  type: "chart";
  kind: "bar" | "line" | "area" | "pie";
  title?: string;
  unit?: string;
  x_label?: string;
  y_label?: string;
  labels: string[];
  series: Series[];
  /** The file the harness read the series from; absent for inline data. */
  source?: { file: string; rows?: number };
}

export interface TableSpec {
  type: "table";
  title?: string;
  columns: string[];
  rows: string[][];
  source?: { file: string; rows?: number };
}

export interface MetricsSpec {
  type: "metrics";
  items: { label: string; value: string | number; unit?: string; delta?: string; note?: string; source?: string }[];
}

export interface CompareSpec {
  type: "compare";
  title?: string;
  options: { name: string; note?: string; highlight?: boolean }[];
  rows: { label: string; values: string[] }[];
}

export interface WhatIfInput {
  name: string;
  label: string;
  min: number;
  max: number;
  step: number;
  value: number;
  unit?: string;
}

export interface WhatIfSpec {
  type: "what_if";
  title?: string;
  inputs: WhatIfInput[];
  values: { name: string; formula: string | number }[];
  outputs: { label: string; formula: string | number; unit?: string; decimals?: number }[];
  highlight?: "min" | "max";
  chart?: {
    kind: "line" | "area" | "bar";
    unit?: string;
    x: { name: string; from: string | number; to: string | number; step: string | number; label?: string };
    series: { name: string; formula: string | number }[];
  };
}

export interface FormField {
  name: string;
  label: string;
  kind: "select" | "radio" | "checkbox" | "text" | "number" | "textarea";
  options?: string[];
  value?: unknown;
  placeholder?: string;
  required?: boolean;
}

export interface FormSpec {
  type: "form";
  title?: string;
  fields: FormField[];
  submit: string;
}

export interface TimelineItem {
  label: string;
  /** ISO dates (`2026-10-09`), as the harness wrote them. */
  start: string;
  end?: string;
  group?: string;
  note?: string;
  done?: boolean;
}

export interface TimelineSpec {
  type: "timeline";
  title?: string;
  items: TimelineItem[];
  source?: { file: string; rows?: number };
}

export interface MapSpec {
  type: "map";
  title?: string;
  unit?: string;
  /** Countries by ISO 3166 alpha-3 code, with the name the model gave each. */
  regions: { id: string; name: string; value: number }[];
  points: { lat: number; lon: number; label: string; value?: number }[];
  source?: { file: string; rows?: number };
}

/** A block whose file the harness has not read yet: the call is still running. */
export interface PendingSpec {
  type: "pending";
  of: string;
  title?: string;
  file?: string;
}

export type VisualBlock =
  | ChartSpec
  | TableSpec
  | MetricsSpec
  | CompareSpec
  | WhatIfSpec
  | FormSpec
  | TimelineSpec
  | MapSpec
  | PendingSpec;

type Rec = Record<string, unknown>;
const rec = (v: unknown): Rec | null => (v && typeof v === "object" && !Array.isArray(v) ? (v as Rec) : null);
const str = (v: unknown): string | undefined =>
  typeof v === "string" ? v : typeof v === "number" && Number.isFinite(v) ? String(v) : undefined;
const num = (v: unknown): number | undefined => {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string" && v.trim() !== "" && Number.isFinite(Number(v))) return Number(v);
  return undefined;
};
const arr = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);
const formula = (v: unknown): string | number | undefined => (typeof v === "number" ? v : str(v));

function sourceOf(v: unknown): { file: string; rows?: number } | undefined {
  const r = rec(v);
  const file = r ? str(r.file) : undefined;
  return file ? { file, ...(num(r!.rows) !== undefined ? { rows: num(r!.rows) } : {}) } : undefined;
}

function chart(r: Rec): VisualBlock | null {
  const kind = (["bar", "line", "area", "pie"] as const).find((k) => k === r.kind) ?? "bar";
  const labels = arr(r.labels).map((l) => str(l) ?? "");
  const series: Series[] = arr(r.series)
    .map(rec)
    .filter((s): s is Rec => s !== null)
    .map((s, i) => ({ name: str(s.name) ?? `Series ${i + 1}`, values: arr(s.values).map((v) => num(v) ?? NaN) }));
  if (labels.length === 0 || series.length === 0) {
    // A file-backed chart has no labels until the harness has read the file.
    const file = sourceOf(r.source)?.file;
    return file ? { type: "pending", of: "chart", title: str(r.title), file } : null;
  }
  return {
    type: "chart",
    kind,
    title: str(r.title),
    unit: str(r.unit),
    x_label: str(r.x_label),
    y_label: str(r.y_label),
    labels,
    series,
    source: sourceOf(r.source)?.file && labels.length ? sourceOf(r.source) : undefined,
  };
}

function table(r: Rec): VisualBlock | null {
  const columns = arr(r.columns).map((c) => str(c) ?? "");
  if (columns.length === 0) {
    const file = sourceOf(r.source)?.file;
    return file ? { type: "pending", of: "table", title: str(r.title), file } : null;
  }
  const rows = arr(r.rows)
    .filter(Array.isArray)
    .map((row) => (row as unknown[]).map((cell) => str(cell) ?? ""));
  return { type: "table", title: str(r.title), columns, rows, source: sourceOf(r.source) };
}

function metrics(r: Rec): VisualBlock | null {
  const items = arr(r.items)
    .map(rec)
    .filter((i): i is Rec => i !== null && str(i.label) !== undefined)
    .map((i) => ({
      label: str(i.label)!,
      value: typeof i.value === "number" ? i.value : (str(i.value) ?? "…"),
      unit: str(i.unit),
      delta: str(i.delta),
      note: str(i.note),
      source: str(i.source) ?? sourceOf(i.source)?.file,
    }));
  return items.length ? { type: "metrics", items } : null;
}

function compare(r: Rec): VisualBlock | null {
  const options = arr(r.options)
    .map((o) => (typeof o === "string" ? { name: o } : rec(o)))
    .filter((o): o is Rec => o !== null && str(o.name) !== undefined)
    .map((o) => ({ name: str(o.name)!, note: str(o.note), highlight: o.highlight === true }));
  if (options.length === 0) return null;
  const rows = arr(r.rows)
    .map(rec)
    .filter((row): row is Rec => row !== null && str(row.label) !== undefined)
    .map((row) => ({ label: str(row.label)!, values: arr(row.values).map((v) => str(v) ?? "") }));
  return { type: "compare", title: str(r.title), options, rows };
}

function whatIf(r: Rec): VisualBlock | null {
  const inputs: WhatIfInput[] = [];
  for (const raw of arr(r.inputs)) {
    const i = rec(raw);
    const name = i ? str(i.name) : undefined;
    const min = i ? num(i.min) : undefined;
    const max = i ? num(i.max) : undefined;
    if (!i || !name || min === undefined || max === undefined || min >= max) continue;
    const value = num(i.value) ?? min;
    const step = num(i.step) ?? (max - min) / 100;
    inputs.push({ name, label: str(i.label) ?? name, min, max, step: step > 0 ? step : (max - min) / 100,
      value: Math.min(Math.max(value, min), max), unit: str(i.unit) });
  }
  if (inputs.length === 0) return null;
  const values = arr(r.values)
    .map(rec)
    .filter((v): v is Rec => v !== null && !!str(v.name) && formula(v.formula) !== undefined)
    .map((v) => ({ name: str(v.name)!, formula: formula(v.formula)! }));
  const outputs = arr(r.outputs)
    .map(rec)
    .filter((o): o is Rec => o !== null && !!str(o.label) && formula(o.formula) !== undefined)
    .map((o) => ({ label: str(o.label)!, formula: formula(o.formula)!, unit: str(o.unit), decimals: num(o.decimals) }));
  const c = rec(r.chart);
  const x = c ? rec(c.x) : null;
  const chartSpec =
    c && x && str(x.name) && formula(x.to) !== undefined
      ? {
          kind: (["line", "area", "bar"] as const).find((k) => k === c.kind) ?? ("line" as const),
          unit: str(c.unit),
          x: { name: str(x.name)!, from: formula(x.from) ?? 0, to: formula(x.to)!, step: formula(x.step) ?? 1, label: str(x.label) },
          series: arr(c.series)
            .map(rec)
            .filter((s): s is Rec => s !== null && formula(s.formula) !== undefined)
            .map((s, i) => ({ name: str(s.name) ?? `Series ${i + 1}`, formula: formula(s.formula)! })),
        }
      : undefined;
  return {
    type: "what_if",
    title: str(r.title),
    inputs,
    values,
    outputs,
    highlight: r.highlight === "min" || r.highlight === "max" ? r.highlight : undefined,
    chart: chartSpec && chartSpec.series.length ? chartSpec : undefined,
  };
}

function form(r: Rec): VisualBlock | null {
  const kinds = ["select", "radio", "checkbox", "text", "number", "textarea"] as const;
  const fields: FormField[] = arr(r.fields)
    .map(rec)
    .filter((f): f is Rec => f !== null && !!str(f.name))
    .map((f) => ({
      name: str(f.name)!,
      label: str(f.label) ?? str(f.name)!,
      kind: kinds.find((k) => k === f.kind) ?? "text",
      options: Array.isArray(f.options) ? f.options.map((o) => str(o) ?? "") : undefined,
      value: f.value,
      placeholder: str(f.placeholder),
      required: f.required === true,
    }));
  return fields.length ? { type: "form", title: str(r.title), fields, submit: str(r.submit) ?? "Send" } : null;
}

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

function timeline(r: Rec): VisualBlock | null {
  const items: TimelineItem[] = arr(r.items)
    .map(rec)
    .filter((i): i is Rec => i !== null && !!str(i.label) && ISO_DATE.test(str(i.start) ?? ""))
    .map((i) => ({
      label: str(i.label)!,
      start: str(i.start)!,
      end: ISO_DATE.test(str(i.end) ?? "") ? str(i.end) : undefined,
      group: str(i.group),
      note: str(i.note),
      done: i.done === true,
    }));
  if (items.length === 0) {
    // Items the model typed are checked by the harness before they are drawn, and a file's are
    // read by it: until the result, the block holds its place.
    return { type: "pending", of: "timeline", title: str(r.title), file: sourceOf(r.source)?.file };
  }
  return { type: "timeline", title: str(r.title), items, source: sourceOf(r.source) };
}

function map(r: Rec): VisualBlock | null {
  const regions = arr(r.regions)
    .map(rec)
    .filter((g): g is Rec => g !== null && /^[A-Z]{3}$/.test(str(g.id) ?? "") && num(g.value) !== undefined)
    .map((g) => ({ id: str(g.id)!, name: str(g.name) ?? str(g.id)!, value: num(g.value)! }));
  const points = arr(r.points)
    .map(rec)
    .filter((pt): pt is Rec => pt !== null && num(pt.lat) !== undefined && num(pt.lon) !== undefined)
    .map((pt) => ({ lat: num(pt.lat)!, lon: num(pt.lon)!, label: str(pt.label) ?? "", value: num(pt.value) }));
  if (regions.length === 0 && points.length === 0) {
    // A region is a country the harness has named by its code: until the result, a placeholder.
    return { type: "pending", of: "map", title: str(r.title), file: sourceOf(r.source)?.file };
  }
  return { type: "map", title: str(r.title), unit: str(r.unit), regions, points, source: sourceOf(r.source) };
}

const READERS: Record<string, (r: Rec) => VisualBlock | null> = {
  chart,
  table,
  metrics,
  compare,
  what_if: whatIf,
  form,
  timeline,
  map,
};

/** The blocks in `value` (a card's `blocks`, or a call's arguments), each read as far as it goes. */
export function readBlocks(value: unknown): VisualBlock[] {
  const holder = rec(value);
  const list = holder && "blocks" in holder ? holder.blocks : value;
  const blocks = Array.isArray(list) ? list : rec(list) ? [list] : [];
  const out: VisualBlock[] = [];
  for (const raw of blocks) {
    const r = rec(raw);
    const reader = r ? READERS[str(r.type) ?? ""] : undefined;
    const block = reader ? reader(r!) : null;
    if (block) out.push(block);
  }
  return out;
}
