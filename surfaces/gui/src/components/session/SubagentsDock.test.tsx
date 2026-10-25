// The subagents roster (`SubagentsDock.tsx`): what the closed header says, what a row says
// while a child runs versus once it settled, and that opening a child goes through the
// caller's navigation. jsdom, so the CSS is not observable — the classes are the contract.

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { ActivityRow } from "./ActivityRow";
import { delegationNodes, type DelegationNode } from "../../delegations";

// The subagents are a segment of the activity row and the roster under it; alone in the row,
// they are what these tests read.
const SubagentsDock = ({
  nodes,
  onOpenChild,
}: {
  nodes: readonly DelegationNode[];
  onOpenChild?: (child: { runId: string; title: string }) => void;
}) => <ActivityRow subagents={nodes} onOpenChild={onOpenChild} />;
import type { ToolItem } from "../../cards/cardModels";

afterEach(cleanup);

const COLORS = {};

function taskCall(over: Partial<ToolItem> = {}): ToolItem {
  return {
    kind: "tool",
    id: "t1",
    name: "task",
    args: { description: "Inventory host-core A2A", prompt: "inventory the A2A surface", subagent_type: "explore" },
    callId: "c1",
    status: "…",
    ...over,
  } as ToolItem;
}

function nodes(...tools: ToolItem[]) {
  return delegationNodes(tools, COLORS);
}

describe("a background workflow in the roster (owner, 2026-10-13)", () => {
  // A reopened session: no live frames, only the call and its stored presentation's run id.
  const replayedRun = (): ToolItem =>
    ({
      kind: "tool",
      id: "w1",
      name: "workflow",
      args: { meta: { name: "file-review", description: "Review each file" }, script: "…" },
      callId: "w1",
      status: "ok",
      runId: "runner-1",
    }) as ToolItem;

  afterEach(() => vi.unstubAllGlobals());

  it("is listed from a reload, and opened it names its agents, each a door into its own session", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => ({
        ok: true,
        json: async () =>
          String(url).includes("/sessions/runner-1/children")
            ? {
                children: [
                  { session_id: "a1", title: "shop/cart.py (@general subagent)", agent: "general", status: "idle" },
                  { session_id: "a2", title: "shop/cache.py (@general subagent)", agent: "general", status: "busy" },
                ],
              }
            : {},
      })),
    );
    const onOpenChild = vi.fn();
    render(<SubagentsDock nodes={delegationNodes([replayedRun()], COLORS)} onOpenChild={onOpenChild} />);
    fireEvent.click(screen.getByTestId("subagents-dock-toggle"));
    const row = screen.getByTestId("subagent-row");
    expect(row.textContent).toContain("Review each file");
    fireEvent.click(screen.getByTestId("subagent-row-toggle"));
    const agents = await screen.findAllByTestId("workflow-agent");
    expect(agents.map((a) => [a.textContent, a.getAttribute("data-state")])).toEqual([
      ["shop/cart.pygeneral", "ended"],
      ["shop/cache.pygeneral", "running"],
    ]);
    fireEvent.click(agents[0]);
    // The run the agent belongs to rides along: the panel says it above the agent's name.
    expect(onOpenChild).toHaveBeenCalledWith({
      runId: "a1",
      title: "shop/cart.py",
      group: expect.stringContaining("Review each file"),
    });
  });
});

