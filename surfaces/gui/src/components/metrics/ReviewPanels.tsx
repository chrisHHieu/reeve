// The harness reviewing itself (2026-10-14): the failure shapes `scripts/harness_review.py` counts
// in the logs, each as a share of what it is counted against, and the verdict of every claim in
// docs/decisions/predictions.json. Both read the logs and spend no tokens; the server runs them in a
// child process every few hours and the panels say how old the result is.

import { useState } from "react";
import { refreshSelfReview, type OpsRefutedClaim, type OpsReview, type OpsShape } from "../../api";
import { Disclosure } from "../../ui";
import { compact, pct } from "./format";
import { EmptyNote, NUM, Panel } from "./primitives";

const SHOWN = 8;

function age(at: number | undefined): string {
  if (!at) return "";
  const minutes = Math.max(0, Math.round((Date.now() / 1000 - at) / 60));
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  return hours < 48 ? `${hours}h ago` : `${Math.round(hours / 24)}d ago`;
}

function shareTone(share: number | null): string {
  if (share == null) return "var(--accent)";
  return share >= 0.2 ? "var(--danger)" : share >= 0.03 ? "var(--warn-ink)" : "var(--accent)";
}

function ShapeRow({ shape }: { shape: OpsShape }) {
  return (
    <li className="grid grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)_76px] items-center gap-3 py-[7px]" data-testid="shape">
      <div className="min-w-0">
        <div className="truncate font-mono text-[12px] text-ink">{shape.name}</div>
        <div className="truncate text-[11.5px] text-faint">{shape.owner}</div>
      </div>
      <span className="h-1.5 overflow-hidden rounded-full bg-paper">
        <i className="block h-full rounded-full" style={{ width: `${Math.max(2, (shape.share ?? 0) * 100)}%`, background: shareTone(shape.share) }} />
      </span>
      <div className={`text-right ${NUM}`}>
        <div className="text-[12.5px] font-medium text-ink">{compact(shape.count)}</div>
        <div className="truncate text-[11px] text-muted" title={shape.per ? `of ${shape.of} ${shape.per}` : undefined}>
          {shape.share != null ? pct(shape.share, 1) : "count"}
        </div>
      </div>
    </li>
  );
}

/** A claim's two sides as its share, the way the ledger reads it. */
function side(pair: [number, number] | null, expect: string): string {
  if (!pair) return "—";
  const [part, whole] = pair;
  if (expect === "zero" || expect === "nonzero") return `${part} of ${whole}`;
  if (!whole) return "—";
  const value = part / whole;
  return value < 1 ? pct(value, value < 0.1 ? 2 : 1) : compact(Math.round(value));
}

const VERDICTS: { key: string; label: string; color: string }[] = [
  { key: "confirmed", label: "Confirmed", color: "var(--ok)" },
  { key: "refuted", label: "Refuted", color: "var(--danger)" },
  { key: "too early", label: "Too early", color: "color-mix(in srgb, var(--ink) 30%, var(--panel))" },
  { key: "not live", label: "Not live", color: "color-mix(in srgb, var(--ink) 14%, var(--panel))" },
];

function Refuted({ claim }: { claim: OpsRefutedClaim }) {
  return (
    <div className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-3 border-t border-line/60 py-2.5" title={claim.claim} data-testid="refuted-claim">
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          {claim.adr && <span className={`rounded border border-line px-1 text-[11px] text-muted ${NUM}`}>{claim.adr}</span>}
          <span className="truncate font-mono text-[12px] text-ink">{claim.id}</span>
        </div>
        <div className="mt-0.5 truncate text-[11.5px] text-muted">{claim.metric} per {claim.per}</div>
      </div>
      <div className={`text-right text-[12px] ${NUM}`}>
        <span className="text-muted">{side(claim.before, claim.expect)}</span>
        <span className="text-faint"> → </span>
        <span className="font-medium text-danger">{side(claim.after, claim.expect)}</span>
        <div className="text-[11px] text-faint">expected {claim.expect}</div>
      </div>
    </div>
  );
}

