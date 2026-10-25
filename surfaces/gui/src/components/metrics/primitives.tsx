// Building blocks of the Metrics tab: panels, the number cards, meters and the health-check
// cards. Tokens only (DESIGN.md); motion via the `ops-*` classes in styles.css, which stop
// under prefers-reduced-motion. The charts are in ./charts.tsx.

import type { ComponentProps, ReactNode } from "react";
import { Sparkline } from "./charts";
import { compact, type Tone } from "./format";

export const NUM = "tabular-nums";
export const TH = "text-left text-[10.5px] font-semibold uppercase tracking-[0.06em] text-faint";
export const TD = "border-b border-line/60 last:border-b-0";
export const CELL_NUM = `px-2 py-2 text-right font-mono text-[11.5px] ${NUM}`;

export function Panel({
  title,
  hint,
  action,
  className = "",
  children,
}: {
  title: string;
  hint?: string;
  action?: ReactNode;
  className?: string;
  children: ReactNode;
}) {
  return (
    <section className={`rounded-xl bg-panel shadow-[var(--elev-1)] ${className}`}>
      <header className="flex items-baseline gap-2 px-4 pb-2 pt-3.5">
        <h2 className="text-[13px] font-semibold tracking-[-0.01em]">{title}</h2>
        {hint && <span className="truncate text-[11px] text-faint">{hint}</span>}
        <span className="flex-1" />
        {action}
      </header>
      <div className="px-4 pb-4">{children}</div>
    </section>
  );
}

/** How a number moved against the span before. Green or red only where up or down is good or
 *  bad (`good` says which); a token total moving is neither, and stays neutral. */
export function Change({ text, up, good }: { text: string; up: boolean; good?: "up" | "down" }) {
  const tone =
    good == null || text === "same"
      ? "bg-paper text-muted"
      : (up ? good === "up" : good === "down")
        ? "bg-okSoft text-ok"
        : "bg-dangerSoft text-danger";
  return (
    <span className={`inline-flex h-5 items-center gap-1 rounded-full px-1.5 text-[11.5px] font-medium tabular-nums ${tone}`} title="against the same span just before" data-testid="kpi-change">
      {text === "same" ? "same" : <>{up ? "▲" : "▼"} {text.replace(/^[+−]/, "")}</>}
    </span>
  );
}

export function Kpi({
  label,
  value,
  detail,
  formula,
  change,
  trend,
  color = "var(--accent)",
  index = 0,
}: {
  label: string;
  value: string;
  detail?: string;
  formula: string;
  change?: ReactNode;
  /** The number's own days, oldest first. */
  trend?: (number | null)[];
  color?: string;
  index?: number;
}) {
  return (
    <div
      className="ops-kpi flex min-w-0 flex-col rounded-xl border border-line bg-panel px-4 pb-2.5 pt-3.5"
      style={{ animationDelay: `${index * 40}ms` }}
      title={`${label} — ${formula}`}
      data-testid="kpi"
    >
      <div className="flex items-center gap-2 text-[12px] text-muted">
        <span className="truncate">{label}</span>
        <span className="ml-auto">{change}</span>
      </div>
      <div className="mt-1 text-[24px] font-semibold leading-[30px] tracking-[-0.02em] tabular-nums">{value}</div>
      {detail && <div className="truncate text-[11.5px] text-faint">{detail}</div>}
      <div className="mt-2">{trend ? <Sparkline values={trend} color={color} /> : <div className="h-[34px]" />}</div>
    </div>
  );
}

/** Number cards in one row; wrapping on narrow panes. */
export function KpiStrip({ children, cols }: { children: ReactNode; cols: 3 | 4 | 5 | 6 }) {
  const grid = { 3: "grid-cols-3", 4: "grid-cols-4", 5: "grid-cols-5", 6: "grid-cols-6" }[cols];
  return <section className={`grid ${grid} gap-3 max-[1100px]:grid-cols-3 max-[700px]:grid-cols-2`}>{children}</section>;
}

