import { fetch, httpBase } from "./base";

// -- the Ops surface ------------------------------------------------------------
// One payload per range, assembled server-side from the session_metrics fold
// (harness/server/manager/metrics_store.py). Nothing here recomputes a number the
// server already folded; the view only formats.
export type OpsRange = "today" | "7d" | "30d" | "all";

export interface OpsHeadline {
  sessions: number;
  turns_started: number;
  turns_completed: number;
  turns_failed: number;
  completion: number | null;
  tool_calls: number;
  tool_ok: number;
  tool_success: number | null;
  tool_errors: number;
  tool_timeouts: number;
  blocked: number;
  llm_calls: number;
  retries: number;
  retry_rate: number | null;
  model_errors: number;
  tokens_in: number;
  tokens_out: number;
  cache_read: number;
  cache_write: number;
  /** Everything that crossed the wire in the range (in + out + cache read + cache write). */
  tokens_total: number;
  /** Prompt tokens the provider's cache served — the cheap half of the input. */
  cache_served: number;
  cache_hit: number | null;
  tokens_per_turn: number | null;
  ttft_avg_ms: number | null;
  ttft_p95_ms: number | null;
  steps: number;
  steps_per_turn: number | null;
  loop_flags: number;
  failed_spend: number | null;
  compactions: number;
  subagents: number;
}

export interface OpsDaily {
  day: string;
  turns: number;
  failed: number;
  llm_calls: number;
  tool_calls: number;
  tool_failures: number;
  tool_error_rate: number | null;
  completion: number | null;
  steps: number;
  steps_per_turn: number | null;
  loops: number;
  blocked: number;
  tokens_in: number;
  tokens_out: number;
  cache_read: number;
  cache_write: number;
  tokens: number;
  ttft_avg_ms: number | null;
  ttft_p95_ms: number | null;
  wall_ms: number;
}

export interface OpsBreakdown {
  /** Only on `by_model` rows: the upstream that served the model ("openai", "anthropic"). */
  provider?: string;
  /** Output tokens; `tokens` on a row is in + out + cache read + cache write. */
  tokens_out: number;
  name: string;
  sessions: number;
  turns: number;
  llm_calls: number;
  retries: number;
  retry_rate: number | null;
  tokens: number;
  tokens_in: number;
  cache_read: number;
  cache_write: number;
  tool_calls: number;
  tool_failures: number;
  tool_success: number | null;
  cache_hit: number | null;
  steps: number;
  steps_per_turn: number | null;
  ttft_avg_ms: number | null;
  /** On `by_model` rows: the harness numbers of the sessions that ran on this model (2026-10-14). */
  harness?: OpsModelHarness;
}

/** A model's (or a span's) harness numbers, with the definitions the headline uses. */
export interface OpsModelHarness {
  turns: number;
  turns_failed: number;
  completion: number | null;
  tool_success: number | null;
  steps_per_turn: number | null;
  tokens_per_turn: number | null;
  ttft_p95_ms: number | null;
}

export interface OpsTool {
  name: string;
  calls: number;
  errors: number;
  timeouts: number;
  blocked: number;
  p95_ms: number | null;
}

export interface OpsFailure {
  session_id: string;
  turn: number;
  day: string;
  agent: string;
  tool: string;
  status: string;
  reason: string;
}

export interface OpsNotable {
  day: string;
  level: "ok" | "warn" | "err";
  kind: string;
  z: number | null;
  text: string;
}

/** Harness-health fold summed over the range (harness/metrics_harness.py). Deleted
 *  sessions are included: their numbers stay (owner call, 2026-09-25). */
export interface OpsHarness {
  spend: {
    turn_total: number;
    main: number;
    summarizer: number;
    prepare: number;
    delegated: number;
    unattributed: number;
    unattributed_pct: number | null;
  };
  stalls: { compaction: number; "provider retry": number; unexplained: number };
  ttft: { band: string; p50_ms: number; p90_ms: number; samples: number }[];
  compaction: Record<string, number>;
  prepares: Record<string, number>;
  usage_partial: { model: string; sessions: number }[];
  /** The checks that had only a range total, per day (2026-10-13). Older servers omit it. */
  daily?: OpsHarnessDay[];
}

export interface OpsHarnessDay {
  day: string;
  turn_total: number;
  unattributed: number;
  unattributed_pct: number | null;
  unexplained: number;
  compaction_failed: number;
  usage_partial: number;
}

/** Tokens and calls in the span just before the one on screen (null for all time). */
export interface OpsPrevious {
  since: string;
  until: string;
  calls: number;
  tokens_in: number;
  tokens_out: number;
  cache_read: number;
  cache_write: number;
  tokens: number;
  cache_hit: number | null;
  /** The same span's harness numbers, for each tile's change (2026-10-14). */
  harness?: OpsModelHarness;
}

export interface OpsShip {
  day: string;
  adrs: string[];
}

/** One failure shape the review counted (scripts/harness_review.py), as a share where it has a base. */
export interface OpsShape {
  name: string;
  owner: string;
  count: number;
  per: string | null;
  of: number | null;
  share: number | null;
}

export interface OpsRefutedClaim {
  id: string;
  adr: string | null;
  claim: string;
  metric: string;
  per: string;
  expect: string;
  before: [number, number] | null;
  after: [number, number] | null;
  resolution: string;
}

export interface OpsReview {
  /** When the result was computed (unix seconds); absent before the first run lands. */
  at?: number;
  days?: number;
  shapes?: OpsShape[];
  claims?: { verdicts: Record<string, number>; refuted: OpsRefutedClaim[] };
  /** The last run failed; the result above is the one before it. */
  error?: string;
  running: boolean;
}

/** Tokens per (day, provider) from the durable rollup — each call on the day it ran. */
export interface OpsUsageDay {
  day: string;
  provider: string;
  calls: number;
  tokens_in: number;
  tokens_out: number;
  cache_read: number;
  cache_write: number;
  tokens: number;
}

/** Spend by work that runs outside any turn: titles, memory passes, commit messages. */
export interface OpsBackground {
  purpose: string;
  calls: number;
  tokens: number;
}

export interface OpsMetrics {
  range: OpsRange;
  backfilling: boolean;
  headline: OpsHeadline;
  daily: OpsDaily[];
  by_model: OpsBreakdown[];
  tools: OpsTool[];
  failures: OpsFailure[];
  notable: OpsNotable[];
  harness?: OpsHarness;
  usage_daily?: OpsUsageDay[];
  background?: OpsBackground[];
  previous?: OpsPrevious | null;
  /** The days a harness change went live (the ledger's `shipped`), for the day charts' marks. */
  ships?: OpsShip[];
  /** The harness's last review of its own logs; null where the build has no review scripts. */
  review?: OpsReview | null;
}

/** The server's `metrics_changed` push (a step of some session finished and was folded into the store),
 *  re-announced on `window` so the Metrics page hears it without opening a second socket. */
export const METRICS_CHANGED = "reeve:metrics-changed";

export async function getOpsMetrics(range: OpsRange): Promise<OpsMetrics> {
  const res = await fetch(`${httpBase()}/v1/metrics?range=${encodeURIComponent(range)}`);
  return res.json();
}

/** Start the self-review now; the result arrives with the next `metrics_changed`. */
export async function refreshSelfReview(): Promise<{ started: boolean }> {
  const res = await fetch(`${httpBase()}/v1/metrics/review`, { method: "POST" });
  return res.json();
}
