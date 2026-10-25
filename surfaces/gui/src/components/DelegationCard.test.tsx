// The delegation card's own behaviour: the auto-open/pin rule, the promote affordance
// (P6 — a port of the old TaskRow promote tests), and the aggregate header's vocabulary.

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { DelegationCard } from "./DelegationCard";
import type { ToolItem } from "../cards/cardModels";
import { setOpenFinishedTools } from "../cards/toolRowPrefs";
import { updateToolMetadata } from "../toolRows";

afterEach(cleanup);

function taskCall(over: Partial<ToolItem> = {}): ToolItem {
  return {
    kind: "tool",
    id: "t1",
    name: "task",
    args: {
      description: "Investigate the cache",
      prompt: "find where the cache lives",
      subagent_type: "general",
    },
    callId: "c1",
    status: "…",
    ...over,
  } as ToolItem;
}

describe("the delegation card", () => {
  it("starts open while a child runs and folds once the group settles", () => {
    const { container, rerender } = render(
      <DelegationCard items={[{ tool: taskCall() }]} />,
    );
    expect(screen.getByTestId("task-row")).toBeTruthy();
    expect(container.querySelector(".delegation-card.active")).toBeTruthy();

    rerender(<DelegationCard items={[{ tool: taskCall({ status: "ok" }) }]} />);
    // Settled with no user toggle: the card folds back to its header line.
    expect(screen.queryByTestId("task-row")).toBeNull();
    fireEvent.click(screen.getByTestId("delegation-card-head"));
    expect(screen.getByTestId("task-row")).toBeTruthy();

    // The user's own toggle pins — collapse it again, and a new running child does NOT
    // force it back open (a mid-run collapse is a request to stop watching).
    fireEvent.click(screen.getByTestId("delegation-card-head"));
    expect(screen.queryByTestId("task-row")).toBeNull();
    rerender(<DelegationCard items={[{ tool: taskCall() }]} />);
    expect(screen.queryByTestId("task-row")).toBeNull();
  });

  it("stays open once its children finish when finished work is set to stay open", () => {
    setOpenFinishedTools(true);
    try {
      const { rerender } = render(<DelegationCard items={[{ tool: taskCall() }]} />);
      rerender(<DelegationCard items={[{ tool: taskCall({ status: "ok" }) }]} />);
      expect(screen.getByTestId("task-row")).toBeTruthy();
      cleanup();
      // A card that was already history when it mounted starts folded either way.
      render(<DelegationCard items={[{ tool: taskCall({ status: "ok" }) }]} />);
      expect(screen.queryByTestId("task-row")).toBeNull();
    } finally {
      setOpenFinishedTools(false);
    }
  });

  it("a call in line for a slot says Waiting until its child reports in", () => {
    // Two of six in one fan-out read "Running" while they waited for one of the four running to
    // finish, with no child behind them (owner, 2026-10-21). The frames come off the wire here.
    const queued = updateToolMetadata([taskCall()], "c1", { waiting: { running: 4, limit: 4 } });
    const card = (items: ReturnType<typeof updateToolMetadata>) => (
      <DelegationCard items={[{ tool: items[0] as ToolItem }]} onOpenChild={() => {}} onPromoteTask={() => {}} />
    );
    const { rerender } = render(card(queued));
    expect(screen.getByTestId("task-status").textContent).toBe("Waiting");
    expect(screen.getByTestId("task-live").textContent).toBe("↳ Starts when one of the 4 running finishes");
    expect(screen.getByTestId("task-row").getAttribute("data-waiting")).toBe("true");
    // Nothing is working yet: no moving mark, nothing to open, nothing to send to the background.
    expect(screen.queryByTestId("task-spinner")).toBeNull();
    expect(screen.queryByTestId("task-promote")).toBeNull();
    expect(screen.queryByTestId("task-open")).toBeNull();
    // It is still unfinished work: the header does not count it as done.
    expect(screen.getByTestId("delegation-metrics").textContent).toContain("0 of 1 finished");

    // The child's first frame, with its session id, ends the wait.
    rerender(card(updateToolMetadata(queued, "c1", { session_id: "child-5", toolcalls: 0, model: "m" })));
    expect(screen.getByTestId("task-status").textContent).toBe("Running");
    expect(screen.getByTestId("task-row").getAttribute("data-waiting")).toBeNull();
    expect(screen.getByTestId("task-spinner")).toBeTruthy();
    expect(screen.getByTestId("task-promote")).toBeTruthy();
  });

  it("a queued call has returned, and its row still says Waiting until its child starts", () => {
    // A call past the cap returns at once as queued; its row is a background
    // row from the first frame, so it keeps receiving its child's frames after it settled.
    let items = updateToolMetadata([taskCall()], "c1", { waiting: { running: 4, limit: 4 }, background: true });
    items = [{ ...(items[0] as ToolItem), status: "ok", preview: '{"task_id": "", "status": "queued"}' }];
    const { rerender } = render(<DelegationCard items={[{ tool: items[0] as ToolItem }]} />);
    expect(screen.getByTestId("task-status").textContent).toBe("Waiting");
    expect(screen.getByTestId("delegation-metrics").textContent).toContain("0 of 1 finished");

    items = updateToolMetadata(items, "c1", { session_id: "child-7", toolcalls: 0, background: true });
    rerender(<DelegationCard items={[{ tool: items[0] as ToolItem }]} />);
    expect(screen.getByTestId("task-status").textContent).toBe("Running");
  });

  it("a child whose turn ended mid-batch says Finished, and stops shimmering", () => {
    // The faster half of a two-spawn batch: its settle frame has arrived, its result has not —
    // no result of a parallel chunk is recorded until every call in it finished, so
    // the row used to read "Running" until the slower child ended (owner-hit 2026-10-18).
    render(
      <DelegationCard
        items={[
          {
            tool: taskCall({
              subagent: { sessionId: "child-1", endedAt: 1_700_000_100, durationMs: 12_000 },
            }),
          },
          { tool: taskCall({ id: "t2", callId: "c2", subagent: { sessionId: "child-2" } }) },
        ]}
      />,
    );
    const rows = screen.getAllByTestId("task-row");
    expect(rows[0]?.getAttribute("data-outcome")).toBe("settled");
    expect(screen.getAllByTestId("task-status")[0]?.textContent).toBe("Finished · 12.0s");
    expect(screen.getAllByTestId("task-live")[0]?.textContent).toBe(
      "↳ Finished its turn — waiting for the result",
    );
    // No moving glyph and no shimmer on a child that stopped: the still mark is the bot.
    expect(rows[0]?.querySelector("[data-testid='task-leading']")).toBeTruthy();
    expect(rows[0]?.querySelector("[data-testid='task-spinner']")).toBeNull();
    expect(rows[0]?.querySelector(".delegation-node-desc")?.className).not.toContain("shimmer-text");
    // Its sibling is still working: the group stays live and counts 1 of 2 finished.
    expect(rows[1]?.getAttribute("data-outcome")).toBe("running");
    expect(screen.getByTestId("delegation-metrics").textContent).toContain("1 of 2 finished");
  });

  it("offers Run in background while a foreground child runs, and never double-fires the node", () => {
    const onPromote = vi.fn();
    const onOpen = vi.fn();
    render(
      <DelegationCard
        items={[{ tool: taskCall() }]}
        onOpenChild={onOpen}
        onPromoteTask={onPromote}
      />,
    );

    fireEvent.click(screen.getByTestId("task-promote"));
    expect(onPromote).toHaveBeenCalledTimes(1);
    expect(onOpen).not.toHaveBeenCalled();
    expect(screen.queryByTestId("task-background")).toBeNull();
  });

  it("hides the button and marks (background) once the promotion frame arrives", () => {
    render(
      <DelegationCard
        items={[{ tool: taskCall({ subagent: { sessionId: "child-1", background: true } }) }]}
        onPromoteTask={vi.fn()}
      />,
    );

    expect(screen.queryByTestId("task-promote")).toBeNull();
    expect(screen.getByTestId("task-background").textContent).toBe("(background)");
  });

  it("renders no promote affordance on a settled child or without the route", () => {
    render(<DelegationCard items={[{ tool: taskCall({ status: "ok" }) }]} />);
    expect(screen.queryByTestId("task-promote")).toBeNull();

    render(<DelegationCard items={[{ tool: taskCall() }]} />);
    expect(screen.queryByTestId("task-promote")).toBeNull();
  });

  it("shows the model the child runs on, live and after a route override", () => {
    // A RUNNING child keeps the card open; the chip is there from the first frame.
    const { unmount } = render(
      <DelegationCard
        items={[{ tool: taskCall({ subagent: { model: "deepseek:deepseek-flash" } }) }]}
      />,
    );
    expect(screen.getByTestId("task-model").textContent).toBe("deepseek:deepseek-flash");
    unmount();

    // A call whose live frames are gone still shows the REQUESTED override.
    render(
      <DelegationCard
        items={[
          {
            tool: taskCall({
              args: {
                description: "Verify provider override on explore",
                prompt: "run",
                subagent_type: "explore",
                provider: "deepseek",
                model: "deepseek-flash",
              },
            }),
          },
        ]}
      />,
    );
    expect(screen.getByTestId("task-model").textContent).toBe("deepseek:deepseek-flash");
  });

  it("omits the model chip when nothing names one", () => {
    render(<DelegationCard items={[{ tool: taskCall() }]} />);
    expect(screen.queryByTestId("task-model")).toBeNull();
  });
});