describe("SubagentsDock", () => {
  it("renders nothing without children", () => {
    render(<SubagentsDock nodes={[]} />);
    expect(screen.queryByTestId("subagents-dock")).toBeNull();
  });

  it("names the working child in the closed header, with its live count", () => {
    render(
      <SubagentsDock
        nodes={nodes(
          taskCall({
            id: "t1",
            callId: "c1",
            subagent: { sessionId: "child-1", startedAt: 100, tool: "read_file", title: "src/a.ts" },
          }),
          taskCall({ id: "t2", callId: "c2", args: { description: "Second look", prompt: "p", subagent_type: "explore" } }),
        )}
      />,
    );
    const dock = screen.getByTestId("subagents-dock");
    expect(dock.getAttribute("data-state")).toBe("live");
    // Both spawn calls are still in flight (`status: "…"`), so both children count.
    expect(screen.getByTestId("subagents-dock-progress").textContent).toContain("2 running");
    expect(screen.getByTestId("subagents-dock-toggle").textContent).toContain("Inventory host-core A2A");
    // Closed: the rows are mounted but inert, so nothing inside is reachable yet.
    expect(screen.getByTestId("subagents-dock-body").getAttribute("data-open")).toBe("false");
  });

  it("lists the working child, with the finished one behind its fold, and a token figure only once it settled", () => {
    const running = taskCall({
      id: "t1",
      callId: "c1",
      stepCount: 7,
      subagent: { sessionId: "child-1", startedAt: 100, tool: "grep", title: "host-core" },
    });
    const done = taskCall({
      id: "t2",
      callId: "c2",
      args: { description: "Second look", prompt: "p", subagent_type: "explore" },
      status: "ok",
      preview: '{"task_id": "child-2", "status": "completed", "tokens": 41200, "steps": 14, "report": "…"}',
      subagent: { sessionId: "child-2", startedAt: 100, endedAt: 240, durationMs: 140_000 },
    });
    render(<SubagentsDock nodes={nodes(running, done)} />);
    fireEvent.click(screen.getByTestId("subagents-dock-toggle"));

    // Thirty settled children are one line, "Finished 30", until someone asks for them.
    expect(screen.getAllByTestId("subagent-row")).toHaveLength(1);
    const fold = screen.getByTestId("subagents-finished-toggle");
    expect(fold.textContent).toBe("Finished 1");
    fireEvent.click(fold);
    const rows = screen.getAllByTestId("subagent-row");
    expect(rows).toHaveLength(2);
    // The running child reports steps and its live line — never a token figure it lacks.
    expect(rows[0].getAttribute("data-outcome")).toBe("running");
    expect(rows[0].textContent).toContain("7 steps");
    expect(screen.getByTestId("subagent-live").textContent).toBe("↳ grep host-core");
    expect(rows[1].getAttribute("data-outcome")).toBe("completed");
    expect(rows[1].textContent).toContain("41.2k tok");
    expect(rows[1].textContent).toContain("2m 20s");
  });

  it("a call in line for a slot holds still and says when it starts, until its child exists", () => {
    const working = taskCall({ id: "t1", callId: "c1", subagent: { sessionId: "child-1", startedAt: 100, phase: "thinking" } });
    const queued = (subagent: ToolItem["subagent"]) =>
      taskCall({ id: "t2", callId: "c2", args: { description: "Rewrite docs/API.md", prompt: "p", subagent_type: "general" }, subagent });
    const { rerender } = render(<SubagentsDock nodes={nodes(working, queued({ waiting: { running: 4, limit: 4 } }))} />);
    fireEvent.click(screen.getByTestId("subagents-dock-toggle"));
    const inLine = screen.getAllByTestId("subagent-row")[1];
    expect(inLine.querySelector('[data-testid="subagent-waiting-mark"]')).toBeTruthy();
    expect(inLine.querySelector('[data-testid="subagent-running-mark"]')).toBeNull();
    expect(inLine.querySelector(".shimmer-text")).toBeNull();
    expect(inLine.textContent).toContain("↳ Starts when one of the 4 running finishes");

    rerender(<SubagentsDock nodes={nodes(working, queued({ waiting: { running: 4, limit: 4 }, sessionId: "child-2" }))} />);
    const started = screen.getAllByTestId("subagent-row")[1];
    expect(started.querySelector('[data-testid="subagent-running-mark"]')).toBeTruthy();
    expect(started.textContent).not.toContain("Starts when");
  });

  it("opens a child's transcript from its row, and shows a failure's own text on expand", () => {
    const onOpenChild = vi.fn();
    render(
      <SubagentsDock
        onOpenChild={onOpenChild}
        nodes={nodes(
          taskCall({
            status: "error",
            preview: '{"error": "the child ran out of steps"}',
            subagent: { sessionId: "child-9", startedAt: 100, endedAt: 130, durationMs: 30_000 },
          }),
        )}
      />,
    );
    fireEvent.click(screen.getByTestId("subagents-dock-toggle"));
    const row = screen.getByTestId("subagent-row");
    expect(row.getAttribute("data-outcome")).toBe("failed");
    // The row body is the door; the disclosure is what reveals the task text.
    fireEvent.click(screen.getByTestId("subagent-open-main"));
    expect(onOpenChild).toHaveBeenCalledWith({ runId: "child-9", title: "Inventory host-core A2A" });
    fireEvent.click(screen.getByTestId("subagent-row-toggle"));
    expect(screen.getByTestId("subagent-body").textContent).toContain("the child ran out of steps");
    expect(screen.getByTestId("subagent-body").textContent).toContain("inventory the A2A surface");
  });

  it("stays as a quiet count once every child settled, and opens on the finished ones", () => {
    const done = taskCall({ status: "ok", preview: '{"task_id": "c", "status": "completed", "tokens": 10}' });
    render(<SubagentsDock nodes={nodes(done)} />);
    // The roster was the header chip's. Its home is this segment now, in the quiet ink.
    expect(screen.getByTestId("subagents-dock").getAttribute("data-state")).toBe("done");
    expect(screen.getByTestId("subagents-dock-progress").textContent).toBe("1 finished");
    fireEvent.click(screen.getByTestId("subagents-dock-toggle"));
    // Nothing to show above the fold, so it starts open.
    expect(screen.getAllByTestId("subagent-row")).toHaveLength(1);
  });
});
