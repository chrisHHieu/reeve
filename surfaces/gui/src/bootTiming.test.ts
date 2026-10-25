// How long the engine takes to answer on this computer, and when a start is called slow
// (the boot screen redesign, 2026-10-23).
import { afterEach, describe, expect, it } from "vitest";
import { KEPT, SLOW_FLOOR_MS, isSlow, readBootTimes, rememberBootTime, seconds, usualBootMs } from "./bootTiming";

afterEach(() => localStorage.clear());

describe("a start's usual", () => {
  it("is the median of the starts remembered, and only once there are three", () => {
    expect(usualBootMs([])).toBeNull();
    expect(usualBootMs([2000, 3000])).toBeNull();
    expect(usualBootMs([2000, 9000, 3000])).toBe(3000);
    expect(usualBootMs([2000, 3000, 4000, 5000])).toBe(3500);
  });

  it("keeps the last few starts, and nothing a start could not take", () => {
    for (let i = 1; i <= KEPT + 2; i++) rememberBootTime(i * 1000);
    rememberBootTime(0);
    rememberBootTime(Number.NaN);
    expect(readBootTimes()).toEqual([3000, 4000, 5000, 6000, 7000]);
  });

  it("reads nothing out of a value it did not write", () => {
    localStorage.setItem("reeve.boot.engine-ms", "not json");
    expect(readBootTimes()).toEqual([]);
    localStorage.setItem("reeve.boot.engine-ms", JSON.stringify(["3", -1, 2500]));
    expect(readBootTimes()).toEqual([2500]);
  });
});

describe("a slow start", () => {
  it("is never called slow before the floor, however quick the usual", () => {
    expect(isSlow(SLOW_FLOOR_MS - 1, [500, 500, 500])).toBe(false);
    expect(isSlow(SLOW_FLOOR_MS + 1, [500, 500, 500])).toBe(true);
  });

  it("is past twice the usual where the usual is long", () => {
    const usual = [5000, 5000, 5000];
    expect(isSlow(9_000, usual)).toBe(false);
    expect(isSlow(10_001, usual)).toBe(true);
  });

  it("with nothing remembered, is past the floor", () => {
    expect(isSlow(SLOW_FLOOR_MS + 1, [])).toBe(true);
    expect(isSlow(SLOW_FLOOR_MS - 1, [])).toBe(false);
  });
});

it("says seconds with one decimal under ten, whole above", () => {
  expect(seconds(2400)).toBe("2.4 s");
  expect(seconds(9949)).toBe("9.9 s");
  expect(seconds(12_400)).toBe("12 s");
  expect(seconds(-5)).toBe("0.0 s");
});
