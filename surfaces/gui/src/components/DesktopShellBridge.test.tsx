// Leaving the desktop app: what the tray is told, the question the first close asks,
// and the question Quit asks when it would stop work. The shell is faked as `window.reeveDesktop`.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { DesktopShellBridge, runningWork } from "./DesktopShellBridge";
import type { SessionInfo } from "../types";

type Listener = (event: { payload: unknown }) => void;

let calls: Array<[string, Record<string, unknown> | undefined]>;
let listeners: Record<string, Listener>;

beforeEach(() => {
  calls = [];
  listeners = {};
  (globalThis as any).reeveDesktop = {
    invoke: vi.fn(async (cmd: string, args?: Record<string, unknown>) => {
      calls.push([cmd, args]);
      return cmd === "set_close_behavior" ? (args?.onClose as string) : null;
    }),
    on: vi.fn((name: string, cb: (payload: unknown) => void) => {
      listeners[name] = (event) => cb(event.payload);
      return () => delete listeners[name];
    }),
  };
});

afterEach(() => {
  cleanup();
  delete (globalThis as any).reeveDesktop;
});

const session = (id: string, extra: Partial<SessionInfo> = {}): SessionInfo =>
  ({ session_id: id, title: id, workspace: "/w", ...extra }) as SessionInfo;

const flush = () => act(async () => {});
const named = (cmd: string) => calls.filter(([c]) => c === cmd);

describe("the tray is told what runs", () => {
  it("counts the conversations with a turn in flight and the ones that wait on the person", () => {
    const work = runningWork([
      session("Fix the CSV export", { liveness: "working" }),
      session("Weekly report", { liveness: "working", attention: 1 }),
      session("Idle one"),
    ]);
    expect(work.running.map((s) => s.title)).toEqual(["Fix the CSV export", "Weekly report"]);
    expect(work.needsYou).toBe(1);
    expect(work.titles).toBe("Fix the CSV export · Weekly report");
  });

  it("reports to the shell when the count changes, not on every render", async () => {
    const { rerender } = render(<DesktopShellBridge sessions={[session("a", { liveness: "working" })]} />);
    await flush();
    rerender(<DesktopShellBridge sessions={[session("a", { liveness: "working" })]} />);
    await flush();
    expect(named("set_tray_status")).toEqual([["set_tray_status", { running: 1, needsYou: 0, titles: "a" }]]);
    rerender(<DesktopShellBridge sessions={[session("a")]} />);
    await flush();
    const calls = named("set_tray_status");
    expect(calls[calls.length - 1]).toEqual(["set_tray_status", { running: 0, needsYou: 0, titles: "" }]);
  });
});

describe("closing the window the first time", () => {
  it("asks once, remembers the tray, and hides the window", async () => {
    render(<DesktopShellBridge sessions={[]} />);
    await flush();
    await act(async () => listeners["reeve:close-requested"]({ payload: { behavior: "ask" } }));
    expect(named("close_ack")).toHaveLength(1);
    expect(screen.getByTestId("first-close-dialog")).toBeTruthy();
    // The answer most people give holds focus.
    expect(document.activeElement).toBe(screen.getByTestId("first-close-tray"));
    await act(async () => fireEvent.click(screen.getByTestId("first-close-tray")));
    expect(named("set_close_behavior")).toEqual([["set_close_behavior", { onClose: "tray" }]]);
    expect(named("close_to_tray")).toHaveLength(1);
    expect(screen.queryByTestId("first-close-dialog")).toBeNull();
  });

  it("choosing to quit with work running asks what to do with the work", async () => {
    render(<DesktopShellBridge sessions={[session("Fix it", { liveness: "working" })]} />);
    await flush();
    await act(async () => listeners["reeve:close-requested"]({ payload: { behavior: "ask" } }));
    await act(async () => fireEvent.click(screen.getByTestId("first-close-quit")));
    expect(named("set_close_behavior")).toEqual([["set_close_behavior", { onClose: "quit" }]]);
    expect(named("quit_app")).toHaveLength(0);
    expect(screen.getByTestId("quit-dialog")).toBeTruthy();
  });
});

describe("quitting", () => {
  it("with nothing running quits at once", async () => {
    render(<DesktopShellBridge sessions={[]} />);
    await flush();
    await act(async () => listeners["reeve:close-requested"]({ payload: { behavior: "quit" } }));
    expect(named("quit_app")).toHaveLength(1);
    expect(screen.queryByTestId("quit-dialog")).toBeNull();
  });

  it("with work running names it, keeps it on Cancel, and offers to quit when it finishes", async () => {
    render(
      <DesktopShellBridge
        sessions={[session("Fix the CSV export", { liveness: "working" }), session("Weekly report", { liveness: "working" })]}
      />,
    );
    await flush();
    await act(async () => listeners["reeve:quit-requested"]({ payload: null }));
    expect(screen.getByText("Quit Reeve while 2 tasks are running?")).toBeTruthy();
    expect(screen.getByTestId("quit-dialog-list").textContent).toContain("Weekly report");
    expect(document.activeElement?.textContent).toBe("Cancel");
    await act(async () => fireEvent.click(screen.getByText("Cancel")));
    expect(named("quit_app")).toHaveLength(0);

    await act(async () => listeners["reeve:quit-requested"]({ payload: null }));
    await act(async () => fireEvent.click(screen.getByTestId("quit-when-done")));
    expect(named("set_quit_when_done")).toEqual([["set_quit_when_done", { on: true }]]);
    expect(named("close_to_tray")).toHaveLength(1);
    expect(named("quit_app")).toHaveLength(0);

    await act(async () => listeners["reeve:quit-requested"]({ payload: null }));
    await act(async () => fireEvent.click(screen.getByTestId("quit-now")));
    expect(named("quit_app")).toHaveLength(1);
  });
});
