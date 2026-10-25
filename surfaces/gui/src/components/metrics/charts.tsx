// The Metrics page's charts, painted from the app's tokens (`var(--accent)` and kin), so every
// palette and both themes follow.
//
// 2026-10-14 (owner: the columns read old-fashioned; style A of the approved canvas): a day is a
// soft column on a faint track, its parts parted by a 2px gap, one hue from the accent down, and
// the pointer dims every other day instead of laying a band over this one. The columns are HTML,
// not SVG: a stretched SVG turns round corners into ellipses. A lone outlier no longer flattens the
// rest: the axis stops at the next tallest day and the outlier carries its value on a pill.
//
// Motion is the app's and compositor-only: a column rises once (transform), the dimming is opacity,
// and a refetch changes heights in place (a column regrowing every step would be a number bump).

import { useState, type ComponentProps, type MouseEvent, type ReactNode } from "react";
import { Area, AreaChart, CartesianGrid, ReferenceLine, Tooltip, XAxis, YAxis } from "recharts";
import { niceTicks } from "./format";

const AXIS = { fontSize: 10.5, fill: "var(--faint)" };
const GRID = "color-mix(in srgb, var(--ink) 6%, transparent)";
const MOTION = { isAnimationActive: true, animationDuration: 240, animationEasing: "ease-out" as const };

/** A week in a line: the number's own days, no axes. */
export function Sparkline({
  values,
  color = "var(--accent)",
  height = 34,
  domain,
}: {
  values: (number | null)[];
  color?: string;
  height?: number;
  domain?: ComponentProps<typeof YAxis>["domain"];
}) {
  const data = values.map((value, index) => ({ index, value }));
  if (data.filter((d) => d.value != null).length < 2) return <div style={{ height }} aria-hidden />;
  return (
    <div aria-hidden data-testid="sparkline">
      <AreaChart responsive width="100%" height={height} data={data} margin={{ top: 3, right: 3, bottom: 1, left: 3 }} accessibilityLayer={false}>
        <YAxis hide domain={domain ?? ["dataMin", "dataMax"]} />
        <Area type="monotone" dataKey="value" stroke={color} strokeWidth={1.75} fill={color} fillOpacity={0.1} connectNulls dot={false} activeDot={false} {...MOTION} />
      </AreaChart>
    </div>
  );
}

export interface Series {
  key: string;
  label: string;
  color: string;
}

/** A day that a harness change went live, drawn as a dashed mark (the ledger's `shipped`). */
export interface ShipMark {
  day: string;
  adrs: string[];
}

function shipLabel(adrs: string[]): string {
  if (adrs.length === 0) return "a change";
  return adrs.length === 1 ? `ADR ${adrs[0]}` : `${adrs.length} ADRs`;
}

/** The ship days whose mark gets a label: one at least `gap` days after the last labelled one, so
 *  a busy week reads as dashes under a few labels instead of labels written over each other. The
 *  hover card names every day's ADRs either way. */
function labelled(ships: ShipMark[], days: string[], gap: number): Set<string> {
  const out = new Set<string>();
  let last = -Infinity;
  for (const ship of ships) {
    const at = days.indexOf(ship.day);
    if (at < 0 || at - last < gap) continue;
    out.add(ship.day);
    last = at;
  }
  return out;
}

/** The hover card every chart here uses: the day, then each series with its value, then any
 *  extra lines the caller adds for that day. */
function Card({
  title,
  total,
  rows,
  extra,
}: {
  title: string;
  total?: string;
  rows: { label: string; color: string; value: string }[];
  extra?: ReactNode;
}) {
  return (
    <div className="min-w-[200px] rounded-xl border border-line bg-panel px-3.5 py-3 text-[12px] shadow-[var(--elev-3)]" data-testid="chart-card">
      <div className="mb-2 flex items-baseline justify-between gap-4">
        <span className="text-muted">{title}</span>
        {total && <b className="text-[13px] font-semibold tabular-nums text-ink">{total}</b>}
      </div>
      {rows.map((row) => (
        <div key={row.label} className="flex items-center gap-2 leading-[22px] text-muted">
          <i className="inline-block h-2 w-2 shrink-0 rounded-full" style={{ background: row.color }} />
          <span className="min-w-0 flex-1 truncate">{row.label}</span>
          <b className="font-medium tabular-nums text-ink">{row.value}</b>
        </div>
      ))}
      {extra}
    </div>
  );
}

