// What a session has spent, from the `usage` sidecar the server puts on assistant messages and on
// `assistant_message` ({model, input, output, cache_read, cache_write}, and the latest request's
// `context_breakdown`). A server or provider that reports none leaves everything at zero.

import type { ConversationMessage } from "./api";
import type { ContextBreakdown, SessionUsage, StepTokens, TurnUsage } from "./types";

export function emptyUsage(): SessionUsage {
  return { byModel: {}, context: 0, prompt: 0 };
}

/** A count from the wire: a positive finite number, else 0. */
const count = (v: unknown): number => {
  const n = Number(v);
  return Number.isFinite(n) && n > 0 ? n : 0;
};

const isObject = (raw: unknown): raw is Record<string, unknown> => !!raw && typeof raw === "object";

const ZERO: StepTokens = { input: 0, output: 0, cache_read: 0, cache_write: 0 };

/** The four billing classes of a sidecar, each read as a count. */
function classes(raw: Record<string, unknown>): StepTokens {
  return { input: count(raw.input), output: count(raw.output), cache_read: count(raw.cache_read), cache_write: count(raw.cache_write) };
}

export function tokensTotal(t: StepTokens): number {
  return t.input + t.output + t.cache_read + t.cache_write;
}

/** Two spends added; either may be absent. */
export function addTokens(a?: StepTokens, b?: StepTokens): StepTokens | undefined {
  if (!a || !b) return a ?? b;
  return { input: a.input + b.input, output: a.output + b.output, cache_read: a.cache_read + b.cache_read, cache_write: a.cache_write + b.cache_write };
}

/** One step's spend from its sidecar, or undefined when it reported none. */
export function stepTokens(raw: unknown): StepTokens | undefined {
  if (!isObject(raw)) return undefined;
  const t = classes(raw);
  return tokensTotal(t) > 0 ? t : undefined;
}

/** What a turn spent: the tokens of every step its items carry, cache reads included — what the
 *  account is billed for, the count the top bar totals. */
export function turnTokens(items: readonly object[]): StepTokens | undefined {
  let sum: StepTokens | undefined;
  for (const item of items) sum = addTokens(sum, (item as { tokens?: StepTokens }).tokens);
  return sum;
}

// The parts of a request, in the order the usage popover lists them.
const PARTS = ["messages", "mcp_tools", "system_tools", "skills", "instructions", "system_prompt", "meta_context"] as const;

/** The parts the server measured, zeros left out; undefined when it measured none, so the popover
 *  leaves the section out. */
function breakdownOf(raw: unknown): ContextBreakdown | undefined {
  if (!isObject(raw)) return undefined;
  const parts: ContextBreakdown = {};
  for (const part of PARTS) if (count(raw[part])) parts[part] = count(raw[part]);
  return Object.keys(parts).length ? parts : undefined;
}

/** One request's sidecar added to the session. The sums grow per model; `context` and `prompt` are
 *  the latest request's alone, since each request sends the whole history again. `context` counts
 *  the output too, which the next request carries; `prompt` is what the breakdown describes. */
export function addTurnUsage(prev: SessionUsage, raw: unknown): SessionUsage {
  if (!isObject(raw)) return prev;
  const model = typeof raw.model === "string" && raw.model ? raw.model : null;
  const spent = classes(raw);
  const key = model || "unknown";
  const sum: TurnUsage = { model, ...(addTokens(prev.byModel[key] ?? ZERO, spent) as StepTokens) };
  const prompt = spent.input + spent.cache_read + spent.cache_write;
  const next: SessionUsage = { byModel: { ...prev.byModel, [key]: sum }, context: prompt + spent.output, prompt };
  const breakdown = breakdownOf(raw.context_breakdown);
  if (breakdown) next.breakdown = breakdown;
  return next;
}

/** The session's spend again from its transcript, as a session opens. */
export function usageFromMessages(messages: ConversationMessage[]): SessionUsage {
  let usage = emptyUsage();
  for (const m of messages || []) if (m.role === "assistant" && m.usage) usage = addTurnUsage(usage, m.usage);
  return usage;
}

/** `context` set to a size the engine measured, the sums untouched. After a compaction no request
 *  has been made, so the provider's last count describes a history no longer sent; the engine's
 *  `now_tokens` moves the meter now rather than a turn later. `prompt` and the breakdown still
 *  describe the last request, so they stay. */
export function withContext(prev: SessionUsage, tokens: unknown): SessionUsage {
  const n = count(tokens);
  return n ? { ...prev, context: n } : prev;
}

/** The breakdown made to add up to the request's real token count. chars/4 is no tokenizer and ran
 *  about 48% over a real prompt (397k summed against 268k), so parts over the count are scaled down
 *  to it, and a count over the parts leaves the rest as `other` — schemas, wrappers, what the engine
 *  does not itemize. Either way the parts sum to the prompt, and the percentages mean something. */
export function reconcileBreakdown(breakdown: ContextBreakdown | undefined, prompt: number): { values: ContextBreakdown; other: number } | undefined {
  if (!breakdown || !(prompt > 0)) return undefined;
  const parts = (Object.entries(breakdown) as [keyof ContextBreakdown, number | undefined][]).filter(([, v]) => (v || 0) > 0) as [keyof ContextBreakdown, number][];
  const sum = parts.reduce((total, [, v]) => total + v, 0);
  if (sum <= 0) return undefined;
  const scale = Math.min(1, prompt / sum);
  const values: ContextBreakdown = {};
  let used = 0;
  for (const [part, v] of parts) {
    values[part] = v * scale;
    used += v * scale;
  }
  return { values, other: Math.max(0, prompt - used) };
}

/** Every token the session spent, over models and classes: the usage chip's headline. */
export function totalTokens(u: SessionUsage): number {
  return Object.values(u.byModel).reduce((total, t) => total + tokensTotal(t), 0);
}

/** 980 → "980", 12_400 → "12.4k", 982_000 → "982k", 1_240_000 → "1.24M". */
export function formatTokens(n: number): string {
  if (!Number.isFinite(n) || n <= 0) return "0";
  if (n < 1000) return String(Math.round(n));
  const [value, unit, places] = n < 1_000_000 ? [n / 1000, "k", 1] : [n / 1_000_000, "M", 2];
  return (value < 100 ? value.toFixed(places).replace(/\.?0+$/, "") : String(Math.round(value))) + unit;
}
