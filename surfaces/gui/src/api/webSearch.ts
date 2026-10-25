// Settings ▸ Web search: where the agent's web searches go, the key they use, and a test.
//
// The engine resolves the provider on every search, so a change here applies to the next one
// in every conversation. The server never returns a key: only whether one is set and where it
// comes from. Every call goes through this package's own `fetch` (api/base.ts), which attaches
// the launch token.

import { fetch, httpBase } from "./base";

/** `auto` = Exa, then DuckDuckGo when Exa errs or finds nothing. `tavily` = Tavily, then
 * DuckDuckGo. `duckduckgo` = only that. */
export type WebSearchMode = "auto" | "tavily" | "duckduckgo";

export interface WebSearchSettings {
  mode: WebSearchMode;
  /** Where a search goes, as the approval card names it (`exa → duckduckgo`). */
  destination: string;
  /** A key is saved here (Settings). */
  has_key: boolean;
  /** Where the Exa key a search would use comes from, in the order the engine reads them. */
  key_source: "settings" | "environment" | "connector" | null;
  /** The day the saved key was set (YYYY-MM-DD). */
  key_set_at?: string | null;
  /** Where a search really starts. Differs from `mode` when Tavily is chosen with no key:
   * searches then start at Exa. */
  engine?: "exa" | "tavily" | "duckduckgo";
  /** Tavily's key: saved here, or `TAVILY_API_KEY` in the environment. */
  tavily?: { has_key: boolean; key_source: "settings" | "environment" | null; key_set_at?: string | null };
}

export interface WebSearchChange {
  mode: WebSearchMode;
  apiKey?: string;
  removeKey?: boolean;
  tavilyKey?: string;
  removeTavilyKey?: boolean;
}

export interface WebSearchTest {
  ok: boolean;
  query: string;
  /** The service that ANSWERED. */
  provider?: string;
  results?: number;
  ms?: number;
  first?: { title: string; url: string };
  /** The service that was asked first and did not answer, and what it said. */
  fallback_from?: string;
  fallback_reason?: string;
  error?: string;
}

export async function getWebSearch(): Promise<WebSearchSettings> {
  const res = await fetch(`${httpBase()}/v1/web-search`);
  return res.json();
}

export async function setWebSearch(change: WebSearchChange): Promise<{ ok: boolean; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/web-search`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      provider: change.mode,
      ...(change.apiKey ? { api_key: change.apiKey } : {}),
      ...(change.removeKey ? { remove_key: true } : {}),
      ...(change.tavilyKey ? { tavily_api_key: change.tavilyKey } : {}),
      ...(change.removeTavilyKey ? { remove_tavily_key: true } : {}),
    }),
  });
  return res.json().catch(() => ({ ok: false, error: "the server did not answer" }));
}

export async function testWebSearch(query: string): Promise<WebSearchTest> {
  const res = await fetch(`${httpBase()}/v1/web-search/test`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query }),
  });
  return res.json().catch(() => ({ ok: false, query, error: "the server did not answer" }));
}
