// The disclosure's contract is about MOUNTING, not about CSS: the body must exist while it
// is opening or closing, and must be gone once it is closed — that is what keeps a folded
// transcript from holding every diff it ever rendered.
//
// jsdom has no matchMedia, which `usePrefersReducedMotion` reads as "unknown → still". The
// animated path is therefore tested by stubbing the query, and the still path by leaving it
// absent, so both behaviours are pinned rather than assumed.

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Disclosure } from "./Disclosure";
import { DURATION } from "./motion";

function stubMotion(matches: boolean) {
  vi.stubGlobal(
    "matchMedia",
    vi.fn(() => ({
      matches,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  );
}

afterEach(cleanup);

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("Disclosure", () => {
  it("renders nothing at all while closed when motion is unavailable", () => {
    render(
      <Disclosure open={false}>
        <span data-testid="body">body</span>
      </Disclosure>,
    );
    expect(screen.queryByTestId("body")).toBeNull();
  });

  it("mounts the body and marks itself open", () => {
    render(
      <Disclosure open testId="wrap">
        <span data-testid="body">body</span>
      </Disclosure>,
    );
    expect(screen.queryByTestId("body")).not.toBeNull();
    expect(screen.getByTestId("wrap").getAttribute("data-open")).toBe("true");
  });

  it("keeps the body mounted for the closing frames, then unmounts it", () => {
    vi.useFakeTimers();
    stubMotion(false);
    const { rerender } = render(
      <Disclosure open testId="wrap">
        <span data-testid="body">body</span>
      </Disclosure>,
    );
    act(() => {
      vi.advanceTimersByTime(20); // let the open frame land
    });
    expect(screen.getByTestId("wrap").getAttribute("data-open")).toBe("true");

    rerender(
      <Disclosure open={false} testId="wrap">
        <span data-testid="body">body</span>
      </Disclosure>,
    );
    // The closing transition is playing: no `data-open`, but still mounted.
    expect(screen.getByTestId("wrap").getAttribute("data-open")).toBeNull();
    expect(screen.queryByTestId("body")).not.toBeNull();

    act(() => {
      vi.advanceTimersByTime(DURATION.base + 20);
    });
    expect(screen.queryByTestId("body")).toBeNull();
    expect(screen.queryByTestId("wrap")).toBeNull();
  });

  it("closes immediately when the reader asked for no motion", () => {
    stubMotion(true);
    const { rerender } = render(
      <Disclosure open>
        <span data-testid="body">body</span>
      </Disclosure>,
    );
    rerender(
      <Disclosure open={false}>
        <span data-testid="body">body</span>
      </Disclosure>,
    );
    expect(screen.queryByTestId("body")).toBeNull();
  });

  it("reopens after a close without waiting for the old timer", () => {
    vi.useFakeTimers();
    stubMotion(false);
    const { rerender } = render(
      <Disclosure open testId="wrap">
        <span data-testid="body">body</span>
      </Disclosure>,
    );
    rerender(
      <Disclosure open={false} testId="wrap">
        <span data-testid="body">body</span>
      </Disclosure>,
    );
    rerender(
      <Disclosure open testId="wrap">
        <span data-testid="body">body</span>
      </Disclosure>,
    );
    act(() => {
      vi.advanceTimersByTime(DURATION.base + 20);
    });
    expect(screen.queryByTestId("body")).not.toBeNull();
  });

  it("is not a focus trap or a button: the wrapper carries no role of its own", () => {
    render(
      <Disclosure open testId="wrap">
        <button type="button">inner</button>
      </Disclosure>,
    );
    const wrap = screen.getByTestId("wrap");
    expect(wrap.getAttribute("role")).toBeNull();
    expect(wrap.getAttribute("tabindex")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "inner" }));
  });
});
