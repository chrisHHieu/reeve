// The one line a tool call is, across its states (owner, 2026-10-13).

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { Transcript } from "./Transcript";
import { formatTook } from "./ToolRow";
import { plainLine } from "./ThinkRow";
import type { Item } from "../types";

afterEach(cleanup);

const user: Item = { kind: "user", text: "run the tests" };
const call = (extra: Record<string, unknown>): Item =>
  ({
    kind: "tool",
    id: "t1",
    callId: "c1",
    name: "run_shell",
    args: { command: "pytest -q" },
    presentation: { summary: "pytest -q" },
    callPresentation: { summary: "pytest -q" },
    ...extra,
  }) as Item;

const row = () => document.querySelector(".tool-row") as HTMLElement;

describe("a running row", () => {
  it("keeps saying what is running, with no clock in its first second", () => {
    render(<Transcript items={[user, call({ status: "…", ts: Date.now() / 1000 })]} running onApprove={vi.fn()} />);
    expect(row().getAttribute("data-state")).toBe("running");
    expect(row().querySelector(".tool-row-summary")!.textContent).toBe("pytest -q");
    expect(screen.queryByTestId("elapsed")).toBeNull();
  });

  it("shows its clock once it has run for a second", () => {
    render(<Transcript items={[user, call({ status: "…", ts: Date.now() / 1000 - 12 })]} running onApprove={vi.fn()} />);
    expect(row().querySelector(".tool-row-summary")!.textContent).toBe("pytest -q");
    expect(screen.getByTestId("elapsed").textContent).toMatch(/^1[23]s$/);
  });

  it("never wears the settle mark: finishing is a colour change, not a pop", () => {
    const { rerender } = render(<Transcript items={[user, call({ status: "…" })]} running onApprove={vi.fn()} />);
    rerender(<Transcript items={[user, call({ status: "ok" })]} onApprove={vi.fn()} />);
    expect(row().hasAttribute("data-settle")).toBe(false);
  });
});

describe("a call next in line", () => {
  it("rests and says it is waiting, with no shimmer and no clock", () => {
    render(
      <Transcript items={[user, call({ status: "…", queued: true, ts: Date.now() / 1000 - 12 })]} running onApprove={vi.fn()} />,
    );
    expect(row().getAttribute("data-state")).toBe("queued");
    expect(screen.getByTestId("tool-queued").textContent).toBe("waiting to run");
    expect(screen.queryByTestId("elapsed")).toBeNull();
    expect(row().querySelector(".shimmer-text")).toBeNull();
  });

  it("names its tool, not the work: 'Edit', until it runs and reads 'Editing'", () => {
    const edit = (extra: Record<string, unknown>) =>
      call({ name: "write_file", args: { path: "f1.txt", content: "x" }, presentation: undefined, callPresentation: undefined, ...extra });
    const { rerender } = render(
      <Transcript items={[user, edit({ status: "…", queued: true })]} running onApprove={vi.fn()} />,
    );
    expect(row().querySelector(".tool-row-title")!.textContent).toBe("Edit");
    rerender(<Transcript items={[user, edit({ status: "…" })]} running onApprove={vi.fn()} />);
    expect(row().querySelector(".tool-row-title")!.textContent).toBe("Editing");
  });

  it("starts running the moment it is dispatched", () => {
    const { rerender } = render(
      <Transcript items={[user, call({ status: "…", queued: true })]} running onApprove={vi.fn()} />,
    );
    rerender(<Transcript items={[user, call({ status: "…" })]} running onApprove={vi.fn()} />);
    expect(row().getAttribute("data-state")).toBe("running");
    expect(screen.queryByTestId("tool-queued")).toBeNull();
    expect(row().querySelector(".shimmer-text")).not.toBeNull();
  });
});

describe("a settled row", () => {
  it("says how long the call took once that is a second or more", () => {
    render(<Transcript items={[user, call({ status: "ok", durationMs: 4200 })]} onApprove={vi.fn()} />);
    expect(screen.getByTestId("tool-took").textContent).toBe("4.2s");
    expect(screen.getByTestId("tool-took").closest(".tool-row-meta")).not.toBeNull();
  });

  it("says no time for a call that returned at once", () => {
    render(<Transcript items={[user, call({ status: "ok", durationMs: 80 })]} onApprove={vi.fn()} />);
    expect(screen.queryByTestId("tool-took")).toBeNull();
  });

  it("formats the time the way a person reads it", () => {
    expect([formatTook(undefined), formatTook(999), formatTook(1000), formatTook(12_400), formatTook(72_000)]).toEqual([
      null,
      null,
      "1.0s",
      "12s",
      "1m 12s",
    ]);
  });
});

describe("a failed row", () => {
  it("keeps what was run and says why on a line of its own", () => {
    render(
      <Transcript
        items={[user, call({ status: "error", preview: '{"error": "1 failed, 3 passed"}' })]}
        onApprove={vi.fn()}
      />,
    );
    expect(row().querySelector(".tool-row-summary")!.textContent).toBe("pytest -q");
    expect(screen.getByTestId("tool-row-why").textContent).toBe("1 failed, 3 passed");
  });
});

describe("the Thought row's line", () => {
  it("is words, without the trace's markdown marks", () => {
    expect(plainLine("**Starting background task**")).toBe("Starting background task");
    expect(plainLine("## Plan: find `export` first")).toBe("Plan: find export first");
    expect(plainLine("- check the tests")).toBe("check the tests");
    // an identifier keeps its underscores
    expect(plainLine("rename fetch_record in user_store")).toBe("rename fetch_record in user_store");
  });
});
