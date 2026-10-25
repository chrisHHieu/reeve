// The words and marks a web search row shows (`webSearchView.ts`), without a DOM.

import { describe, expect, it } from "vitest";
import { markTerms, providerLabel, publishedLabel, queryTerms } from "./webSearchView";

describe("the query's terms", () => {
  it("are the words worth marking, each once", () => {
    expect(queryTerms("context compaction in coding agents")).toEqual(["context", "compaction", "coding", "agents"]);
    expect(queryTerms("What is the Context of it? context")).toEqual(["context"]);
    expect(queryTerms("")).toEqual([]);
  });

  it("are marked as whole words, in any script", () => {
    const hits = (text: string, terms: string[]) =>
      markTerms(text, terms).filter((run) => run.hit).map((run) => run.text);
    expect(hits("Context fills the window", ["context", "in"])).toEqual(["Context"]);
    expect(hits("Không gian ngữ cảnh, ngữ cảnh dài", ["ngữ"])).toEqual(["ngữ", "ngữ"]);
    expect(hits("C++ and c", ["c++"])).toEqual(["C++"]);
    // The text is unchanged by the marking.
    expect(markTerms("a Context b", ["context"]).map((run) => run.text).join("")).toBe("a Context b");
  });
});

describe("when a page was published", () => {
  const now = new Date(2026, 9, 5, 15, 0);
  it("counts calendar days, then names the date", () => {
    expect(publishedLabel("2026-10-05T00:00:00.000Z", now).text).toBe("today");
    expect(publishedLabel("2026-10-04T10:00:00.000Z", now).text).toBe("yesterday");
    expect(publishedLabel("2026-10-01T10:00:00.000Z", now).text).toBe("4 days ago");
    expect(publishedLabel("2026-07-29T16:00:00.000Z", now).text).toBe("29 Jul");
    expect(publishedLabel("2025-03-02", now).text).toBe("2 Mar 2025");
  });

  it("is a calendar day, not a count of hours", () => {
    // Two hours apart, on two different days.
    const justAfterMidnight = new Date(2026, 9, 5, 1, 0);
    expect(publishedLabel(new Date(2026, 9, 4, 23, 0).toISOString(), justAfterMidnight).text).toBe("yesterday");
    // Twenty hours apart, on the same day.
    const lateEvening = new Date(2026, 9, 5, 23, 0);
    expect(publishedLabel(new Date(2026, 9, 5, 3, 0).toISOString(), lateEvening).text).toBe("today");
  });

  it("says nothing it does not know", () => {
    expect(publishedLabel(undefined, now)).toEqual({ text: "", title: "" });
    expect(publishedLabel("N/A", now)).toEqual({ text: "", title: "" });
  });
});

describe("a provider's name", () => {
  it("is written the way the service writes it", () => {
    expect(providerLabel("exa")).toBe("Exa");
    expect(providerLabel("duckduckgo")).toBe("DuckDuckGo");
    expect(providerLabel("tavily")).toBe("Tavily");
    expect(providerLabel("")).toBe("");
  });
});
