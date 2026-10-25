import { describe, expect, it } from "vitest";
import { change, compact, ms, pct, share, readableReason, rangeDays } from "./format";

describe("readableReason", () => {
  it("unwraps a JSON error with escaped quotes and dashes", () => {
    const raw =
      '{"error": "`mcp__x` is not on this session\'s tool list \u2014 find it with \\"tool_search\\"", "error_type": "ValueError"}';
    expect(readableReason(raw)).toBe(
      '`mcp__x` is not on this session\'s tool list — find it with "tool_search"',
    );
  });

  it("strips an envelope the log clipped mid-string", () => {
    expect(readableReason('{"error": "path escapes \\"a\\" and more')).toBe('path escapes "a" and more');
  });

  it("drops the Error: prefix", () => {
    expect(readableReason("Error: not a file: x")).toBe("not a file: x");
  });
});

describe("rangeDays", () => {
  it("pads a range to one slot per day, quiet days included", () => {
    expect(rangeDays("7d", [])).toHaveLength(7);
    expect(rangeDays("today", [])).toHaveLength(1);
  });
});

describe("a missing number never reaches the screen as NaN", () => {
  it("compact, ms and pct say a dash", () => {
    expect(compact(NaN)).toBe("—");
    expect(ms(NaN)).toBe("—");
    expect(pct(NaN)).toBe("—");
    expect(share(NaN, 10)).toBe(0);
  });
});

describe("change", () => {
  it("says a count's move in percent and a ratio's in points", () => {
    expect(change(125, 100)).toEqual({ text: "+25%", up: true });
    expect(change(94, 100)).toEqual({ text: "−6%", up: false });
    expect(change(0.81, 0.78, "ratio")).toEqual({ text: "+3 pt", up: true });
    expect(change(100, 100)).toEqual({ text: "same", up: true });
  });

  it("says nothing without a span before, or with nothing in it", () => {
    expect(change(100, null)).toBeNull();
    expect(change(100, 0)).toBeNull();
    expect(change(null, 0.5, "ratio")).toBeNull();
  });
});
