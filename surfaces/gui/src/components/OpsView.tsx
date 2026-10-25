// The Metrics tab (sidebar "Metrics"): the harness and the providers, tracked over time.
//
// Redesigned 2026-09-25 around the owner's brief: this tab tracks the HARNESS and the TOKENS
// each provider/model spent — not individual sessions. So the per-session lists are gone,
// and every number survives deleting the conversation it came from (metrics_store keeps a
// deleted session's counts). Three views over one payload:
//
//   Usage    tokens by provider → model and by day (the durable rollup; tokens, never $)
//   Harness  five numbers by day with the days a change shipped, health checks with targets,
//            the numbers by model, the harness's review of its own logs (failure shapes and
//            the claims' verdicts), latency, where turn tokens went, compaction, waits
//   Tools    per-tool calls, failure rate and slow tail, and the recent failure reasons
//
// No evals (2026-10-14): they run by hand and spend tokens, so they are read where they run.
//
// Primitives, formatting and each view live in ./metrics/.

import { useCallback, useEffect, useRef, useState } from "react";
import { getOpsMetrics, METRICS_CHANGED, type OpsMetrics, type OpsRange } from "../api";
import { Icon } from "./Icon";
import { agoLabel } from "./metrics/format";
import { HarnessTab } from "./metrics/HarnessTab";
import { ToolsTab } from "./metrics/ToolsTab";
import { UsageTab } from "./metrics/UsageTab";
import { PageHeader, PageShell } from "../ui";

const RANGES: { key: OpsRange; label: string }[] = [
  { key: "today", label: "Today" },
  { key: "7d", label: "7 days" },
  { key: "30d", label: "30 days" },
  { key: "all", label: "All" },
];

/** How often a live push may refetch the page: a fast agent finishes steps faster than this. */
const LIVE_MIN_MS = 1000;

type View = "usage" | "harness" | "tools";
const VIEWS: { key: View; label: string; hint: string }[] = [
  { key: "usage", label: "Usage", hint: "Tokens by provider and model" },
  { key: "harness", label: "Harness", hint: "Health checks, latency, compaction" },
  { key: "tools", label: "Tools", hint: "Calls and failures per tool" },
];
const VIEW_KEY = "reeve:metrics-view";

/** The last numbers each range showed, for this run of the app: a range opened again, or the
 *  page opened again, shows them at once with their age, and they update in place when the
 *  new ones land (2026-10-13). The first open of a range keeps the skeleton. */
const lastByRange = new Map<OpsRange, { data: OpsMetrics; at: number }>();

function storedView(): View {
  try {
    const value = window.localStorage.getItem(VIEW_KEY);
    return value === "harness" || value === "tools" ? value : "usage";
  } catch {
    return "usage";
  }
}

