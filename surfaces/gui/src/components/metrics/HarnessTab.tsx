// Harness: is the harness itself behaving? Five numbers as tiles, the chosen one drawn by day with
// the days a change shipped (the ledger's `shipped`), each against the span before; the checks the
// harness is held to; the same numbers by model, since a change is judged on one model at a time;
// the harness's review of its own logs; then latency, where the tokens went, compaction and waits.
// Redrawn 2026-10-14 from the approved canvas (Vercel-style, soft columns). Evals are not here:
// they run by hand and spend tokens.

import { useState } from "react";
import type { OpsMetrics, OpsRange } from "../../api";
import { DayArea, StackedDays } from "./charts";
import { band, change, compact, ms, modelName, pct, rangeDays, share, shortDay, tintColor, type Tone } from "./format";
import { Change, EmptyNote, HealthCheck, Meter, NUM, Panel } from "./primitives";
import { ReviewPanels } from "./ReviewPanels";

const COMPACTION_LABEL: Record<string, string> = {
  summarized: "Summarized",
  prepared: "Prepared (instant)",
  pruned: "Pruned tool output",
  "pruned (cache cold)": "Pruned, cache cold",
  trimmed: "Trimmed",
  failed: "Failed",
  no_change: "No change",
  folded: "Model folded",
};

type TileKey = "turns" | "completion" | "tool" | "steps" | "ttft";
const TILE_KEY = "reeve:metrics-harness-tile";

function storedTile(): TileKey {
  try {
    const value = window.localStorage.getItem(TILE_KEY);
    return value === "turns" || value === "tool" || value === "steps" || value === "ttft" ? value : "completion";
  } catch {
    return "completion";
  }
}

function countTone(value: number, warnAt: number): Tone {
  return value === 0 ? "ok" : value <= warnAt ? "warn" : "bad";
}

/** A count's line starts at zero and reaches its largest day, never less than one. */
const COUNT_DOMAIN: [number, (max: number) => number] = [0, (max) => Math.max(1, max)];
const PERCENT_DOMAIN: [(min: number) => number, number] = [(min) => Math.min(90, Math.floor(min)), 100];

