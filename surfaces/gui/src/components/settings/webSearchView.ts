// Settings ▸ Web search, in words — pure functions, kept out of the component file (a module
// that mixes component and non-component exports cannot Fast Refresh).

import type { WebSearchSettings, WebSearchTest } from "../../api/webSearch";

export type Tone = "ok" | "warn" | "danger";

/** A test whose first choice was refused for its key (Exa or Tavily answered 401/403). */
export function keyRejected(test: WebSearchTest | null): boolean {
  return Boolean(test?.fallback_from && /\b(401|403)\b/.test(test.fallback_reason ?? ""));
}

/** The page's state at a glance: which service searches go to, on whose key, and what steps in. */
export function searchState(
  settings: WebSearchSettings,
  test: WebSearchTest | null = null,
): { tone: Tone; word: string; line: string } {
  if (settings.mode === "duckduckgo") {
    return { tone: "ok", word: "DuckDuckGo", line: "No key needed. Results are shorter and carry no dates." };
  }
  if (keyRejected(test)) {
    const who = nameOf(test?.fallback_from) || "Exa";
    return {
      tone: "warn",
      word: `${who} rejected the key`,
      line: "Searches still work: DuckDuckGo answers them until the key is replaced.",
    };
  }
  if (settings.mode === "tavily") {
    if (engineOf(settings) !== "tavily") {
      return { tone: "warn", word: "Tavily has no key", line: "Searches use Exa until a Tavily key is added." };
    }
    const whose = settings.tavily?.key_source === "environment" ? "the key from TAVILY_API_KEY" : "your key";
    return { tone: "ok", word: "Tavily", line: `With ${whose}. DuckDuckGo steps in if Tavily fails.` };
  }
  const whose =
    settings.key_source === "settings"
      ? "your key"
      : settings.key_source === "environment"
        ? "the key from EXA_API_KEY"
        : settings.key_source === "connector"
          ? "the Exa connector's key"
          : null;
  if (!whose) {
    return {
      tone: "ok",
      word: "Exa, free shared access",
      line: "About 150 searches a day for everyone on this network. DuckDuckGo steps in after that.",
    };
  }
  return { tone: "ok", word: "Exa", line: `With ${whose}. DuckDuckGo steps in if Exa fails.` };
}

/** Where a search really starts (an older server sends no `engine`). */
export function engineOf(settings: WebSearchSettings): "exa" | "tavily" | "duckduckgo" {
  if (settings.engine) return settings.engine;
  return settings.mode === "auto" ? "exa" : settings.mode;
}

/** The Exa account row: its title and one line about the key a search would use. */
export function keyTitle(settings: WebSearchSettings): string {
  switch (settings.key_source) {
    case "settings":
      return "Your key";
    case "environment":
      return "Key from the environment";
    case "connector":
      return "Key from the Exa connector";
    default:
      return "No key";
  }
}

export function keyDetail(settings: WebSearchSettings): string {
  switch (settings.key_source) {
    case "settings":
      return `Saved${settings.key_set_at ? ` ${settings.key_set_at}` : ""}. Searches use your Exa account.`;
    case "environment":
      return "EXA_API_KEY is set where Reeve runs. Add a key here to use that one instead.";
    case "connector":
      return "Saved by the Exa connector. Add a key here to use that one instead.";
    default:
      return "Exa's free shared access: about 150 searches a day for everyone on this network. A key of your own lifts that.";
  }
}

/** The Tavily account row: its title and one line about the key. */
export function tavilyKeyTitle(settings: WebSearchSettings): string {
  const source = settings.tavily?.key_source;
  return source === "settings" ? "Your key" : source === "environment" ? "Key from the environment" : "No key";
}

export function tavilyKeyDetail(settings: WebSearchSettings): string {
  const t = settings.tavily;
  if (t?.key_source === "settings") {
    return `Saved${t.key_set_at ? ` ${t.key_set_at}` : ""}. Searches use your Tavily account, one credit each.`;
  }
  if (t?.key_source === "environment") return "TAVILY_API_KEY is set where Reeve runs. Add a key here to use that one instead.";
  return "Tavily needs a key. The free plan covers about 1,000 searches a month.";
}

const NAMES: Record<string, string> = { exa: "Exa", duckduckgo: "DuckDuckGo", tavily: "Tavily" };
const nameOf = (raw?: string) => (raw ? (NAMES[raw.toLowerCase()] ?? raw) : "");

/** One line about a test search, and how it should read. */
export function testVerdict(test: WebSearchTest): { tone: Tone; text: string } {
  if (!test.ok && !test.provider) {
    return { tone: "danger", text: test.error || "The test search failed." };
  }
  const took = typeof test.ms === "number" ? ` in ${(test.ms / 1000).toFixed(1)} s` : "";
  const count = test.results ?? 0;
  const found = `${count} result${count === 1 ? "" : "s"}${took}`;
  if (test.fallback_from) {
    const from = nameOf(test.fallback_from);
    const why = keyRejected(test) ? `${from} rejected the key` : `${from} did not answer (${test.fallback_reason || "no reason given"})`;
    return { tone: "warn", text: `${why}, so ${nameOf(test.provider)} answered: ${found}.` };
  }
  if (!test.ok) return { tone: "danger", text: `${nameOf(test.provider)}: ${test.error || "no results"}` };
  return { tone: "ok", text: `${nameOf(test.provider)} answered: ${found}.` };
}

/** Connectors that bring search tools of their own, shown on this page so the two meet. */
export const SEARCH_CONNECTORS = ["firecrawl"];
