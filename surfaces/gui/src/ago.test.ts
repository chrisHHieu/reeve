import { describe, expect, it } from "vitest";
import { sinceLabel } from "./ago";

const NOW = Date.UTC(2026, 9, 24, 12, 0, 0);
const at = (secondsAgo: number) => NOW / 1000 - secondsAgo;

describe("sinceLabel", () => {
  it("says nothing for a moment that is not there", () => {
    expect(sinceLabel(undefined, { now: NOW })).toBeNull();
    expect(sinceLabel(0, { now: NOW })).toBeNull();
    expect(sinceLabel(Number.NaN, { now: NOW })).toBeNull();
  });

  it("counts minutes, then hours, then days", () => {
    expect(sinceLabel(at(30), { now: NOW })).toBe("just now");
    expect(sinceLabel(at(5 * 60), { now: NOW })).toBe("5m ago");
    expect(sinceLabel(at(3 * 3600), { now: NOW })).toBe("3h ago");
    expect(sinceLabel(at(47 * 3600), { now: NOW })).toBe("47h ago");
    expect(sinceLabel(at(3 * 86400), { now: NOW })).toBe("3d ago");
  });

  it("a moment in the future is just now, not a negative time", () => {
    expect(sinceLabel(at(-600), { now: NOW })).toBe("just now");
  });

  it("follows the thresholds it is given", () => {
    const message = { justNow: 45, hoursUntilDays: 24, dateFromDays: 7, now: NOW };
    expect(sinceLabel(at(60), message)).toBe("1m ago");
    expect(sinceLabel(at(30 * 3600), message)).toBe("1d ago");
    expect(sinceLabel(at(9 * 86400), message)).toBe(new Date(at(9 * 86400) * 1000).toLocaleDateString());
  });
});
