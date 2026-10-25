// What the fetch card says, as words and numbers (webFetchView.ts).

import { describe, expect, it } from "vitest";
import {
  addressTrail,
  compactCount,
  coverage,
  fetchCopyText,
  fetchRowSuffix,
  markPhrase,
  quoteTally,
  statusTone,
} from "./webFetchView";
import type { WebFetchCard } from "./types";

const base: WebFetchCard = { card: "web", kind: "fetch", url: "https://x.example/a" };

describe("fetch card words", () => {
  it("reads a status as an answer, a move or a refusal", () => {
    expect([200, 204, 299, 301, 304, 399, 400, 404, 500].map(statusTone)).toEqual([
      "ok", "ok", "ok", "neutral", "neutral", "neutral", "danger", "danger", "danger",
    ]);
    expect(statusTone(undefined)).toBeNull();
  });

  it("counts characters the way a reader takes them in", () => {
    expect([0, 687, 999, 1000, 1850, 9999, 10_000, 53_986, 999_499, 1_200_000].map(compactCount)).toEqual([
      "0", "687", "999", "1k", "1.9k", "10k", "10k", "54k", "999k", "1.2M",
    ]);
  });

  it("writes an address as its host and path, keeping a deep path's two ends", () => {
    expect(addressTrail("https://www.example.com/")).toEqual({ host: "example.com", path: "" });
    expect(addressTrail("https://a.example/wiki/Caf%C3%A9")).toEqual({ host: "a.example", path: "wiki › Café" });
    expect(addressTrail("https://a.example/a/b/c/d/e/f.html").path).toBe("a › … › e › f.html");
    expect(addressTrail("not a url")).toEqual({ host: "not a url", path: "" });
  });

  it("places what was read inside the page", () => {
    const read = coverage({ ...base, read: { start: 20_000, end: 40_000, page_chars: 54_000 } })!;
    expect(read.whole).toBe(false);
    expect([read.from, read.to]).toEqual([20_000 / 54_000, 40_000 / 54_000]);
    expect(read.text).toBe("Characters 20k–40k of 54k");
    expect(coverage({ ...base, read: { start: 0, end: 6947, page_chars: 6947 } })?.text).toBe(
      "The whole page · 6.9k characters",
    );
    expect(coverage({ ...base, mode: "find", read: { page_chars: 6947 } })).toBeNull();
    expect(coverage(base)).toBeNull();
  });

  it("marks a phrase in any case, across any run of whitespace, taking its symbols literally", () => {
    expect(markPhrase("A Task\n Group, a task group.", "task group")).toEqual([
      { text: "A ", hit: false },
      { text: "Task\n Group", hit: true },
      { text: ", a ", hit: false },
      { text: "task group", hit: true },
      { text: ".", hit: false },
    ]);
    expect(markPhrase("calls f(x) and fx", "f(x)").filter((run) => run.hit).map((run) => run.text)).toEqual(["f(x)"]);
    expect(markPhrase("nothing", "  ")).toEqual([{ text: "nothing", hit: false }]);
  });

  it("says in a word what came of a fetch, a refusal first", () => {
    const suffix = (extra: Partial<WebFetchCard>) => fetchRowSuffix({ ...base, ...extra });
    expect(suffix({ status_code: 404, mode: "find", found: { phrase: "x", count: 3, matches: [] } })).toBe("HTTP 404");
    expect(suffix({ mode: "find", found: { phrase: "x", count: 1, matches: [] } })).toBe("1 match");
    expect(suffix({ mode: "find", found: { phrase: "x", count: 18, matches: [] } })).toBe("18 matches");
    expect(suffix({ mode: "redirect" })).toBe("moved, not followed");
    expect(suffix({ mode: "pdf" })).toBe("PDF");
    expect(suffix({ mode: "answer", answer: { text: "y", quotes: [] } })).toBe("answered");
    expect(
      suffix({ mode: "answer", answer: { text: "y", quotes: [{ quote: "q", found: true }], quotes_total: 3, quotes_found: 2 } }),
    ).toBe("answered · 2 of 3 quotes found");
    expect(suffix({ mode: "page", read: { start: 20_000, end: 40_000, page_chars: 54_000 } })).toBe("20k of 54k");
    expect(suffix({ mode: "page", read: { start: 0, end: 900, page_chars: 900 } })).toBeUndefined();
    expect(suffix({ truncated: true, chars: 4000 })).toBe("truncated");
  });

  it("counts an older answer's quotes from the ones it carries", () => {
    expect(quoteTally({ text: "", quotes: [{ quote: "a", found: true }, { quote: "b", found: false }] })).toEqual({
      found: 1,
      total: 2,
    });
  });

  it("copies the page and what the call got", () => {
    const text = fetchCopyText({
      ...base,
      title: "Widgets",
      answer: { question: "Why?", text: "Because.", quotes: [{ quote: "q1", found: true }, { quote: "q2", found: false }] },
    });
    expect(text).toBe("Widgets — https://x.example/a\n\nQ: Why?\n\nBecause.\n✓ “q1”\n✗ “q2”");
    expect(fetchCopyText({ ...base, found: { phrase: "x", count: 2, matches: [{ at: 5, text: "a x" }] } })).toBe(
      "https://x.example/a\n\n2 matches for “x”\n@5: a x",
    );
  });
});
