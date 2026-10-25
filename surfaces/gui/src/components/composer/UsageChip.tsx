import { useEffect, useRef, useState } from "react";
import { displayModelFull } from "../../modelName";
import type { ContextBreakdown, SessionMetrics, SessionUsage, TurnUsage } from "../../types";
import { formatDuration } from "../../ui/Elapsed";
import { formatTokens, reconcileBreakdown, tokensTotal, totalTokens } from "../../usage";
import { ContextGauge, gaugeLevel, type GaugeLevel } from "../ContextGauge";
import { Icon } from "../Icon";

// The composer's usage chip: a ring for how full the context is, when the model's window
// is known, else the session's token total. Over it, a panel with the gauge, what the context is
// made of, each model's spend and the session's calls. Tokens only, never money: what a token cost
// cannot be known here (discounts, each provider's cache billing).

// The parts of a request, as the panel names them. Largest first, each with a bar as long as its
// share, in one colour (2026-10-12); seven dots in near-identical blues from outside the palette
// said nothing of which part was large.
const PARTS: { key: keyof ContextBreakdown; label: string }[] = [
  { key: "messages", label: "Messages" },
  { key: "mcp_tools", label: "MCP tools" },
  { key: "system_tools", label: "System tools" },
  { key: "skills", label: "Skills" },
  // AGENTS.md, CLAUDE.md and the rules, read on every request.
  { key: "instructions", label: "Instruction files" },
  { key: "system_prompt", label: "System prompt" },
  { key: "meta_context", label: "Meta context" },
];

const LEVEL_COLOR: Record<GaugeLevel, string> = { ok: "var(--accent)", near: "var(--warn-ink)", soon: "var(--danger)" };

/** One arc from twelve o'clock, clockwise, over a hairline track. Rings are drawn on the chip only;
 *  inside the panel the gauge is a straight bar (owner, 2026-09-10: "chỉ ở ngoài khung chat mới
 *  hình tròn"). Several segments are supported, and none uses them today. */
function Ring({ segments, size = 18, stroke = 2.4 }: { segments: { color: string; share: number }[]; size?: number; stroke?: number }) {
  const r = (size - stroke) / 2;
  const around = 2 * Math.PI * r;
  // Segments are parted by a hairline gap; a lone arc keeps clean ends.
  const gap = segments.length > 1 ? 1.5 : 0;
  let from = 0;
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="-rotate-90 shrink-0" aria-hidden="true">
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--line)" strokeWidth={stroke} />
      {segments.map((segment, i) => {
        const start = (from / 100) * around;
        const length = Math.max((Math.min(Math.max(segment.share, 0), 100) / 100) * around - gap, 1.25);
        from += segment.share;
        return (
          <circle
            key={i}
            className="usage-arc"
            cx={size / 2}
            cy={size / 2}
            r={r}
            fill="none"
            stroke={segment.color}
            strokeWidth={stroke}
            strokeDasharray={`${length} ${around - length}`}
            strokeDashoffset={-start}
            strokeLinecap="round"
          />
        );
      })}
    </svg>
  );
}

/** A labelled figure, a line of its own. */
function Figure({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="flex items-baseline justify-between text-[12px] leading-snug">
      <span className="text-faint">{label}</span>
      <span className="text-ink tabular-nums">{typeof value === "number" ? formatTokens(value) : value}</span>
    </div>
  );
}

/** A model's session totals: its name and total, opening to its billing classes. Every model starts
 *  shut (2026-10-12): the row already says both, and a panel that opened the current model's lines
 *  stood tall enough to cover the conversation. It opens and shuts with a motion (0fr ↔ 1fr); shut,
 *  its lines stay mounted and out of reach. */
function ModelUsageRow({ id, label, spent }: { id: string; label: string; spent: TurnUsage }) {
  const [open, setOpen] = useState(false);
  const input = spent.input + spent.cache_read + spent.cache_write;
  return (
    <div>
      <button
        type="button"
        className="w-full flex items-center gap-1.5 py-0.5 rounded-md text-left hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        title={id}
        data-testid={`usage-model-${id}`}
      >
        <Icon name="chevronDown" size={11} className={"usage-model-chevron shrink-0 text-faint " + (open ? "" : "-rotate-90")} />
        <span className="flex-1 min-w-0 truncate text-[12px] text-ink font-medium">{label}</span>
        <span className="shrink-0 text-[12px] text-ink tabular-nums">{formatTokens(tokensTotal(spent))}</span>
      </button>
      <div className="usage-model-body" data-open={open} aria-hidden={!open} {...(open ? {} : { inert: "" })} data-testid={`usage-model-detail-${id}`}>
        <div className="usage-model-lines">
          {/* Session sums. Where the provider splits its cache, the input is its three billing
              classes, each priced apart, adding up to the total; where it does not (Ollama, the
              compatible vendors), "Input" is the whole of it. */}
          {spent.cache_read + spent.cache_write > 0 ? (
            <>
              <Figure label="Uncached input" value={spent.input} />
              <Figure label="Cache reads" value={spent.cache_read} />
              <Figure label="Cache writes" value={spent.cache_write} />
              <Figure label="Total input" value={input} />
            </>
          ) : (
            <Figure label="Input" value={spent.input} />
          )}
          <Figure label="Output" value={spent.output} />
        </div>
      </div>
    </div>
  );
}

