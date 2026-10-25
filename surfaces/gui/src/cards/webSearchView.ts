// A web search result, in the words and marks the card shows — pure functions, kept out of the
// component file (a module that mixes component and non-component exports cannot Fast Refresh).

/** Words a search carries that say nothing about the page: marking them would light up half
 *  of every snippet. English only, on purpose — the marks are a reading aid, not a filter. */
const QUIET = new Set(
  "a an and are as at be by for from has have how in into is it its of on or that the this to was what when where which who why will with".split(
    " ",
  ),
);

/** The query's own terms worth marking: three letters or more, not a filler word, each once. */
export function queryTerms(query?: string): string[] {
  const seen = new Set<string>();
  for (const raw of (query ?? "").split(/[\s"“”'’,;:()]+/)) {
    // Punctuation at the ends is the sentence's, not the word's — but `C++` and `C#` keep theirs.
    const term = raw.replace(/^[^\p{L}\p{N}]+|[^\p{L}\p{N}+#]+$/gu, "");
    if (term.length < 3 || QUIET.has(term.toLowerCase())) continue;
    seen.add(term.toLowerCase());
  }
  return [...seen];
}

/** Split `text` into runs, `hit` where a WHOLE term matches. A term inside a longer word is
 *  not a hit: "in" was being marked inside "window" and "working". */
export function markTerms(text: string, terms: readonly string[]): Array<{ text: string; hit: boolean }> {
  if (terms.length === 0 || !text) return [{ text, hit: false }];
  const escaped = terms.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  // Unicode-aware word edges, so a Vietnamese or accented word is one word.
  const pattern = new RegExp(`(?<![\\p{L}\\p{N}])(${escaped.join("|")})(?![\\p{L}\\p{N}])`, "giu");
  const runs: Array<{ text: string; hit: boolean }> = [];
  let last = 0;
  for (const match of text.matchAll(pattern)) {
    const at = match.index ?? 0;
    if (at > last) runs.push({ text: text.slice(last, at), hit: false });
    runs.push({ text: match[0], hit: true });
    last = at + match[0].length;
  }
  if (last < text.length) runs.push({ text: text.slice(last), hit: false });
  return runs;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** When a page was published, as a row says it: `today`, `yesterday`, `3 days ago` within a
 *  week, then `5 Oct`, then `5 Oct 2025` in another year. Counted in calendar days: a provider
 *  often gives a date with a midnight time, and "14 hours ago" would invent the hour. `title`
 *  is the full date, for the hover. Empty when there is no usable date. */
export function publishedLabel(raw: string | undefined, now: Date = new Date()): { text: string; title: string } {
  if (!raw) return { text: "", title: "" };
  const at = new Date(raw);
  if (Number.isNaN(at.getTime())) return { text: "", title: "" };
  const day = (d: Date) => Date.UTC(d.getFullYear(), d.getMonth(), d.getDate());
  const days = Math.round((day(now) - day(at)) / 86_400_000);
  const title = at.toLocaleDateString(undefined, { year: "numeric", month: "long", day: "numeric" });
  if (days < 0) return { text: `${at.getDate()} ${MONTHS[at.getMonth()]}`, title };
  if (days === 0) return { text: "today", title };
  if (days === 1) return { text: "yesterday", title };
  if (days < 7) return { text: `${days} days ago`, title };
  const sameYear = at.getFullYear() === now.getFullYear();
  return {
    text: `${at.getDate()} ${MONTHS[at.getMonth()]}${sameYear ? "" : ` ${at.getFullYear()}`}`,
    title,
  };
}

/** A provider's wire name as a person reads it. */
export function providerLabel(raw: string | undefined): string {
  const name = (raw ?? "").trim();
  if (!name) return "";
  if (name.toLowerCase() === "duckduckgo") return "DuckDuckGo";
  if (name.toLowerCase() === "exa") return "Exa";
  if (name.toLowerCase() === "tavily") return "Tavily";
  return name.charAt(0).toUpperCase() + name.slice(1);
}

/** The bare host, without `www.`. */
export function hostOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "") || url;
  } catch {
    return url;
  }
}

/** How long a snippet may be before the row shows it clamped with a "more" control. */
export const SNIPPET_CLAMP_CHARS = 220;