export function OpsView({ onOpenTrace }: { onOpenTrace: (sessionId: string) => void }) {
  const [range, setRange] = useState<OpsRange>("7d");
  const [view, setView] = useState<View>(storedView);
  const [data, setData] = useState<OpsMetrics | null>(() => lastByRange.get("7d")?.data ?? null);
  const [updatedAt, setUpdatedAt] = useState<number | null>(() => lastByRange.get("7d")?.at ?? null);
  const [fetching, setFetching] = useState(false);
  const [failed, setFailed] = useState(false);
  const [, setTick] = useState(0);

  const lastFetch = useRef(0);
  const shown = useRef(range);
  const refresh = useCallback(() => {
    lastFetch.current = Date.now();
    setFetching(true);
    getOpsMetrics(range)
      .then((payload) => {
        const at = Date.now();
        lastByRange.set(range, { data: payload, at });
        if (shown.current !== range) return; // the reader moved to another range meanwhile
        setData(payload);
        setUpdatedAt(at);
        setFailed(false);
      })
      .catch(() => setFailed(true))
      .finally(() => {
        if (shown.current === range) setFetching(false);
      });
  }, [range]);

  useEffect(() => {
    // Another range: what it showed last, if anything, while its new numbers come.
    shown.current = range;
    const last = lastByRange.get(range);
    setData(last?.data ?? null);
    setUpdatedAt(last?.at ?? null);
    refresh();
  }, [refresh, range]);

  // Live: the server says `metrics_changed` after each step it folds (a reply with its tokens, a tool
  // result), not when the turn ends. Refetch at most about once a second, with one trailing fetch, so the
  // page follows the agent without a request per step.
  const trailing = useRef<number | null>(null);
  useEffect(() => {
    const onChanged = () => {
      if (trailing.current !== null) return;
      const wait = Math.max(0, LIVE_MIN_MS - (Date.now() - lastFetch.current));
      trailing.current = window.setTimeout(() => {
        trailing.current = null;
        refresh();
      }, wait);
    };
    window.addEventListener(METRICS_CHANGED, onChanged);
    return () => {
      window.removeEventListener(METRICS_CHANGED, onChanged);
      if (trailing.current !== null) window.clearTimeout(trailing.current);
      trailing.current = null;
    };
  }, [refresh]);

  // "Updated Xs ago" stays honest between pushes.
  useEffect(() => {
    const id = window.setInterval(() => setTick((n) => n + 1), 1000);
    return () => window.clearInterval(id);
  }, []);

  const choose = (next: View) => {
    setView(next);
    try {
      window.localStorage.setItem(VIEW_KEY, next);
    } catch {
      // a blocked storage only forgets the choice
    }
  };

  const empty =
    data != null && (data.headline?.sessions ?? 0) === 0 && (data.by_model?.length ?? 0) === 0;

  return (
    <PageShell wide testId="ops-view">
          <PageHeader
            title="Metrics"
            icon={<Icon name="ops" size={18} />}
            sub="The harness and the providers over time. Numbers stay when conversations are deleted."
            actions={
              <>
                {updatedAt != null && (
                  <span className="inline-flex items-center gap-2 text-[12px] text-muted" data-testid="ops-updated">
                    <i className={"h-[7px] w-[7px] rounded-full " + (failed ? "bg-danger" : "bg-ok")} aria-hidden />
                    {fetching && data != null ? "Refreshing · " : "Live · "}updated {agoLabel(updatedAt)}
                  </span>
                )}
                <div className="flex gap-0.5 rounded-lg bg-chrome p-0.5" role="group" aria-label="Range">
                  {RANGES.map((item) => (
                    <button
                      key={item.key}
                      type="button"
                      aria-pressed={range === item.key}
                      data-testid={`ops-range-${item.key}`}
                      onClick={() => setRange(item.key)}
                      className={
                        "rounded-md px-2.5 py-1 text-[12px] transition-colors duration-150 " +
                        (range === item.key
                          ? "bg-panel font-semibold text-ink shadow-[var(--elev-1)]"
                          : "text-muted hover:text-ink")
                      }
                    >
                      {item.label}
                    </button>
                  ))}
                </div>
                <button
                  type="button"
                  onClick={refresh}
                  data-testid="ops-refresh"
                  title="Refresh now"
                  aria-label="Refresh now"
                  className="grid h-7 w-7 place-items-center rounded-lg bg-chrome text-muted transition-colors duration-150 hover:text-ink"
                >
                  <Icon name="refresh" size={13} />
                </button>
              </>
            }
          />

          <nav className="mt-5 flex gap-1 border-b border-line" role="tablist" aria-label="Metrics views">
            {VIEWS.map((item) => (
              <button
                key={item.key}
                type="button"
                role="tab"
                aria-selected={view === item.key}
                data-testid={`ops-tab-${item.key}`}
                title={item.hint}
                onClick={() => choose(item.key)}
                className={
                  "relative -mb-px px-3 pb-2.5 pt-1 text-[13px] transition-colors duration-150 " +
                  (view === item.key
                    ? "font-semibold text-ink after:absolute after:inset-x-2 after:bottom-0 after:h-[2px] after:rounded-full after:bg-accent"
                    : "text-muted hover:text-ink")
                }
              >
                {item.label}
              </button>
            ))}
          </nav>

          {failed && (
            <div className="mt-4 flex items-center gap-2 rounded-lg bg-dangerSoft px-3 py-2 text-[12px] text-danger">
              <span className="h-1.5 w-1.5 rounded-full bg-danger" />
              Sidecar not reachable — showing the last fold{updatedAt ? ` (${agoLabel(updatedAt)})` : ""}.
            </div>
          )}
          {data?.backfilling && (
            <div className="mt-4 flex items-center gap-2 rounded-lg bg-warnSoft px-3 py-2 text-[12px] text-warnInk">
              <span className="h-1.5 w-1.5 rounded-full bg-warnInk" />
              Indexing session logs — totals fill as history folds.
            </div>
          )}

          <div className="mt-4">
            {data == null ? (
              <div className="flex flex-col gap-4" aria-busy>
                <div className="h-[92px] animate-pulse rounded-xl bg-panel" />
                <div className="h-[240px] animate-pulse rounded-xl bg-panel" />
              </div>
            ) : empty ? (
              <div className="ops-rise flex flex-col items-center rounded-xl bg-panel px-6 py-14 text-center shadow-[var(--elev-1)]">
                <Icon name="ops" size={26} className="text-faint" />
                <div className="mt-3 text-[13.5px] font-medium">Nothing to measure yet</div>
                <p className="mt-1 max-w-[380px] text-[12px] leading-relaxed text-muted">
                  Run a session and its tokens, tools and latency fold in here — no setup, the event
                  logs are already being written.
                </p>
              </div>
            ) : (
              <div className="ops-rise" key={view}>
                {view === "usage" && <UsageTab data={data} range={range} />}
                {view === "harness" && <HarnessTab data={data} range={range} />}
                {view === "tools" && <ToolsTab data={data} range={range} onOpenTrace={onOpenTrace} />}
              </div>
            )}
          </div>
    </PageShell>
  );
}