export function Legend({ items }: { items: { label: string; className?: string; color?: string; value?: string }[] }) {
  return (
    <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-[12px] text-muted">
      {items.map((item) => (
        <span key={item.label} className="inline-flex items-center gap-1.5">
          <span className={`inline-block h-2.5 w-2.5 rounded-[3px] ${item.className ?? ""}`} style={item.color ? { background: item.color } : undefined} />
          {item.label}
          {item.value && <span className={`font-medium text-ink ${NUM}`}>{item.value}</span>}
        </span>
      ))}
    </div>
  );
}

export function Meter({
  label,
  value,
  total,
  tone = "accent",
  display,
}: {
  label: string;
  value: number;
  total: number;
  tone?: "accent" | "warn" | "bad" | "ok" | "muted";
  display?: string;
}) {
  const width = total > 0 && value > 0 ? Math.max(2, Math.round((value * 100) / total)) : 0;
  const fill = {
    accent: "bg-accent/70",
    warn: "bg-warnInk/70",
    bad: "bg-danger/70",
    ok: "bg-ok/70",
    muted: "bg-ink/25",
  }[tone];
  return (
    <div className="flex items-center gap-3 py-[5px] text-[12px]">
      <span className="w-[132px] shrink-0 truncate text-muted" title={label}>
        {label}
      </span>
      <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-paper">
        <i className={`block h-full rounded-full ${fill}`} style={{ width: `${width}%` }} />
      </span>
      <span className={`w-[64px] shrink-0 text-right font-mono text-[11.5px] ${NUM}`}>
        {display ?? compact(value)}
      </span>
    </div>
  );
}

const CHECK_PILL: Record<Tone, string> = {
  ok: "bg-okSoft text-ok",
  warn: "bg-warnSoft text-warnInk",
  bad: "bg-dangerSoft text-danger",
  none: "bg-paper text-muted",
};
const CHECK_LINE: Record<Tone, string> = {
  ok: "var(--ok)",
  warn: "var(--warn-ink)",
  bad: "var(--danger)",
  none: "var(--faint)",
};
const CHECK_WORD: Record<Tone, string> = { ok: "Healthy", warn: "Watch", bad: "Failing", none: "No data" };

/** One harness check: its status in words, what it measures, the value, the target it is held
 *  to, and its days in this range. */
export function HealthCheck({
  title,
  meaning,
  value,
  target,
  tone,
  trend,
  domain,
}: {
  title: string;
  meaning: string;
  value: string;
  target: string;
  tone: Tone;
  trend?: (number | null)[];
  domain?: ComponentProps<typeof Sparkline>["domain"];
}) {
  return (
    <div className="flex min-w-0 flex-col gap-1.5 rounded-xl border border-line bg-panel px-4 pb-3 pt-3.5" data-testid="health-check" data-tone={tone}>
      <div className="flex items-center gap-2">
        <span className={`inline-flex h-[21px] shrink-0 items-center gap-1.5 rounded-full px-2 text-[11.5px] font-medium ${CHECK_PILL[tone]}`}>
          <i className="h-1.5 w-1.5 rounded-full bg-current" aria-hidden />
          {CHECK_WORD[tone]}
        </span>
        <span className="truncate text-[13px] font-medium">{title}</span>
      </div>
      <div className="min-h-[34px] text-[12px] leading-[17px] text-muted">{meaning}</div>
      <div className="flex items-end justify-between gap-3">
        <div className="shrink-0">
          <div className="text-[22px] font-semibold tracking-[-0.02em] tabular-nums">{value}</div>
          <div className="text-[11.5px] text-faint">target {target}</div>
        </div>
        <div className="w-[120px] min-w-0">{trend ? <Sparkline values={trend} color={CHECK_LINE[tone]} height={36} domain={domain} /> : null}</div>
      </div>
    </div>
  );
}

export function EmptyNote({ children }: { children: ReactNode }) {
  return <p className="py-6 text-center text-[12px] text-faint">{children}</p>;
}
