import { describe, expect, it } from "vitest";
import { compactingWords } from "./compactingWords";

describe("compactingWords", () => {
  it("says why a compaction runs, beside the context size", () => {
    expect(compactingWords("stage", 161_000)).toBe(
      "Compacting the conversation · a stage of the work just finished · 161k tokens",
    );
    expect(compactingWords("full", 480_000)).toContain("the context is near its limit");
    expect(compactingWords("overflow", 0)).toBe("Compacting the conversation · the last request did not fit");
  });

  it("says no reason it was not given, or does not know", () => {
    expect(compactingWords(null, 0)).toBe("Compacting the conversation");
    expect(compactingWords("something-new", 12_000)).toBe("Compacting the conversation · 12k tokens");
  });
});
