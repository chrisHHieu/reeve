// What the fetch card says, in words and numbers, apart from how it draws them (WebBlock.tsx).
//
// A fetch reads a page one of three ways, and the card's job is to say which and what came of
// it: the start of the page (and how much of it the model has now read), an answer a model
// wrote from the page with each quote checked against it, or the places a phrase occurs. Around
// that, the facts a reader would otherwise have to ask for: did the site answer (the status, in
// a tone), is this the whole page, was the menu left out, was it this session's copy.

import type { WebFetchCard } from "./types";
import { hostOf } from "./webSearchView";

export type Tone = "ok" | "neutral" | "warn" | "danger";

/** A status in the tone of what it means: an answer, a move, or a refusal. */
export function statusTone(code: number | undefined): Tone | null {
  if (code === undefined) return null;
  if (code >= 200 && code < 300) return "ok";
  if (code >= 400) return "danger";
  return "neutral";
}

/** A character count the way a reader takes it in: 687, 6.9k, 54k, 1.2M. */
export function compactCount(n: number): string {
  if (n < 1000) return String(n);
  if (n < 10_000) return `${(n / 1000).toFixed(1).replace(/\.0$/, "")}k`;
  if (n < 1_000_000) return `${Math.round(n / 1000)}k`;
  return `${(n / 1_000_000).toFixed(1).replace(/\.0$/, "")}M`;
}

/** The address as a trail — the host, then the path: `docs.python.org`, `3 › library ›
 *  asyncio-task.html`. A deep path keeps its two ends: where the site starts and the page. */
export function addressTrail(url: string): { host: string; path: string } {
  let parts: string[] = [];
  try {
    parts = new URL(url).pathname.split("/").filter(Boolean).map((part) => {
      try {
        return decodeURIComponent(part);
      } catch {
        return part;
      }
    });
  } catch {
    return { host: url, path: "" };
  }
  if (parts.length > 4) parts = [parts[0], "…", ...parts.slice(-2)];
  return { host: hostOf(url).replace(/^www\./, "") || url, path: parts.join(" › ") };
}

export interface Coverage {
  /** Where the text the model got starts and ends, as shares of the page (0–1); absent when
   *  the card does not say (one from before `read`), so no bar claims a share. */
  from?: number;
  to?: number;
  whole: boolean;
  text: string;
}

/** How much of the page this call read, for a fetch that read the page itself. */
export function coverage(card: WebFetchCard): Coverage | null {
  if (card.mode && card.mode !== "page") return null;
  const total = card.read?.page_chars;
  if (!total) {
    // A card from before `read` knew the length of its text and whether it was cut.
    if (card.chars === undefined) return null;
    const size = `${compactCount(card.chars)} characters`;
    return { whole: !card.truncated, text: card.truncated ? `${size} · part of the page` : size };
  }
  const start = Math.min(card.read?.start ?? 0, total);
  const end = Math.min(card.read?.end ?? total, total);
  const whole = start === 0 && end >= total;
  return {
    from: start / total,
    to: end / total,
    whole,
    text: whole
      ? `The whole page · ${compactCount(total)} characters`
      : `Characters ${compactCount(start)}–${compactCount(end)} of ${compactCount(total)}`,
  };
}

/** How the text was read, each worth a reader's note. `warn` is something they should weigh. */
export function aboutNotes(card: WebFetchCard): { text: string; tone: Tone }[] {
  const about = card.about ?? {};
  const notes: { text: string; tone: Tone }[] = [];
  if (about.unanswered) {
    notes.push({ text: `The question was not answered (${about.unanswered}); this is the page itself`, tone: "warn" });
  }
  if (about.js_required) {
    notes.push({ text: "Built by JavaScript: its HTML held almost no text", tone: "warn" });
  }
  if (about.plain_http) notes.push({ text: "Fetched over plain http: https would not connect", tone: "warn" });
  if (about.source_cut) notes.push({ text: "The source was cut at 5 MB", tone: "warn" });
  if (about.left_out_chars) {
    notes.push({ text: `Main content: ${compactCount(about.left_out_chars)} characters of menus left out`, tone: "neutral" });
  }
  if (about.cached_seconds !== undefined) {
    const s = about.cached_seconds;
    const ago = s < 60 ? "just now" : s < 3600 ? `${Math.floor(s / 60)} min ago` : `${Math.floor(s / 3600)} h ago`;
    notes.push({ text: `This session's copy, fetched ${ago}`, tone: "neutral" });
  }
  return notes;
}

/** Where a phrase occurs in a match's text, the way the page search finds it: any case, any
 *  run of whitespace between its words. */
export function markPhrase(text: string, phrase: string): { text: string; hit: boolean }[] {
  const words = phrase.trim().split(/\s+/).filter(Boolean);
  if (!words.length) return [{ text, hit: false }];
  const pattern = new RegExp(words.map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("\\s+"), "giu");
  const runs: { text: string; hit: boolean }[] = [];
  let last = 0;
  for (const match of text.matchAll(pattern)) {
    const at = match.index ?? 0;
    if (at > last) runs.push({ text: text.slice(last, at), hit: false });
    runs.push({ text: match[0], hit: true });
    last = at + match[0].length;
  }
  if (last < text.length) runs.push({ text: text.slice(last), hit: false });
  return runs.length ? runs : [{ text, hit: false }];
}

/** What a closed row says after the host: what came of the fetch, in a word or two. A refusal
 *  first, since a page that is not there makes everything else moot. */
export function fetchRowSuffix(card: WebFetchCard): string | undefined {
  if (card.status_code !== undefined && card.status_code >= 400) return `HTTP ${card.status_code}`;
  const mode = card.mode ?? "page";
  if (mode === "redirect") return "moved, not followed";
  if (mode === "image") return "image";
  if (mode === "pdf") return "PDF";
  if (mode === "find" && card.found) {
    return `${card.found.count} match${card.found.count === 1 ? "" : "es"}`;
  }
  if (mode === "answer" && card.answer) {
    const { found, total } = quoteTally(card.answer);
    return total ? `answered · ${found} of ${total} quotes found` : "answered";
  }
  const read = coverage(card);
  const total = card.read?.page_chars;
  if (read && !read.whole && total) {
    const start = card.read?.start ?? 0;
    return `${compactCount(Math.min(card.read?.end ?? total, total) - start)} of ${compactCount(total)}`;
  }
  return card.truncated ? "truncated" : undefined;
}

/** How many of an answer's quotes are in the page, over all of them: the card carries at most
 *  eight, so the counts come with it rather than from the ones shown. */
export function quoteTally(answer: NonNullable<WebFetchCard["answer"]>): { found: number; total: number } {
  return {
    found: answer.quotes_found ?? answer.quotes.filter((quote) => quote.found).length,
    total: answer.quotes_total ?? answer.quotes.length,
  };
}

/** The card as text a person would paste: the page, then what the call got from it. */
export function fetchCopyText(card: WebFetchCard): string {
  const lines = [card.title ? `${card.title} — ${card.url}` : card.url];
  if (card.answer) {
    if (card.answer.question) lines.push("", `Q: ${card.answer.question}`);
    lines.push("", card.answer.text);
    for (const quote of card.answer.quotes) {
      lines.push(`${quote.found ? "✓" : "✗"} “${quote.quote}”`);
    }
  } else if (card.found) {
    lines.push("", `${card.found.count} matches for “${card.found.phrase}”`);
    for (const match of card.found.matches) lines.push(`@${match.at}: ${match.text}`);
  } else if (card.excerpt) {
    lines.push("", card.excerpt);
  }
  return lines.join("\n");
}
