// Usage: where the tokens went — by provider, by model, by day. Everything here reads the
// durable rollup (`model_usage`), so a deleted conversation's spend is still counted, and
// each call sits on the day it ran. Tokens, never dollars: the desktop app does not show
// model prices (docs/harness-capability-matrix.md).
//
// Redrawn 2026-10-13 as charts with axes and a hover card, each number with its week and its
// change against the span before (owner: "chart đẹp hơn đàng hoàng"); 2026-10-14 in the approved
// canvas's style A: soft columns in one hue, the models and their cache hit as lists.

import { Fragment, useState } from "react";
import type { OpsBreakdown, OpsMetrics, OpsRange } from "../../api";
import { StackedDays, type Series } from "./charts";
import { change, compact, shortDay, modelName, pct, rangeDays, share, tintColor } from "./format";
import { CELL_NUM, Change, EmptyNote, Kpi, KpiStrip, Legend, Meter, NUM, Panel, TD, TH } from "./primitives";

interface ProviderGroup {
  provider: string;
  models: OpsBreakdown[];
  calls: number;
  fresh: number;
  cacheRead: number;
  cacheWrite: number;
  output: number;
  total: number;
}

function group(rows: OpsBreakdown[]): ProviderGroup[] {
  const map = new Map<string, ProviderGroup>();
  for (const row of rows) {
    const provider = row.provider || "unknown";
    const entry =
      map.get(provider) ??
      { provider, models: [], calls: 0, fresh: 0, cacheRead: 0, cacheWrite: 0, output: 0, total: 0 };
    entry.models.push(row);
    entry.calls += row.llm_calls;
    entry.fresh += row.tokens_in;
    entry.cacheRead += row.cache_read;
    entry.cacheWrite += row.cache_write;
    entry.output += row.tokens_out ?? 0;
    entry.total += row.tokens;
    map.set(provider, entry);
  }
  return [...map.values()].sort((a, b) => b.total - a.total);
}

const hit = (read: number, fresh: number, write: number) =>
  fresh + read + write > 0 ? read / (fresh + read + write) : null;

const PURPOSE: Record<string, string> = {
  title: "Session titles",
  memory: "Memory passes",
  decision: "Decision checks",
  commit_message: "Commit messages",
};

const KINDS: Series[] = [
  { key: "fresh", label: "Fresh input", color: tintColor(0) },
  { key: "cacheRead", label: "Cache read", color: tintColor(2) },
  { key: "cacheWrite", label: "Cache write", color: tintColor(4) },
  { key: "output", label: "Output", color: "var(--teal-ink)" },
];

/** The cache hit a route is held to (DESIGN: the 80% line the chart drew). */
const HIT_TARGET = 0.8;

