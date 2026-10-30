// What the compacting line says: that the summarizer runs, why it runs, and how big the context
// is. "Compacting the conversation · 161k tokens" beside a gauge at 34% read as a gauge
// that had not caught up (owner, 2026-10-30), when it was a stage cut working as meant.

import { formatTokens } from "../../usage";

const WHY: Record<string, string> = {
  full: "the context is near its limit",
  stage: "a stage of the work just finished",
  overflow: "the last request did not fit",
};

export function compactingWords(why: string | null | undefined, tokens: number): string {
  const parts = ["Compacting the conversation"];
  const reason = why ? WHY[why] : undefined;
  if (reason) parts.push(reason);
  if (tokens > 0) parts.push(`${formatTokens(tokens)} tokens`);
  return parts.join(" · ");
}
