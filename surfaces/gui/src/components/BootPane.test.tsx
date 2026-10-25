// What the window says while Reeve starts (the boot screen redesign, 2026-10-23): only what it
// knows, and a start that fails says why and what can be done.
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { BootPane } from "./BootPane";

afterEach(() => {
  cleanup();
  localStorage.clear();
  vi.useRealTimers();
});

const base = {
  opening: false,
  title: null,
  startedAt: Date.now(),
  engineMs: null,
  sidecar: null,
  failure: null,
  onRetry: () => {},
};

describe("while the engine starts", () => {
  it("says it is starting the engine, and what comes after", () => {
    render(<BootPane {...base} />);
    expect(screen.getByText("Starting Reeve")).toBeTruthy();
    expect(screen.getByTestId("boot-step-now").textContent).toBe("Starting the engine");
    expect(screen.getByText("Opening your last conversation")).toBeTruthy();
    expect(screen.queryByTestId("boot-slow")).toBeNull();
  });

  it("counts the seconds once the start is no longer quick", () => {
    render(<BootPane {...base} startedAt={Date.now() - 4_200} />);
    expect(screen.getByTestId("boot-step-now").textContent).toBe("Starting the engine4.2 s");
  });

  it("says the supervisor's restart in its own words", () => {
    const sidecar = { state: "restarting", attempt: 2, max_attempts: 3, reason: null };
    const { rerender } = render(<BootPane {...base} sidecar={sidecar} />);
    expect(screen.getByTestId("boot-step-now").textContent).toBe("The engine stopped. Starting it again, try 2 of 3");
    rerender(<BootPane {...base} sidecar={{ state: "crashed", attempt: 0, max_attempts: 3, reason: "exit code 1" }} />);
    expect(screen.getByTestId("boot-step-now").textContent).toBe("The engine stopped (exit code 1). Starting it again");
  });

  it("calls a start slow against this computer's usual, and only then", () => {
    localStorage.setItem("reeve.boot.engine-ms", JSON.stringify([2000, 3000, 3000]));
    render(<BootPane {...base} startedAt={Date.now() - 8_000} />);
    expect(screen.getByTestId("boot-slow").textContent).toBe(
      "Slower than usual. It took 3.0 s the last few times on this computer. It keeps trying for 20 s, then says what went wrong.",
    );
  });

  it("with nothing to compare, says only that it is still starting", () => {
    render(<BootPane {...base} startedAt={Date.now() - 8_000} />);
    expect(screen.getByTestId("boot-slow").textContent).toMatch(/^Still starting\. It keeps trying for 20 s/);
  });

  it("is not slow at a usual pace", () => {
    localStorage.setItem("reeve.boot.engine-ms", JSON.stringify([5000, 5000, 5000]));
    render(<BootPane {...base} startedAt={Date.now() - 8_000} />);
    expect(screen.queryByTestId("boot-slow")).toBeNull();
  });

  it("moves its clock while it waits", () => {
    vi.useFakeTimers();
    const started = Date.now();
    render(<BootPane {...base} startedAt={started} />);
    expect(screen.getByTestId("boot-step-now").textContent).toBe("Starting the engine");
    act(() => {
      vi.advanceTimersByTime(3_500);
    });
    expect(screen.getByTestId("boot-step-now").textContent).toBe("Starting the engine3.5 s");
  });
});

describe("while the conversation opens", () => {
  it("says the engine started, how long it took, and which conversation is opening", () => {
    render(<BootPane {...base} opening engineMs={2_400} title="Tính năng thiết kế" />);
    expect(screen.getByText("Engine started")).toBeTruthy();
    expect(screen.getByText("2.4 s")).toBeTruthy();
    expect(screen.getByTestId("boot-step-now").textContent).toBe("Opening “Tính năng thiết kế”");
    expect(screen.queryByText("Starting Reeve")).toBeNull();
  });
});

describe("a start that failed", () => {
  const failure = { reason: "crashed 3 times in a row", waitedMs: 7_000 };

  it("says what happened, that the work is safe, and what can be done", () => {
    const onRetry = vi.fn();
    render(<BootPane {...base} failure={failure} sidecar={{ state: "stopped", attempt: 3, max_attempts: 3, reason: "crashed 3 times in a row" }} onRetry={onRetry} />);
    expect(screen.getByRole("alert").textContent).toContain("Reeve’s engine didn’t start");
    expect(screen.getByRole("alert").textContent).toContain("It stopped while starting, so Reeve stopped trying.");
    expect(screen.getByRole("alert").textContent).toContain("Your conversations and files are safe on this computer.");
    expect(screen.getByTestId("boot-reason").textContent).toBe("crashed 3 times in a row");
    fireEvent.click(screen.getByTestId("boot-retry"));
    expect(onRetry).toHaveBeenCalledOnce();
    // The log folder is the desktop app's: a browser has none to open.
    expect(screen.queryByTestId("boot-logs")).toBeNull();
  });

  it("names the wait when the engine simply never answered", () => {
    render(<BootPane {...base} failure={{ reason: "The engine did not answer.", waitedMs: 20_400 }} />);
    expect(screen.getByRole("alert").textContent).toContain("It didn’t answer within 20 s.");
  });

  it("copies what happened for someone who can look into it", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    render(<BootPane {...base} failure={failure} />);
    await act(async () => {
      fireEvent.click(screen.getByTestId("boot-copy"));
    });
    const copied = String(writeText.mock.calls[0][0]);
    expect(copied).toContain("What happened: crashed 3 times in a row");
    expect(copied).toContain("Waited: 7.0 s");
    expect(screen.getByTestId("boot-copy").textContent).toBe("Copied");
  });
});
