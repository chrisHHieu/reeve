import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Elapsed, formatElapsed } from "./Elapsed";

afterEach(cleanup);

afterEach(() => {
  vi.useRealTimers();
});

describe("formatElapsed", () => {
  it("keeps a tenth of a second under ten seconds, so a live row looks alive", () => {
    expect(formatElapsed(0)).toBe("0.0s");
    expect(formatElapsed(1.24)).toBe("1.2s");
    expect(formatElapsed(9.96)).toBe("10.0s");
  });

  it("drops to whole seconds, then to minutes", () => {
    expect(formatElapsed(10)).toBe("10s");
    expect(formatElapsed(59.4)).toBe("59s");
    expect(formatElapsed(60)).toBe("1m 00s");
    expect(formatElapsed(123)).toBe("2m 03s");
  });

  it("says nothing rather than something wrong for a bad input", () => {
    expect(formatElapsed(Number.NaN)).toBe("");
    expect(formatElapsed(-5)).toBe("");
  });
});

describe("Elapsed", () => {
  it("ticks while running", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-01-01T00:00:00Z"));
    render(<Elapsed from={Date.now()} />);
    expect(screen.getByTestId("elapsed").textContent).toBe("0.0s");
    act(() => {
      vi.advanceTimersByTime(1200);
    });
    expect(screen.getByTestId("elapsed").textContent).toBe("1.2s");
  });

  it("stops ticking once the work settled", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-01-01T00:00:00Z"));
    const started = Date.now();
    render(<Elapsed from={started} running={false} />);
    act(() => {
      vi.advanceTimersByTime(5000);
    });
    // Frozen at the value it mounted with: the settled row uses the server's measured
    // duration instead, and a stale ticker would overwrite it with a wall clock.
    expect(screen.getByTestId("elapsed").textContent).toBe("0.0s");
    expect(screen.getByTestId("elapsed").getAttribute("data-running")).toBeNull();
  });
});