export function HarnessTab({ data, range }: { data: OpsMetrics; range: OpsRange }) {
  const [tile, setTile] = useState<TileKey>(storedTile);
  const headline = data.headline;
  const harness = data.harness;
  const before = data.previous?.harness;
  const ships = data.ships ?? [];
  const stalls = harness?.stalls ?? { compaction: 0, "provider retry": 0, unexplained: 0 };
  const compaction = harness?.compaction ?? {};
  const failedCompactions = compaction.failed ?? 0;
  const partial = harness?.usage_partial ?? [];
  const spend = harness?.spend;

  const days = rangeDays(range, [...(data.daily ?? []).map((d) => d.day), ...(harness?.daily ?? []).map((d) => d.day)]);
  const byDay = new Map((data.daily ?? []).map((d) => [d.day, d]));
  const harnessDays = harness?.daily;
  const byHarnessDay = new Map((harnessDays ?? []).map((d) => [d.day, d]));
  const daysOf = (pick: (d: NonNullable<typeof harnessDays>[number]) => number | null) =>
    harnessDays ? days.map((day) => { const d = byHarnessDay.get(day); return d ? pick(d) : 0; }) : undefined;
  const perDay = (pick: (d: NonNullable<ReturnType<typeof byDay.get>>) => number | null) =>
    days.map((day) => { const d = byDay.get(day); return { day, value: d ? pick(d) : null }; });

  const choose = (key: TileKey) => {
    setTile(key);
    try {
      window.localStorage.setItem(TILE_KEY, key);
    } catch {
      // a blocked storage only forgets the choice
    }
  };
  const tiles: { key: TileKey; label: string; value: string; moved: ReturnType<typeof change>; good?: "up" | "down" }[] = [
    { key: "turns", label: "Turns", value: compact(headline.turns_started), moved: change(headline.turns_started, before?.turns) },
    { key: "completion", label: "Turn completion", value: pct(headline.completion), moved: change(headline.completion, before?.completion, "ratio"), good: "up" },
    { key: "tool", label: "Tool success", value: pct(headline.tool_success), moved: change(headline.tool_success, before?.tool_success, "ratio"), good: "up" },
    { key: "steps", label: "Steps / turn", value: headline.steps_per_turn != null ? String(headline.steps_per_turn) : "—", moved: change(headline.steps_per_turn, before?.steps_per_turn) },
    { key: "ttft", label: "TTFT p95", value: ms(headline.ttft_p95_ms), moved: change(headline.ttft_p95_ms, before?.ttft_p95_ms), good: "down" },
  ];

  const chart = (() => {
    if (tile === "turns") {
      const bars = days.map((day) => {
        const d = byDay.get(day);
        const failed = d?.failed ?? 0;
        return { day, finished: Math.max(0, (d?.turns ?? 0) - failed), failed };
      });
      return (
        <StackedDays
          data={bars}
          series={[
            { key: "finished", label: "Finished", color: tintColor(0) },
            { key: "failed", label: "Failed", color: "var(--danger)" },
          ]}
          format={(v) => compact(Math.round(v))}
          dayLabel={shortDay}
          ships={ships}
          height={270}
        />
      );
    }
    const spec = {
      completion: { label: "Turn completion", values: perDay((d) => (d.completion == null ? null : d.completion * 100)), format: (v: number) => `${Math.round(v)}%`, domain: PERCENT_DOMAIN },
      tool: { label: "Tool success", values: perDay((d) => (d.tool_error_rate == null ? null : (1 - d.tool_error_rate) * 100)), format: (v: number) => `${Math.round(v)}%`, domain: PERCENT_DOMAIN },
      steps: { label: "Steps / turn", values: perDay((d) => d.steps_per_turn), format: (v: number) => String(Math.round(v * 10) / 10), domain: undefined },
      ttft: { label: "TTFT p95", values: perDay((d) => d.ttft_p95_ms), format: (v: number) => ms(v), domain: undefined },
    }[tile];
    return <DayArea data={spec.values} label={spec.label} format={spec.format} dayLabel={shortDay} domain={spec.domain} ships={ships} height={270} />;
  })();

  const ttft = harness?.ttft ?? [];
  const ttftPeak = Math.max(1, ...ttft.map((row) => row.p90_ms));
  const compactionTotal = Object.values(compaction).reduce((a, b) => a + b, 0);
  const stallTotal = stalls.compaction + stalls["provider retry"] + stalls.unexplained;
  const spendParts = spend && spend.turn_total > 0
    ? [
        { label: "Main model", value: spend.main, color: tintColor(0) },
        { label: "Subagents", value: spend.delegated, color: "var(--teal-ink)" },
        { label: "Summaries", value: spend.summarizer + spend.prepare, color: "var(--warn-ink)" },
        { label: "Unattributed", value: spend.unattributed, color: "var(--danger)" },
      ]
    : [];
  const models = (data.by_model ?? [])
    .filter((row) => row.harness && row.harness.turns > 0)
    .sort((a, b) => (b.harness?.turns ?? 0) - (a.harness?.turns ?? 0))
    .slice(0, 8);
  const mostTurns = Math.max(1, ...models.map((row) => row.harness?.turns ?? 0));

  return (
    <div className="flex flex-col gap-4">
      <section className="overflow-hidden rounded-xl bg-panel shadow-[var(--elev-1)]" aria-label="The harness by day">
        <div className="grid grid-cols-5 border-b border-line max-[700px]:grid-cols-2 max-[700px]:[&>*:last-child]:col-span-2" role="group" aria-label="Number drawn">
          {tiles.map((t, index) => {
            const on = t.key === tile;
            return (
              <button
                key={t.key}
                type="button"
                aria-pressed={on}
                onClick={() => choose(t.key)}
                data-testid={`harness-tile-${t.key}`}
                className={
                  "flex min-w-0 flex-col gap-1.5 px-4 pb-3.5 pt-3.5 text-left transition-[color,box-shadow] duration-150 " +
                  (index ? "border-l border-line " : "") +
                  (on ? "shadow-[inset_0_-2px_0_var(--ink)]" : "bg-[color-mix(in_srgb,var(--ink)_2%,var(--panel))] text-muted hover:text-ink")
                }
              >
                <span className="truncate text-[12px] text-muted">{t.label}</span>
                <span className="text-[24px] font-semibold leading-[30px] tracking-[-0.02em] tabular-nums text-ink">{t.value}</span>
                <span className="flex h-5 items-center gap-1.5 text-[11.5px] text-faint">
                  {t.moved ? <Change {...t.moved} good={t.good} /> : <span>—</span>}
                  {t.moved && <span className="truncate">vs the span before</span>}
                </span>
              </button>
            );
          })}
        </div>
        <div className="px-4 pb-3 pt-4" data-testid="harness-chart">
          {(data.daily ?? []).length === 0 ? <EmptyNote>No turns in this range.</EmptyNote> : chart}
          <p className={`mt-2 text-[11.5px] text-faint ${NUM}`}>
            Retries {pct(headline.retry_rate)} · loop flags {headline.loop_flags} · compactions {compactionTotal || headline.compactions}
            {ships.length > 0 && " · dashed: a day a harness change shipped"}
          </p>
        </div>
      </section>

      <div className="grid grid-cols-4 gap-3 max-[1100px]:grid-cols-2 max-[700px]:grid-cols-1" data-testid="health-checks">
        <HealthCheck title="Unattributed spend" value={pct(spend?.unattributed_pct ?? null, 2)} target="≈ 0%"
          tone={band(spend?.unattributed_pct ?? null, 0.01, 0.05, false)}
          meaning="Turn tokens no logged model call explains: an unlogged call hides here."
          trend={daysOf((d) => (d.unattributed_pct == null ? null : d.unattributed_pct * 100))} />
        <HealthCheck title="Unexplained waits" value={String(stalls.unexplained)} target="0"
          tone={harness ? countTone(stalls.unexplained, 2) : "none"}
          meaning="Waits over 10s before a step with no compaction or provider retry to explain them."
          trend={daysOf((d) => d.unexplained)} domain={COUNT_DOMAIN} />
        <HealthCheck title="Usage under-reported" value={partial.length === 1 ? "1 route" : `${partial.length} routes`} target="0"
          tone={harness ? (partial.length === 0 ? "ok" : "warn") : "none"}
          meaning={partial.length ? partial.map((p) => p.model).join(", ") : "Provider routes whose prompt counts did not grow with the prompt."}
          trend={daysOf((d) => d.usage_partial)} domain={COUNT_DOMAIN} />
        <HealthCheck title="Compaction failures" value={String(failedCompactions)} target="0"
          tone={harness ? countTone(failedCompactions, 1) : "none"}
          meaning="Compactions whose summarizer failed; the context stayed as it was."
          trend={daysOf((d) => d.compaction_failed)} domain={COUNT_DOMAIN} />
      </div>

      <Panel title="By model" hint="a harness change is judged on one model at a time">
        {models.length === 0 ? (
          <EmptyNote>No turns in this range.</EmptyNote>
        ) : (
          <div className="-mx-1 overflow-x-auto">
            <table className="w-full min-w-[760px] text-[12.5px]" data-testid="harness-by-model">
              <thead>
                <tr className="border-b border-line text-right text-[11.5px] text-muted">
                  <th className="py-2 pl-1 pr-2 text-left font-medium">Model</th>
                  <th className="px-2 py-2 font-medium">Turns</th>
                  <th className="px-2 py-2 font-medium">Completion</th>
                  <th className="px-2 py-2 font-medium">Tool success</th>
                  <th className="px-2 py-2 font-medium">Steps / turn</th>
                  <th className="px-2 py-2 font-medium">Tokens / turn</th>
                  <th className="py-2 pl-2 pr-1 font-medium">Cache hit</th>
                </tr>
              </thead>
              <tbody>
                {models.map((row) => {
                  const h = row.harness!;
                  return (
                    <tr key={row.name} className={`border-b border-line/60 text-right last:border-b-0 ${NUM}`}>
                      <td className="relative py-2.5 pl-1 pr-2 text-left">
                        <i className="absolute inset-y-1 left-0 rounded-r-[4px]" style={{ width: `${(h.turns / mostTurns) * 100}%`, background: "color-mix(in srgb, var(--accent) 8%, transparent)" }} />
                        <span className="relative font-mono text-[12px] text-ink" title={row.name}>{modelName(row.name, row.provider)}</span>
                      </td>
                      <td className="px-2 py-2.5">{compact(h.turns)}</td>
                      <td className={`px-2 py-2.5 ${h.completion != null && h.completion < 0.97 ? "font-medium text-warnInk" : ""}`}>{pct(h.completion)}</td>
                      <td className="px-2 py-2.5">{pct(h.tool_success)}</td>
                      <td className="px-2 py-2.5">{h.steps_per_turn ?? "—"}</td>
                      <td className="px-2 py-2.5">{compact(h.tokens_per_turn == null ? null : Math.round(h.tokens_per_turn))}</td>
                      <td className="py-2.5 pl-2 pr-1">{pct(row.cache_hit)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Panel>

      <ReviewPanels review={data.review} />

      <div className="grid grid-cols-3 gap-4 max-[1100px]:grid-cols-1">
        <Panel title="Time to first token" hint="by prompt size · median to p90">
          {ttft.length === 0 ? (
            <EmptyNote>No timed model calls yet.</EmptyNote>
          ) : (
            <div className="flex flex-col gap-3 pt-2" data-testid="ttft-bands">
              {ttft.map((row) => {
                const from = (row.p50_ms * 100) / ttftPeak;
                const to = (row.p90_ms * 100) / ttftPeak;
                return (
                  <div key={row.band} className="flex items-center gap-3 text-[12px]" title={`${row.samples} calls`}>
                    <span className="w-[72px] shrink-0 text-right font-mono text-[11.5px] text-muted">{row.band}</span>
                    <span className="relative h-1.5 flex-1 rounded-full bg-paper">
                      <i className="absolute inset-y-0 rounded-full" style={{ left: `${from}%`, width: `${Math.max(1, to - from)}%`, background: tintColor(0) }} />
                    </span>
                    <span className={`w-[104px] shrink-0 text-right font-mono text-[11.5px] ${NUM}`}>
                      {ms(row.p50_ms)} <span className="text-faint">/ {ms(row.p90_ms)}</span>
                    </span>
                  </div>
                );
              })}
              <p className="text-[11.5px] text-faint">Longer prompts answer slower: one of the reasons the working window is capped at 400k.</p>
            </div>
          )}
        </Panel>

        <Panel title="Where turn tokens went" hint="the logged call that spent them">
          {spendParts.length === 0 ? (
            <EmptyNote>No closed turns yet.</EmptyNote>
          ) : (
            <div data-testid="turn-spend">
              <div className="my-1.5 flex h-2 gap-0.5 overflow-hidden rounded-full">
                {spendParts.filter((part) => part.value > 0).map((part) => (
                  <i key={part.label} className="block h-full" style={{ flex: `${Math.max(0.6, (part.value * 100) / spend!.turn_total)} 1 0`, background: part.color }} />
                ))}
              </div>
              <div className="mt-3 flex flex-col gap-1.5 text-[12px] text-muted">
                {spendParts.map((part) => (
                  <span key={part.label} className="inline-flex items-center gap-2">
                    <i className="h-2 w-2 rounded-full" style={{ background: part.color }} />
                    {part.label}
                    <b className={`ml-auto font-medium text-ink ${NUM}`}>{pct(share(part.value, spend!.turn_total), part.label === "Unattributed" ? 2 : 1)}</b>
                  </span>
                ))}
              </div>
            </div>
          )}
        </Panel>

        <Panel title="Compaction" hint={compactionTotal ? `${compactionTotal} · what each did` : "what each did"}>
          {compactionTotal === 0 ? (
            <EmptyNote>No compaction in this range.</EmptyNote>
          ) : (
            Object.entries(compaction).sort((a, b) => b[1] - a[1]).map(([reason, count]) => (
              <Meter key={reason} label={COMPACTION_LABEL[reason] ?? reason} value={count} total={Math.max(...Object.values(compaction))}
                tone={reason === "failed" ? "bad" : "accent"} display={String(count)} />
            ))
          )}
        </Panel>
      </div>

      <Panel title="Waits before a step" hint="over 10 seconds, by cause">
        {stallTotal === 0 ? (
          <EmptyNote>No long waits.</EmptyNote>
        ) : (
          <>
            <div className="grid grid-cols-3 gap-2.5" data-testid="waits">
              {([["Compaction", stalls.compaction, false], ["Provider retry", stalls["provider retry"], false], ["Unexplained", stalls.unexplained, true]] as const).map(([label, value, loud]) => (
                <div key={label} className={`rounded-[10px] border px-3 py-2.5 ${loud && value > 0 ? "border-transparent bg-dangerSoft" : "border-line bg-canvas"}`}>
                  <div className={`text-[20px] font-semibold ${NUM} ${loud && value > 0 ? "text-danger" : ""}`}>{value}</div>
                  <div className="text-[12px] text-muted">{label}</div>
                </div>
              ))}
            </div>
            <p className="mt-2.5 text-[11.5px] text-faint">An unexplained wait is one nothing in the log accounts for: the thing to look at.</p>
          </>
        )}
      </Panel>

      {(data.notable ?? []).length > 0 && (
        <Panel title="Notable changes" hint="against each series' own history (±2σ)">
          {(data.notable ?? []).map((item) => (
            <div key={`${item.kind}:${item.day}`} className="flex items-center gap-3 py-1.5 text-[12.5px]">
              <span className="w-[48px] shrink-0 text-[11.5px] text-faint">{shortDay(item.day)}</span>
              <span className={"h-1.5 w-1.5 shrink-0 rounded-full " + (item.level === "err" ? "bg-danger" : item.level === "warn" ? "bg-warnInk" : "bg-ok")} />
              <span className="min-w-0 flex-1">{item.text}</span>
            </div>
          ))}
        </Panel>
      )}
    </div>
  );
}