export function UsageTab({ data, range }: { data: OpsMetrics; range: OpsRange }) {
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const [by, setBy] = useState<"provider" | "kind">("provider");
  const providers = group(data.by_model ?? []);
  const colour = new Map(providers.map((p, index) => [p.provider, tintColor(index)]));
  const sum = providers.reduce(
    (acc, p) => ({
      calls: acc.calls + p.calls,
      fresh: acc.fresh + p.fresh,
      cacheRead: acc.cacheRead + p.cacheRead,
      cacheWrite: acc.cacheWrite + p.cacheWrite,
      output: acc.output + p.output,
      total: acc.total + p.total,
    }),
    { calls: 0, fresh: 0, cacheRead: 0, cacheWrite: 0, output: 0, total: 0 },
  );
  const before = data.previous ?? null;

  const usage = data.usage_daily ?? [];
  const days = rangeDays(range, usage.map((row) => row.day));
  const perDay = days.map((day) => {
    const rows = usage.filter((row) => row.day === day);
    const add = (key: "tokens_in" | "tokens_out" | "cache_read" | "cache_write" | "tokens" | "calls") =>
      rows.reduce((total, row) => total + (row[key] ?? 0), 0);
    const providerTokens = Object.fromEntries(
      providers.map((p) => [p.provider, rows.filter((row) => (row.provider || "unknown") === p.provider).reduce((t, row) => t + row.tokens, 0)]),
    );
    return {
      day,
      fresh: add("tokens_in"),
      cacheRead: add("cache_read"),
      cacheWrite: add("cache_write"),
      output: add("tokens_out"),
      tokens: add("tokens"),
      calls: add("calls"),
      providerTokens,
      providerHit: Object.fromEntries(
        providers.map((p) => {
          const mine = rows.filter((row) => (row.provider || "unknown") === p.provider);
          const value = hit(
            mine.reduce((t, row) => t + row.cache_read, 0),
            mine.reduce((t, row) => t + row.tokens_in, 0),
            mine.reduce((t, row) => t + row.cache_write, 0),
          );
          return [p.provider, value];
        }),
      ),
    };
  });
  const dayHit = (d: (typeof perDay)[number]) => hit(d.cacheRead, d.fresh, d.cacheWrite);

  const providerSeries: Series[] = providers.map((p) => ({ key: `p:${p.provider}`, label: p.provider, color: colour.get(p.provider)! }));
  const bars = perDay.map((d) => ({
    day: d.day,
    ...(by === "kind"
      ? { fresh: d.fresh, cacheRead: d.cacheRead, cacheWrite: d.cacheWrite, output: d.output }
      : Object.fromEntries(providers.map((p) => [`p:${p.provider}`, d.providerTokens[p.provider] ?? 0]))),
  }));
  const models = [...(data.by_model ?? [])].sort((a, b) => b.tokens - a.tokens);
  const topModels = models.slice(0, 6);
  const rest = models.slice(6);
  const modelList = [
    ...topModels.map((row) => ({ name: modelName(row.name, row.provider), full: row.name, tokens: row.tokens })),
    ...(rest.length ? [{ name: `${rest.length} other model${rest.length === 1 ? "" : "s"}`, full: "", tokens: rest.reduce((t, row) => t + row.tokens, 0) }] : []),
  ];
  const mostTokens = Math.max(1, ...modelList.map((row) => row.tokens));
  const hitRows = models.filter((row) => row.cache_hit != null && row.tokens > 0).slice(0, 8);

  const background = data.background ?? [];
  const backgroundTotal = background.reduce((total, row) => total + row.tokens, 0);
  const totalChange = change(sum.total, before?.tokens);
  const hitChange = change(hit(sum.cacheRead, sum.fresh, sum.cacheWrite), before?.cache_hit, "ratio");
  const freshChange = change(sum.fresh, before?.tokens_in);
  const outputChange = change(sum.output, before?.tokens_out);
  const leader = models[0];

  return (
    <div className="flex flex-col gap-4">
      <KpiStrip cols={5}>
        <Kpi index={0} label="Total tokens" value={compact(sum.total)} detail={`${sum.calls.toLocaleString()} model calls`}
          formula="fresh input + cache read + cache write + output, every provider"
          change={totalChange && <Change {...totalChange} />} trend={perDay.map((d) => d.tokens)} />
        <Kpi index={1} label="Cache hit" value={pct(hit(sum.cacheRead, sum.fresh, sum.cacheWrite), 0)}
          detail={`${compact(sum.cacheRead)} served from cache`}
          formula="prompt tokens served from the provider's cache ÷ all prompt tokens"
          change={hitChange && <Change {...hitChange} good="up" />} trend={perDay.map(dayHit)} color="var(--ok)" />
        <Kpi index={2} label="Fresh input" value={compact(sum.fresh)} detail={`${pct(share(sum.fresh, sum.total), 0)} of all tokens`}
          formula="prompt tokens the provider did NOT serve from cache"
          change={freshChange && <Change {...freshChange} />} trend={perDay.map((d) => d.fresh)} />
        <Kpi index={3} label="Output" value={compact(sum.output)} detail={`${pct(share(sum.output, sum.total), 1)} of all tokens`}
          formula="tokens the models generated, reasoning included where billed as output"
          change={outputChange && <Change {...outputChange} />} trend={perDay.map((d) => d.output)} color="var(--teal-ink)" />
        <Kpi index={4} label="Providers · models" value={`${providers.length} · ${models.length}`}
          detail={leader ? `${modelName(leader.name, leader.provider)} leads, ${pct(share(leader.tokens, sum.total), 0)}` : undefined}
          formula="distinct providers and models that answered in this range" />
      </KpiStrip>

      <div className="grid grid-cols-[minmax(0,1.9fr)_minmax(0,1fr)] gap-4 max-[1100px]:grid-cols-1">
        <Panel
          title="Tokens per day"
          hint="each call on the day it ran"
          action={
            <div className="flex gap-0.5 rounded-lg bg-paper p-0.5" role="group" aria-label="Split the days by">
              {(["provider", "kind"] as const).map((key) => (
                <button key={key} type="button" aria-pressed={by === key} data-testid={`usage-by-${key}`} onClick={() => setBy(key)}
                  className={"rounded-md px-2 py-0.5 text-[11.5px] transition-colors duration-150 " +
                    (by === key ? "bg-panel font-medium text-ink shadow-[var(--elev-1)]" : "text-muted hover:text-ink")}>
                  {key === "provider" ? "By provider" : "By kind"}
                </button>
              ))}
            </div>
          }
        >
          {usage.length === 0 ? (
            <EmptyNote>No model calls in this range.</EmptyNote>
          ) : (
            <div data-testid="usage-days">
              <StackedDays
                data={bars}
                series={by === "kind" ? KINDS : providerSeries}
                format={compact}
                dayLabel={shortDay}
                extra={(day) => {
                  const d = perDay.find((row) => row.day === day);
                  if (!d) return null;
                  return (
                    <div className="mt-1.5 border-t border-line pt-1.5 text-muted">
                      <div className="flex justify-between gap-4"><span>Cache hit</span><b className="font-medium tabular-nums text-ink">{pct(dayHit(d), 0)}</b></div>
                      <div className="flex justify-between gap-4"><span>Model calls</span><b className="font-medium tabular-nums text-ink">{d.calls.toLocaleString()}</b></div>
                    </div>
                  );
                }}
              />
              <Legend
                items={(by === "kind" ? KINDS : providerSeries).map((s) => ({
                  label: s.label,
                  color: s.color,
                  value: compact(
                    by === "kind"
                      ? { fresh: sum.fresh, cacheRead: sum.cacheRead, cacheWrite: sum.cacheWrite, output: sum.output }[s.key as "fresh"] ?? 0
                      : providers.find((p) => `p:${p.provider}` === s.key)?.total ?? 0,
                  ),
                }))}
              />
            </div>
          )}
        </Panel>

        <Panel title="By model" hint="tokens · share">
          {modelList.length === 0 ? (
            <EmptyNote>No usage recorded yet.</EmptyNote>
          ) : (
            <div className="-mx-1 flex flex-col" data-testid="model-mix">
              {modelList.map((row) => (
                <div key={row.name} className={`relative flex items-center gap-3 px-1 py-[7px] text-[12.5px] ${NUM}`} title={row.full || undefined}>
                  <i className="absolute inset-y-0.5 left-0 rounded-r-[4px]" style={{ width: `${(row.tokens / mostTokens) * 100}%`, background: "color-mix(in srgb, var(--accent) 8%, transparent)" }} />
                  <span className="relative min-w-0 flex-1 truncate font-mono text-[12px] text-ink">{row.name}</span>
                  <span className="relative font-medium text-ink">{compact(row.tokens)}</span>
                  <span className="relative w-11 text-right text-[11.5px] text-muted">{pct(share(row.tokens, sum.total), 1)}</span>
                </div>
              ))}
            </div>
          )}
        </Panel>
      </div>

      <div className="grid grid-cols-[minmax(0,1.9fr)_minmax(0,1fr)] gap-4 max-[1100px]:grid-cols-1">
        <Panel title="Cache hit" hint="by model · cache read ÷ prompt · target 80%">
          {hitRows.length === 0 ? (
            <EmptyNote>No model calls in this range.</EmptyNote>
          ) : (
            <div className="flex flex-col" data-testid="cache-hit">
              {hitRows.map((row) => {
                const low = (row.cache_hit ?? 0) < HIT_TARGET;
                return (
                  <div key={row.name} className={`grid grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)_52px] items-center gap-3 py-[7px] text-[12.5px] ${NUM}`} title={row.name}>
                    <span className="truncate font-mono text-[12px] text-ink">{modelName(row.name, row.provider)}</span>
                    <span className="relative h-1.5 rounded-full bg-paper">
                      <i className="absolute inset-y-0 left-0 rounded-full" style={{ width: `${(row.cache_hit ?? 0) * 100}%`, background: low ? "var(--warn-ink)" : tintColor(0) }} />
                      <i className="absolute -inset-y-1 w-px" style={{ left: `${HIT_TARGET * 100}%`, background: "color-mix(in srgb, var(--ink) 35%, transparent)" }} aria-hidden />
                    </span>
                    <span className={`text-right font-medium ${low ? "text-warnInk" : "text-ink"}`}>{pct(row.cache_hit, 1)}</span>
                  </div>
                );
              })}
            </div>
          )}
        </Panel>

        <Panel title="Background work" hint="model calls outside any turn">
          {background.length === 0 ? (
            <EmptyNote>No model calls outside a turn.</EmptyNote>
          ) : (
            <>
              {background.map((row) => (
                <Meter key={row.purpose} label={PURPOSE[row.purpose] ?? row.purpose} value={row.tokens}
                  total={backgroundTotal} tone="muted" display={compact(row.tokens)} />
              ))}
              <p className="mt-2 text-[11.5px] text-faint">
                Counted in the totals above: real provider spend, not part of a turn.
              </p>
            </>
          )}
        </Panel>
      </div>

      <Panel title="By provider and model" hint="kept when conversations are deleted">
        {providers.length === 0 ? (
          <EmptyNote>No usage recorded yet.</EmptyNote>
        ) : (
          <div className="-mx-1 overflow-x-auto">
            <table className="w-full min-w-[760px] text-[12.5px]" data-testid="usage-table">
              <thead>
                <tr className="border-b border-line">
                  <th className={`${TH} py-2 pl-1 pr-2`}>Provider · model</th>
                  <th className={`${TH} px-2 py-2 text-right`}>Calls</th>
                  <th className={`${TH} px-2 py-2 text-right`}>Fresh in</th>
                  <th className={`${TH} px-2 py-2 text-right`}>Cache read</th>
                  <th className={`${TH} px-2 py-2 text-right`}>Cache write</th>
                  <th className={`${TH} px-2 py-2 text-right`}>Output</th>
                  <th className={`${TH} px-2 py-2 text-right`}>Cache hit</th>
                  <th className={`${TH} w-[170px] px-2 py-2`}>Share</th>
                </tr>
              </thead>
              <tbody>
                {providers.map((p) => {
                  const expanded = open[p.provider] ?? providers.length <= 3;
                  return (
                    <Fragment key={p.provider}>
                      <tr className={`${TD} cursor-pointer transition-colors duration-150 hover:text-ink`}
                        onClick={() => setOpen((prev) => ({ ...prev, [p.provider]: !expanded }))}
                        aria-expanded={expanded}>
                        <td className="py-2.5 pl-1 pr-2 font-semibold">
                          <span className="inline-flex items-center gap-2">
                            <span className="inline-block h-2.5 w-2.5 rounded-[3px]" style={{ background: colour.get(p.provider) }} />
                            {p.provider}
                            <span className="text-[11px] font-normal text-faint">
                              {p.models.length} model{p.models.length === 1 ? "" : "s"} {expanded ? "▾" : "▸"}
                            </span>
                          </span>
                        </td>
                        <td className={CELL_NUM}>{compact(p.calls)}</td>
                        <td className={CELL_NUM}>{compact(p.fresh)}</td>
                        <td className={CELL_NUM}>{compact(p.cacheRead)}</td>
                        <td className={CELL_NUM}>{compact(p.cacheWrite)}</td>
                        <td className={CELL_NUM}>{compact(p.output)}</td>
                        <td className={CELL_NUM}>{pct(hit(p.cacheRead, p.fresh, p.cacheWrite), 0)}</td>
                        <td className="px-2 py-2.5">
                          <ShareBar value={share(p.total, sum.total)} color={colour.get(p.provider) ?? "var(--accent)"} />
                        </td>
                      </tr>
                      {expanded &&
                        p.models.map((row) => (
                          <tr key={row.name} className={`${TD} text-muted transition-colors duration-150 hover:text-ink`}>
                            <td className="max-w-[300px] truncate py-2 pl-7 pr-2 text-ink" title={row.name}>
                              {modelName(row.name, row.provider)}
                            </td>
                            <td className={CELL_NUM}>{compact(row.llm_calls)}</td>
                            <td className={CELL_NUM}>{compact(row.tokens_in)}</td>
                            <td className={CELL_NUM}>{compact(row.cache_read)}</td>
                            <td className={CELL_NUM}>{compact(row.cache_write)}</td>
                            <td className={CELL_NUM}>{compact(row.tokens_out)}</td>
                            <td className={CELL_NUM}>{pct(row.cache_hit, 0)}</td>
                            <td className="px-2 py-2">
                              <ShareBar value={share(row.tokens, sum.total)} color={colour.get(p.provider) ?? "var(--accent)"} faint />
                            </td>
                          </tr>
                        ))}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </div>
  );
}

function ShareBar({ value, color, faint = false }: { value: number; color: string; faint?: boolean }) {
  return (
    <div className="flex items-center gap-2">
      <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-paper">
        <i className="block h-full rounded-full" style={{ width: `${Math.max(value > 0 ? 2 : 0, value * 100)}%`, background: color, opacity: faint ? 0.45 : 1 }} />
      </span>
      <span className={`w-[40px] text-right font-mono text-[11px] text-muted ${NUM}`}>{pct(value, 0)}</span>
    </div>
  );
}