export function ReviewPanels({ review }: { review: OpsReview | null | undefined }) {
  const [all, setAll] = useState(false);
  const [asked, setAsked] = useState(false);
  if (review == null) {
    return (
      <Panel title="Failure shapes and claims" hint="harness_review · predictions.json">
        <EmptyNote>This build carries no review scripts.</EmptyNote>
      </Panel>
    );
  }
  const running = review.running || asked;
  const shapes = review.shapes ?? [];
  const verdicts = review.claims?.verdicts ?? {};
  const totalClaims = VERDICTS.reduce((sum, v) => sum + (verdicts[v.key] ?? 0), 0);
  const refuted = review.claims?.refuted ?? [];
  const open = refuted.filter((claim) => !claim.resolution);
  const status = (
    <span className="inline-flex items-center gap-2 text-[11.5px] text-muted" data-testid="review-status">
      {running ? "Reviewing the logs…" : review.at ? `as of ${age(review.at)}` : "no review yet"}
      {review.error && !running && <span className="text-warnInk">· last run: {review.error}</span>}
      <button
        type="button"
        disabled={running}
        onClick={() => {
          setAsked(true);
          void refreshSelfReview().finally(() => window.setTimeout(() => setAsked(false), 4000));
        }}
        className="rounded-md px-1.5 py-0.5 font-medium text-accent transition-colors duration-150 hover:text-ink disabled:text-faint"
        data-testid="review-refresh"
      >
        Refresh
      </button>
    </span>
  );
  return (
    <div className="grid grid-cols-2 gap-4 max-[1100px]:grid-cols-1">
      <Panel title="Failure shapes" hint={`last ${review.days ?? 30} days · 0 tokens`} action={status}>
        {shapes.length === 0 ? (
          <EmptyNote>{review.at ? "No known failure shape in these logs." : "The first review is running."}</EmptyNote>
        ) : (
          <div data-testid="failure-shapes">
            <ul className="flex flex-col">
              {shapes.slice(0, SHOWN).map((shape) => <ShapeRow key={shape.name} shape={shape} />)}
            </ul>
            {shapes.length > SHOWN && (
              <>
                <Disclosure open={all}>
                  <ul className="flex flex-col">
                    {shapes.slice(SHOWN).map((shape) => <ShapeRow key={shape.name} shape={shape} />)}
                  </ul>
                </Disclosure>
                <button type="button" onClick={() => setAll((v) => !v)} className="mt-1 text-[12px] font-medium text-accent transition-colors duration-150 hover:text-ink">
                  {all ? "Show fewer" : `Show all ${shapes.length}`}
                </button>
              </>
            )}
          </div>
        )}
      </Panel>
      <Panel title="Claims" hint="each change's prediction, judged on the store">
        {totalClaims === 0 ? (
          <EmptyNote>{review.at ? "No claim in the ledger." : "The first review is running."}</EmptyNote>
        ) : (
          <div data-testid="claims">
            <div className="mt-1 flex h-2 gap-0.5 overflow-hidden rounded-full" aria-label={`${totalClaims} claims`}>
              {VERDICTS.filter((v) => verdicts[v.key]).map((v) => (
                <i key={v.key} className="block h-full" style={{ flex: `${verdicts[v.key]} 1 0`, background: v.color }} />
              ))}
            </div>
            <div className="mt-2.5 flex flex-wrap gap-x-4 gap-y-1 text-[12px] text-muted">
              {VERDICTS.map((v) => (
                <span key={v.key} className="inline-flex items-center gap-1.5">
                  <i className="h-2 w-2 rounded-[2px]" style={{ background: v.color }} />
                  {v.label} <b className={`font-medium text-ink ${NUM}`}>{verdicts[v.key] ?? 0}</b>
                </span>
              ))}
            </div>
            {open.length > 0 ? (
              <div className="mt-3">
                <div className="pb-1.5 text-[11.5px] text-muted">Refuted with no resolution yet: each reopens its change</div>
                {open.map((claim) => <Refuted key={claim.id} claim={claim} />)}
              </div>
            ) : (
              <p className="mt-3 text-[12px] text-muted">No refuted claim is waiting for a resolution.</p>
            )}
          </div>
        )}
      </Panel>
    </div>
  );
}