function ShipLines({ adrs }: { adrs: string[] }) {
  return (
    <div className="mt-2 border-t border-line pt-2">
      <div className="text-muted">Shipped this day</div>
      <div className="font-mono text-[11.5px] leading-5 text-ink">{adrs.length ? adrs.map((a) => `ADR ${a}`).join(" · ") : "a change with no ADR"}</div>
    </div>
  );
}

/** Where the axis of a column chart stops: at the tallest day, unless that day stands alone (more
 *  than 1.6× the next), where it stops at the next and the tallest is drawn to the top with its
 *  value on a pill. */
function columnTop(totals: number[]): { top: number; capped: number } {
  const sorted = [...totals].sort((a, b) => b - a);
  const [first = 0, second = 0] = sorted;
  if (second > 0 && first > second * 1.6) return { top: second, capped: first };
  return { top: first, capped: Infinity };
}

/** Stacked soft columns, one per day, with an axis, a hover card and the days a change shipped. */
export function StackedDays({
  data,
  series,
  format,
  dayLabel,
  height = 250,
  extra,
  ships = [],
}: {
  data: Record<string, number | string>[];
  series: Series[];
  format: (value: number) => string;
  dayLabel: (day: string) => string;
  height?: number;
  /** More lines for a day's hover card (its cache hit, its calls). */
  extra?: (day: string) => ReactNode;
  ships?: ShipMark[];
}) {
  const [hover, setHover] = useState<number | null>(null);
  const totals = data.map((row) => series.reduce((total, s) => total + Number(row[s.key] ?? 0), 0));
  const { top, capped } = columnTop(totals);
  const ticks = niceTicks(top);
  const max = ticks[ticks.length - 1] || 1;
  const plot = height - 26;
  const px = (value: number) => (Math.min(value, max) / max) * plot;
  const n = data.length;
  const shipAt = new Map(ships.map((s) => [s.day, s.adrs]));
  const labelEvery = Math.max(1, Math.ceil(n / 7));
  const pick = (event: MouseEvent<HTMLDivElement>) => {
    const box = event.currentTarget.getBoundingClientRect();
    const at = Math.floor(((event.clientX - box.left) / box.width) * n);
    setHover(Math.max(0, Math.min(n - 1, at)));
  };
  const hovered = hover == null ? null : data[hover];
  const left = hover == null ? 0 : ((hover + 0.5) / n) * 100;
  return (
    <div className="relative pl-11 pt-2" data-testid="soft-columns">
      {ticks.map((tick) => (
        <span key={tick} className="absolute left-0 w-9 text-right text-[10.5px] tabular-nums text-faint" style={{ top: plot - px(tick) + 1 }}>
          {format(tick)}
        </span>
      ))}
      <div className="relative" style={{ height: plot }}>
        {ticks.map((tick) => (
          <i key={tick} className="absolute inset-x-0 h-px" style={{ top: plot - px(tick), background: tick ? GRID : "color-mix(in srgb, var(--ink) 12%, transparent)" }} />
        ))}
        <div className="absolute inset-0 flex items-end gap-[5px]" onMouseMove={pick} onMouseLeave={() => setHover(null)}>
          {data.map((row, index) => {
            const day = String(row.day);
            const total = totals[index];
            const tall = total > 0 ? Math.max(3, px(total)) : 0;
            const parts = series.filter((s) => Number(row[s.key] ?? 0) > 0);
            return (
              <div
                key={day}
                className="ops-col relative flex h-full min-w-0 flex-1 items-end"
                style={{ opacity: hover == null || hover === index ? 1 : 0.34 }}
                data-testid="soft-column"
              >
                <i className="absolute inset-0 rounded-[7px]" style={{ background: "color-mix(in srgb, var(--ink) 3.5%, transparent)" }} />
                {shipAt.has(day) && (
                  <i className="absolute inset-y-0 left-1/2 w-0 border-l border-dashed" style={{ borderColor: "color-mix(in srgb, var(--ink) 30%, transparent)" }} data-testid="ship-mark" />
                )}
                <div className="ops-col-rise relative flex w-full flex-col-reverse gap-[2px]" style={{ animationDelay: `${Math.min(index * 14, 420)}ms` }}>
                  {parts.map((s, at) => {
                    const share = Number(row[s.key]) / total;
                    return (
                      <i
                        key={s.key}
                        className="block w-full"
                        style={{
                          height: Math.max(2, share * tall - (parts.length > 1 ? 2 : 0)),
                          background: s.color,
                          borderRadius: at === parts.length - 1 ? "6px 6px 3px 3px" : 3,
                        }}
                      />
                    );
                  })}
                </div>
                {total >= capped && (
                  <span className="absolute -top-6 left-1/2 -translate-x-1/2 whitespace-nowrap rounded-full border border-line bg-panel px-1.5 text-[10.5px] tabular-nums text-ink" data-testid="column-cap">
                    ↑ {format(total)}
                  </span>
                )}
              </div>
            );
          })}
        </div>
        {hovered && (
          <div className="pointer-events-none absolute top-2 z-10" style={left > 64 ? { right: `calc(${100 - left}% + 14px)` } : { left: `calc(${left}% + 14px)` }}>
            <Card
              title={dayLabel(String(hovered.day))}
              total={format(totals[hover!])}
              rows={series
                .map((s) => ({ label: s.label, color: s.color, raw: Number(hovered[s.key] ?? 0) }))
                .filter((r) => r.raw > 0)
                .sort((a, b) => b.raw - a.raw)
                .map((r) => ({ label: r.label, color: r.color, value: format(r.raw) }))}
              extra={
                <>
                  {extra?.(String(hovered.day))}
                  {shipAt.has(String(hovered.day)) && <ShipLines adrs={shipAt.get(String(hovered.day)) ?? []} />}
                </>
              }
            />
          </div>
        )}
      </div>
      <div className="relative mt-2 h-4 text-[10.5px] tabular-nums text-faint">
        {data.map((row, index) =>
          index % labelEvery === 0 || index === n - 1 ? (
            <span key={String(row.day)} className="absolute -translate-x-1/2 whitespace-nowrap" style={{ left: `${((index + 0.5) / n) * 100}%` }}>
              {dayLabel(String(row.day))}
            </span>
          ) : null,
        )}
      </div>
    </div>
  );
}

