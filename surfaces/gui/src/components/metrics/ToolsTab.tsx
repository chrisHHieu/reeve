// Tools: which tools the agent leans on, and which ones fail it. Calls are bars; the failure
// rate is a point on its own scale beside them, so a tool used 31 times that fails one call in
// eight stands out next to one used a thousand times (redrawn 2026-10-13). The failure list
// keeps the reason text (the thing to fix) and a quiet trace link for the one worth opening.

import type { OpsMetrics, OpsRange } from "../../api";
import { StackedDays } from "./charts";
import { shortDay, ms, pct, rangeDays, readableReason, share } from "./format";
import { EmptyNote, Legend, NUM, Panel } from "./primitives";

/** The rate scale's right end: a rate past it sits at the end, its number still exact. */
const RATE_SCALE = 0.15;

function rateColor(rate: number): string {
  if (rate >= 0.1) return "var(--danger)";
  if (rate >= 0.03) return "var(--warn-ink)";
  return "var(--ok)";
}

export function ToolsTab({
  data,
  range,
  onOpenTrace,
}: {
  data: OpsMetrics;
  range: OpsRange;
  onOpenTrace: (sessionId: string) => void;
}) {
  const tools = data.tools ?? [];
  const busiest = Math.max(1, ...tools.map((t) => t.calls));
  const days = rangeDays(range, (data.daily ?? []).map((d) => d.day));
  const byDay = new Map((data.daily ?? []).map((d) => [d.day, d]));
  const failureDays = days.map((day) => ({ day, failures: byDay.get(day)?.tool_failures ?? 0 }));
  const failureTotal = failureDays.reduce((total, d) => total + d.failures, 0);
  const grid = "grid grid-cols-[minmax(110px,170px)_minmax(0,1fr)_minmax(150px,220px)_64px] items-center gap-4";
  return (
    <div className="flex flex-col gap-4">
      <Panel title="Tool health" hint="busiest first · calls, failure rate, slowest 5%">
        {tools.length === 0 ? (
          <EmptyNote>No tool calls in this range.</EmptyNote>
        ) : (
          <div data-testid="tool-health">
            <div className={`${grid} border-b border-line pb-1.5 text-[12px] text-muted`}>
              <span>Tool</span>
              <span>Calls</span>
              <span>Failure rate</span>
              <span className="text-right">p95</span>
            </div>
            {tools.map((tool) => {
              const failures = tool.errors + tool.timeouts;
              const rate = share(failures, tool.calls);
              const at = `${(Math.min(rate, RATE_SCALE) / RATE_SCALE) * 100}%`;
              const color = rateColor(rate);
              return (
                <div key={tool.name} className={`${grid} h-[34px] border-b border-line/60 text-[12.5px] last:border-b-0`} data-testid="tool-row"
                  title={`${tool.errors} errors · ${tool.timeouts} timeouts · ${tool.blocked} blocked`}>
                  <span className="truncate font-mono text-[12px] font-medium" title={tool.name}>{tool.name}</span>
                  <span className="flex items-center gap-2.5">
                    <span className="h-2 flex-1 overflow-hidden rounded-full bg-paper">
                      <i className="block h-full rounded-full bg-accent" style={{ width: `${Math.max(1.5, (tool.calls * 100) / busiest)}%` }} />
                    </span>
                    <span className={`w-[48px] text-right ${NUM}`}>{tool.calls.toLocaleString()}</span>
                  </span>
                  <span className="flex items-center gap-2.5">
                    <span className="relative h-3 flex-1">
                      <i className="absolute top-1/2 h-px w-full -translate-y-1/2 bg-line" />
                      <i className="absolute top-1/2 h-0.5 -translate-y-1/2" style={{ width: at, background: color }} />
                      <i className="absolute top-1/2 h-2.5 w-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full" style={{ left: at, background: color }} />
                    </span>
                    <span className={`w-[44px] text-right ${NUM}`} style={{ color: rate >= 0.03 ? color : "var(--muted)" }}>{pct(rate, 1)}</span>
                  </span>
                  <span className={`text-right text-muted ${NUM}`}>{ms(tool.p95_ms)}</span>
                </div>
              );
            })}
          </div>
        )}
        <p className="mt-2 text-[11.5px] text-faint">
          Blocked calls are policy or approval refusals, not failures, and are left out of the rate. A rate under 3% is green, under 10% amber, above that red.
        </p>
      </Panel>

      <div className="grid grid-cols-[minmax(0,1fr)_minmax(0,1.5fr)] gap-4 max-[1100px]:grid-cols-1">
        <Panel title="Failures per day" hint="errors and timeouts">
          {failureTotal === 0 ? (
            <EmptyNote>No tool failures in this range.</EmptyNote>
          ) : (
            <div data-testid="failure-days">
              <StackedDays data={failureDays} series={[{ key: "failures", label: "failures", color: "var(--danger)" }]}
                format={(v) => String(Math.round(v))} dayLabel={shortDay} height={190} />
              <Legend items={[{ label: "failures", color: "var(--danger)", value: String(failureTotal) }]} />
            </div>
          )}
        </Panel>

        <Panel title="Recent failures" hint="newest first · the reason is the thing to fix">
          {(data.failures ?? []).length === 0 ? (
            <EmptyNote>No tool failures in this range.</EmptyNote>
          ) : (
            <div className="flex flex-col">
              {(data.failures ?? []).slice(0, 12).map((failure, index) => (
                <div key={`${failure.session_id}:${failure.turn}:${index}`}
                  className="group flex items-center gap-3 border-b border-line/60 px-1.5 py-2 text-[12.5px] last:border-b-0">
                  <span className={"h-1.5 w-1.5 shrink-0 rounded-full " + (failure.status === "error" ? "bg-danger" : "bg-warnInk")} />
                  <span className="w-[160px] shrink-0 truncate font-mono text-[12px] font-medium" title={failure.tool}>{failure.tool}</span>
                  <span className="min-w-0 flex-1 truncate text-muted" title={readableReason(failure.reason)}>
                    {readableReason(failure.reason) || failure.status}
                  </span>
                  <span className="w-[48px] shrink-0 text-right text-[11.5px] text-faint">{shortDay(failure.day)}</span>
                  <button type="button" onClick={() => onOpenTrace(failure.session_id)}
                    className="shrink-0 rounded-md px-1.5 py-0.5 text-[12px] font-medium text-accent opacity-0 transition-opacity duration-150 hover:underline focus-visible:opacity-100 group-hover:opacity-100">
                    Open trace
                  </button>
                </div>
              ))}
            </div>
          )}
        </Panel>
      </div>
    </div>
  );
}
