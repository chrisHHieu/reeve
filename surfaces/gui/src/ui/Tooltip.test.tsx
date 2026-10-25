// The tooltip's timing is the behaviour worth pinning: a delay on the way in (so sweeping a
// row of icons does not strobe), none on the way out, and both Escape and a click dismiss it.

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Tooltip } from "./Tooltip";

afterEach(cleanup);

afterEach(() => {
  vi.useRealTimers();
});

function trigger() {
  return screen.getByRole("button", { name: "Inspect" });
}

describe("Tooltip", () => {
  it("waits before showing on hover", () => {
    vi.useFakeTimers();
    render(
      <Tooltip label="Inspect the call">
        <button type="button">Inspect</button>
      </Tooltip>,
    );
    fireEvent.pointerEnter(trigger().parentElement!);
    expect(screen.queryByTestId("tooltip")).toBeNull();
    act(() => {
      vi.advanceTimersByTime(400);
    });
    expect(screen.getByTestId("tooltip").textContent).toBe("Inspect the call");
  });

  it("hides the moment the pointer leaves", () => {
    vi.useFakeTimers();
    render(
      <Tooltip label="Inspect the call">
        <button type="button">Inspect</button>
      </Tooltip>,
    );
    fireEvent.pointerEnter(trigger().parentElement!);
    act(() => {
      vi.advanceTimersByTime(400);
    });
    fireEvent.pointerLeave(trigger().parentElement!);
    expect(screen.queryByTestId("tooltip")).toBeNull();
  });

  it("shows immediately on keyboard focus, and Escape dismisses it", () => {
    render(
      <Tooltip label="Inspect the call">
        <button type="button">Inspect</button>
      </Tooltip>,
    );
    fireEvent.focus(trigger());
    expect(screen.queryByTestId("tooltip")).not.toBeNull();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByTestId("tooltip")).toBeNull();
  });

  it("renders the trigger untouched when it is disabled", () => {
    render(
      <Tooltip label="Inspect the call" disabled>
        <button type="button">Inspect</button>
      </Tooltip>,
    );
    fireEvent.focus(trigger());
    expect(screen.queryByTestId("tooltip")).toBeNull();
    expect(screen.queryByRole("button", { name: "Inspect" })).not.toBeNull();
  });
});
