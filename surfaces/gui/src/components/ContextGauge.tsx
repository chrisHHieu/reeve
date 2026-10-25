// The context gauge in the usage popover: how much of the conversation's working room is
// used, measured against where the session AUTO-COMPACTS — the only number that means
// something to a person watching it. "222k of 1M · 22%" (the model's catalogue window) read
// as plenty of room in a session about to compact at 380k (owner, 2026-09-28).
//
// The model's own maximum stays on the quiet line below, with why the session works under
// it; without a compaction point from the server (an older one) the window is the only
// honest denominator and is labelled as such.

import { formatTokens } from "../usage";

export type GaugeLevel = "ok" | "near" | "soon";

/** How close the session is to compacting: calm under 70%, near from 70%, soon from 90%. */
export function gaugeLevel(pct: number): GaugeLevel {
  return pct >= 90 ? "soon" : pct >= 70 ? "near" : "ok";
}

export function ContextGauge({
  used,
  compactAt,
  window,
}: {
  used: number;
  compactAt?: number;
  window?: number;
}) {
  const point = compactAt && compactAt > 0 ? compactAt : undefined;
  const budget = point ?? window;
  if (!budget) {
    return used > 0 ? (
      <div className="ctx-gauge-line" data-testid="context-gauge-used">
        {formatTokens(used)} in context
      </div>
    ) : null;
  }
  const pct = Math.min(100, Math.round((used / budget) * 100));
  const level = point ? gaugeLevel(pct) : "ok";
  const left = Math.max(0, budget - used);
  return (
    <div className="ctx-gauge" data-testid="context-gauge" data-level={level}>
      <div className="ctx-gauge-head">
        <span className="ctx-gauge-title">{point ? "Context" : "Context window"}</span>
        <span className="ctx-gauge-pct" data-testid="context-gauge-pct">
          {pct}%
        </span>
      </div>
      <div
        className="ctx-gauge-track"
        role="meter"
        aria-valuemin={0}
        aria-valuemax={budget}
        aria-valuenow={Math.min(used, budget)}
        aria-label={point ? "Context used before auto-compaction" : "Context window used"}
      >
        <div className="ctx-gauge-fill" style={{ width: `${pct}%` }} />
        {point && <span className="ctx-gauge-mark" aria-hidden />}
      </div>
      {point ? (
        <>
          <div className="ctx-gauge-line" data-testid="context-gauge-used">
            {used >= point
              ? `${formatTokens(used)} used · auto-compacts on the next step`
              : `${formatTokens(used)} used · ${formatTokens(left)} until auto-compact`}
          </div>
          <div
            className="ctx-gauge-note"
            data-testid="context-gauge-limit"
            title={
              window && window > point
                ? "The conversation is kept under this point so the model stays accurate; older turns are summarized when it is reached. Change the cap in Settings ▸ Compaction."
                : "Older turns are summarized when the conversation reaches this point."
            }
          >
            Auto-compacts at {formatTokens(point)}
            {window && window > point ? ` · model max ${formatTokens(window)}` : ""}
          </div>
        </>
      ) : (
        <div className="ctx-gauge-line" data-testid="context-gauge-used">
          {formatTokens(used)} of {formatTokens(budget)} · {pct}%
        </div>
      )}
    </div>
  );
}