/** One number per day as a soft area, with a hover card and the days a change shipped. */
export function DayArea({
  data,
  label,
  format,
  dayLabel,
  domain,
  ships = [],
  color = "var(--accent)",
  height = 250,
}: {
  data: { day: string; value: number | null }[];
  label: string;
  format: (value: number) => string;
  dayLabel: (day: string) => string;
  domain?: [number | ((min: number) => number), number | ((max: number) => number)];
  ships?: ShipMark[];
  color?: string;
  height?: number;
}) {
  const shipAt = new Map(ships.map((s) => [s.day, s.adrs]));
  const order = data.map((d) => d.day);
  const days = new Set(order);
  const named = labelled(ships, order, Math.max(2, Math.ceil(order.length / 6)));
  return (
    <div data-testid="day-area">
      <AreaChart responsive width="100%" height={height} data={data} margin={{ top: 22, right: 8, bottom: 0, left: 0 }}>
        <CartesianGrid vertical={false} stroke={GRID} />
        <XAxis dataKey="day" tickFormatter={dayLabel} tick={AXIS} axisLine={false} tickLine={false} minTickGap={24} />
        <YAxis domain={domain ?? ["auto", "auto"]} tickFormatter={(v: number) => format(v)} tick={AXIS} axisLine={false} tickLine={false} width={44} />
        {ships
          .filter((s) => days.has(s.day))
          .map((s) => (
            <ReferenceLine
              key={s.day}
              x={s.day}
              stroke="color-mix(in srgb, var(--ink) 30%, transparent)"
              strokeDasharray="2 3"
              label={named.has(s.day) ? { value: shipLabel(s.adrs), position: "top", fill: "var(--muted)", fontSize: 10.5 } : undefined}
            />
          ))}
        <Tooltip
          cursor={{ stroke: "color-mix(in srgb, var(--ink) 22%, transparent)", strokeWidth: 1 }}
          content={({ active, payload, label: day }) => {
            if (!active || !payload?.length || payload[0].payload.value == null) return null;
            const key = String(day ?? "");
            return (
              <Card
                title={dayLabel(key)}
                rows={[{ label, color, value: format(Number(payload[0].payload.value)) }]}
                extra={shipAt.has(key) ? <ShipLines adrs={shipAt.get(key) ?? []} /> : undefined}
              />
            );
          }}
        />
        <Area
          type="monotone"
          dataKey="value"
          stroke={color}
          strokeWidth={2}
          fill={color}
          fillOpacity={0.09}
          connectNulls
          dot={false}
          activeDot={{ r: 4.5, fill: color, stroke: "var(--panel)", strokeWidth: 3 }}
          {...MOTION}
        />
      </AreaChart>
    </div>
  );
}