/** The panel opens on hover, as a gauge is glanced at (owner, 2026-09-10), and shuts a moment after
 *  the pointer leaves the chip and the panel, which covers the gap between them. A press toggles it,
 *  for the keyboard and for touch. */
function useHoverOpen() {
  const [open, setOpen] = useState(false);
  const timer = useRef<number | null>(null);
  const settle = (next: boolean, after: number) => {
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setOpen(next), after);
  };
  useEffect(() => () => void (timer.current !== null && window.clearTimeout(timer.current)), []);
  return { open, setOpen, enter: () => settle(true, 120), leave: () => settle(false, 220) };
}

/** The session's calls, from the server's count over its event log (GET /metrics): retries and
 *  failure rates are not in the message sidecars. Asked for once, when the panel first opens; a
 *  server without the route leaves the section out. */
function useMetricsOnOpen(open: boolean, load?: () => Promise<SessionMetrics | null>) {
  const [metrics, setMetrics] = useState<SessionMetrics | null>(null);
  const asked = useRef(false);
  useEffect(() => {
    if (!open || asked.current || !load) return;
    asked.current = true;
    load()
      .then(setMetrics)
      .catch(() => setMetrics(null));
  }, [open, load]);
  return metrics;
}

function CallsSection({ metrics }: { metrics: SessionMetrics }) {
  const { llm, tools, latency } = metrics;
  if (!llm && !tools && !latency) return null;
  return (
    <div className="mt-2.5 pt-2 border-t border-line" data-testid="session-metrics">
      <div className="usage-sec">Calls</div>
      <div className="flex flex-col gap-0.5">
        {llm && <Figure label="Model calls" value={`${llm.calls}${llm.retries > 0 ? ` · ${llm.retries} retries` : ""}`} />}
        {llm && llm.errors > 0 && <Figure label="Failed turns" value={`${llm.errors}${llm.error_rate != null ? ` (${Math.round(llm.error_rate * 100)}%)` : ""}`} />}
        {latency?.ttft_ms_avg != null && <Figure label="First token (avg)" value={formatDuration(latency.ttft_ms_avg)} />}
        {tools && <Figure label="Tool calls" value={`${tools.calls}${tools.timeout_or_error ? ` · ${tools.timeout_or_error} failed` : ""}`} />}
      </div>
    </div>
  );
}

type UsageChipProps = {
  usage: SessionUsage;
  contextWindow?: number;
  /** Where the session compacts, when the server has said. */
  compactAt?: number;
  /** Settings can turn the ring off. */
  contextBar?: boolean;
  /** A compaction is running: the ring turns. */
  compacting?: boolean;
  model: string;
  modelLabels?: Record<string, string>;
  loadMetrics?: () => Promise<SessionMetrics | null>;
};

