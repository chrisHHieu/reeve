import { describe, expect, it } from "vitest";
import { clockTime, sessionAge } from "./sessionAge";

// A Sunday afternoon, local time: every case below is counted back from it.
const now = new Date(2026, 9, 11, 15, 30, 0);
const ago = (ms: number) => new Date(now.getTime() - ms).toISOString();
const MIN = 60_000;
const HOUR = 60 * MIN;
const at = (y: number, m: number, d: number, h = 12) => new Date(y, m, d, h, 0, 0).toISOString();

describe("sessionAge", () => {
  it("says the last minutes as they are, and earlier today as the time", () => {
    expect(sessionAge(ago(20_000), now, "en-GB")).toBe("now");
    expect(sessionAge(ago(12 * MIN), now, "en-GB")).toBe("12m");
    expect(sessionAge(ago(59 * MIN), now, "en-GB")).toBe("59m");
    expect(sessionAge(ago(6 * HOUR + 16 * MIN), now, "en-GB")).toBe("9:14");
  });

  it("says yesterday, then the weekday, then the date, then the month and year", () => {
    expect(sessionAge(at(2026, 9, 10, 23), now, "en-GB")).toBe("Yesterday");
    expect(sessionAge(at(2026, 9, 5), now, "en-GB")).toBe("Mon");
    expect(sessionAge(at(2026, 9, 3), now, "en-GB")).toBe("3 Oct");
    expect(sessionAge(at(2025, 11, 24), now, "en-GB")).toBe("Dec 2025");
  });

  it("yesterday is the day before, however few hours ago that was", () => {
    const earlyMorning = new Date(2026, 9, 11, 0, 20, 0);
    expect(sessionAge(new Date(2026, 9, 10, 22, 0, 0).toISOString(), earlyMorning, "en-GB")).toBe("Yesterday");
  });

  it("says nothing for a time it cannot read, and never a time in the future", () => {
    expect(sessionAge(null, now)).toBe("");
    expect(sessionAge("not a date", now)).toBe("");
    expect(sessionAge(ago(-5 * MIN), now, "en-GB")).toBe("now");
    expect(clockTime("nope")).toBe("");
  });
});
