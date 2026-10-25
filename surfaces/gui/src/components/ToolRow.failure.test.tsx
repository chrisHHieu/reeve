// A failure the reader watched happen, and a failure that was already history.
//
// The distinction is the whole behaviour: a live failure opens itself (being told something
// went wrong and having to click to find out what is the difference between an error that can
// be acted on and a red line), while a replayed one stays folded — otherwise opening an old
// session would unfold every error it ever had.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { Transcript } from "./Transcript";
import type { Item } from "../types";
import { setOpenFinishedTools } from "../cards/toolRowPrefs";

afterEach(cleanup);

function runningCall(): Item {
  return {
    kind: "tool",
    id: "t1",
    name: "run_shell",
    args: { command: "pytest -q" },
    callId: "c1",
    status: "\u2026",
    preview: '{"error": "1 failed, 3 passed"}',
    presentation: { summary: "Ran pytest -q" },
  } as Item;
}

function failingCall(status: string, withOutput = false): Item {
  return {
    kind: "tool",
    id: "t1",
    name: "run_shell",
    args: { command: "pytest -q" },
    callId: "c1",
    status,
    ...(withOutput ? { preview: '{"error": "1 failed, 3 passed"}' } : {}),
    presentation: { summary: "Ran pytest -q" },
  } as Item;
}

const items = (tool: Item): Item[] => [{ kind: "user", text: "run the tests" }, tool];

function row(): HTMLElement {
  return document.querySelector("[data-disclosure-row]") as HTMLElement;
}

describe("a failing tool row", () => {
  it("opens itself when it fails while the turn is running", () => {
    const { rerender } = render(
      <Transcript items={items(failingCall("…"))} running onApprove={vi.fn()} />,
    );
    expect(row().getAttribute("aria-expanded")).toBe("false");

    rerender(<Transcript items={items(failingCall("error"))} running onApprove={vi.fn()} />);
    expect(row().getAttribute("aria-expanded")).toBe("true");
  });

  it("leaves a failure that was already history folded", () => {
    render(<Transcript items={items(failingCall("error"))} running={false} onApprove={vi.fn()} />);
    expect(row().getAttribute("aria-expanded")).toBe("false");
  });

  it("does not claim the space for an interruption — that is not a failure", () => {
    const { rerender } = render(
      <Transcript items={items(failingCall("…"))} running onApprove={vi.fn()} />,
    );
    rerender(<Transcript items={items(failingCall("interrupted"))} running onApprove={vi.fn()} />);
    expect(row().getAttribute("aria-expanded")).toBe("false");
  });

  it("offers the failure's own text for copying", async () => {
    const written: string[] = [];
    Object.assign(navigator, {
      clipboard: { writeText: (text: string) => (written.push(text), Promise.resolve()) },
    });
    render(
      <Transcript
        items={items(failingCall("error", true))}
        running={false}
        onApprove={vi.fn()}
      />,
    );
    fireEvent.click(row()); // open the failed row
    fireEvent.click(screen.getByTestId("copy-button"));
    await Promise.resolve();
    expect(written).toEqual(["1 failed, 3 passed"]);
  });
});

describe("a call that keeps running", () => {
  it("stays one line while it runs and once it succeeds: nothing opens to fold back", () => {
    vi.useFakeTimers();
    const { rerender } = render(<Transcript items={items(runningCall())} running onApprove={vi.fn()} />);
    act(() => {
      vi.advanceTimersByTime(2000);
    });
    expect(row().getAttribute("aria-expanded")).toBe("false");
    rerender(<Transcript items={items(failingCall("ok", true))} running onApprove={vi.fn()} />);
    expect(row().getAttribute("aria-expanded")).toBe("false");
    vi.useRealTimers();
  });

  it("stays open when the person opened it themselves", () => {
    const { rerender } = render(<Transcript items={items(runningCall())} running onApprove={vi.fn()} />);
    fireEvent.click(row());
    rerender(<Transcript items={items(failingCall("ok", true))} running onApprove={vi.fn()} />);
    expect(row().getAttribute("aria-expanded")).toBe("true");
  });

  it("leaves a command that already finished to itself", () => {
    // A `git status` that returns in 80ms must not unfurl the transcript, so the timer is
    // cancelled when the call settles first.
    vi.useFakeTimers();
    const { rerender } = render(
      <Transcript items={items(runningCall())} running onApprove={vi.fn()} />,
    );
    rerender(<Transcript items={items(failingCall("ok"))} running onApprove={vi.fn()} />);
    act(() => {
      vi.advanceTimersByTime(2000);
    });
    expect(row().getAttribute("aria-expanded")).toBe("false");
    vi.useRealTimers();
  });
});

describe("with Open tools when they finish on", () => {
  beforeEach(() => setOpenFinishedTools(true));
  afterEach(() => setOpenFinishedTools(false));

  it("a running call opens after half a second, and not before, to stream its output", () => {
    vi.useFakeTimers();
    render(<Transcript items={items(runningCall())} running onApprove={vi.fn()} />);
    expect(row().getAttribute("aria-expanded")).toBe("false");
    act(() => {
      vi.advanceTimersByTime(600);
    });
    expect(row().getAttribute("aria-expanded")).toBe("true");
    vi.useRealTimers();
  });

  it("a call that finishes opens to show what it did", () => {
    const { rerender } = render(<Transcript items={items(runningCall())} running onApprove={vi.fn()} />);
    expect(row().getAttribute("aria-expanded")).toBe("false");
    rerender(<Transcript items={items(failingCall("ok", true))} running onApprove={vi.fn()} />);
    expect(row().getAttribute("aria-expanded")).toBe("true");
  });

  it("a row the person closed while it ran stays closed", () => {
    vi.useFakeTimers();
    const { rerender } = render(<Transcript items={items(runningCall())} running onApprove={vi.fn()} />);
    act(() => {
      vi.advanceTimersByTime(600);
    });
    fireEvent.click(row());
    expect(row().getAttribute("aria-expanded")).toBe("false");
    rerender(<Transcript items={items(failingCall("ok", true))} running onApprove={vi.fn()} />);
    expect(row().getAttribute("aria-expanded")).toBe("false");
    vi.useRealTimers();
  });

  it("history stays folded, and so does an interruption", () => {
    render(<Transcript items={items(failingCall("ok", true))} running={false} onApprove={vi.fn()} />);
    expect(row().getAttribute("aria-expanded")).toBe("false");
    cleanup();
    const { rerender } = render(<Transcript items={items(runningCall())} running onApprove={vi.fn()} />);
    rerender(<Transcript items={items(failingCall("interrupted", true))} running onApprove={vi.fn()} />);
    expect(row().getAttribute("aria-expanded")).toBe("false");
  });
});