export function UsageChip({ usage, contextWindow, compactAt, contextBar, compacting, model, modelLabels, loadMetrics }: UsageChipProps) {
  const { open, setOpen, enter, leave } = useHoverOpen();
  const metrics = useMetricsOnOpen(open, loadMetrics);
  const total = totalTokens(usage);
  // The ring fills toward where the session compacts, the budget it really has; the model's
  // window is the limit named below. Against the window alone it read "381k of 1M · 38%" a moment
  // before compacting at 380k (owner, 2026-09-28).
  const budget = compactAt && compactAt > 0 ? compactAt : contextWindow;
  const pct = budget ? Math.min(100, Math.round((usage.context / budget) * 100)) : null;
  const ring = pct !== null && contextBar === true;
  // Calm, near or soon is only known against the point of compacting: nothing is near a whole window.
  const level: GaugeLevel = ring && compactAt ? gaugeLevel(pct as number) : "ok";
  const nameOf = (id: string) => (id === "unknown" ? "Unknown model" : displayModelFull(id, modelLabels));

  // What fills the context: the engine's chars/4 guess at the latest request, made to add up to its
  // real token count, so the shares sum to what was really in the window.
  const reconciled = reconcileBreakdown(usage.breakdown, usage.prompt);
  const parts = reconciled
    ? [...PARTS.map((part) => ({ ...part, value: reconciled.values[part.key] || 0 })).filter((part) => part.value > 0), ...(reconciled.other > 0 ? [{ key: "other", label: "Other", value: reconciled.other }] : [])]
        .map((part) => ({ ...part, share: (part.value / usage.prompt) * 100 }))
        .sort((a, b) => b.value - a.value)
    : [];

  // The session's cache hit rate: cache reads over every input token.
  const spends = Object.values(usage.byModel);
  const inputs = spends.reduce((sum, t) => sum + t.input + t.cache_read + t.cache_write, 0);
  const hitRate = inputs > 0 ? (spends.reduce((sum, t) => sum + t.cache_read, 0) / inputs) * 100 : null;

  const title = compacting
    ? "Compacting the conversation"
    : ring
      ? `${compactAt ? `Context ${formatTokens(usage.context)} of ${formatTokens(compactAt)} before auto-compact (${pct}%)` : `Context window ${pct}% full`} · ${formatTokens(total)} tokens this session`
      : `Token usage this session: ${formatTokens(total)}`;

  return (
    <div className="relative" onMouseEnter={enter} onMouseLeave={leave}>
      <button
        className="usage-chip inline-flex items-center gap-1.5 px-1.5 h-7 rounded-lg text-[12px] text-muted hover:text-ink shrink-0"
        onClick={() => setOpen(!open)}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label={compacting ? "Compacting the conversation" : ring ? `Context: ${pct}% used` : "Token usage"}
        data-level={level}
        title={title}
        data-testid="usage-chip"
      >
        {/* The ring is how full the context is. Beside the session's total it read as "the total is
            N% of the window", which it never was, so it is one or the other. */}
        {compacting ? (
          // The one time the ring turns; then it settles at its new, shorter length. Nothing is
          // written beside it (owner, 2026-10-13).
          <>
            <span className="usage-ring-busy" aria-hidden>
              <Ring segments={[{ color: "var(--accent)", share: 24 }]} />
            </span>
            <span className="sr-only" data-testid="usage-chip-compacting">
              Compacting
            </span>
          </>
        ) : ring ? (
          <>
            {/* The panel's bands, so the ring alone says "getting close". */}
            <Ring segments={[{ color: LEVEL_COLOR[level], share: pct as number }]} />
            {/* From "near" on, the figure is written too: full is said in words, not by colour. */}
            {level !== "ok" && (
              <span className="usage-ring-pct" data-testid="usage-chip-pct" style={{ color: LEVEL_COLOR[level] }}>
                {pct}%
              </span>
            )}
          </>
        ) : (
          <span className="tabular-nums">{formatTokens(total)}</span>
        )}
      </button>
      {open && (
        <div
          className="usage-popover composer-pop absolute z-40 bottom-full mb-1 right-0 w-[300px] max-w-[calc(100vw-24px)] rounded-xl border border-line bg-panel shadow-2xl flex flex-col [--pop-cap:560px]"
          role="dialog"
          aria-label="Context and usage"
          data-testid="usage-popover"
        >
          {/* The gauge and the parts stay put while the rest scrolls; the panel's cap keeps it in
              the window however many models the session used (owner, 2026-09-17). */}
          <div className="shrink-0 px-3 pt-3 space-y-2.5">
            <ContextGauge used={usage.context} compactAt={compactAt} window={contextWindow} />
            {parts.length > 0 && (
              <div data-testid="context-makeup">
                <div className="usage-sec">What fills it</div>
                <div className="flex flex-col">
                  {parts.map((part) => (
                    <div key={part.key} className="usage-fill-row" data-testid="context-makeup-row">
                      <span className="usage-fill-label">{part.label}</span>
                      <span className="usage-fill-bar" aria-hidden>
                        <i style={{ width: `${Math.min(100, part.share)}%` }} />
                      </span>
                      <span className="usage-fill-share">{part.share.toFixed(1)}%</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-3 pt-2.5 pb-3" data-testid="usage-popover-scroll">
            <div className="usage-sec">This session</div>
            <div className="flex flex-col gap-1.5">
              {Object.entries(usage.byModel).map(([id, spent]) => (
                <ModelUsageRow key={id} id={id} label={nameOf(id)} spent={spent} />
              ))}
            </div>
            <div className="mt-2 pt-2 border-t border-line flex items-baseline justify-between text-[12px]">
              <span className="text-faint">Total</span>
              <span className="text-ink tabular-nums">{formatTokens(total)} tokens</span>
            </div>
            {hitRate !== null && (
              <div className="mt-1.5">
                <Figure label="Average cache hit rate" value={`${hitRate.toFixed(1)}%`} />
              </div>
            )}
            {metrics && <CallsSection metrics={metrics} />}
            {model && !modelLabels?.[model] && contextWindow === undefined && <div className="mt-1 text-[11px] text-faint leading-snug">Context meter unavailable for custom models.</div>}
            {/* No compact button here (owner, 2026-09-16): the panel answers how full the window
                is. `/compact` in the composer is the one way to compact. */}
          </div>
        </div>
      )}
    </div>
  );
}
