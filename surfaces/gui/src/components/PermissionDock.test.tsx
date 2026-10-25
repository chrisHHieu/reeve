// The permission dock (P1 / ledger 5.7): who asks, the tool hint — "Launch sub-agents" for
// a `task` ask — the matched value, and the three replies.
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PermissionDock, permissionDetail, permissionTitle } from "./PermissionDock";
import type { PermissionRequestInfo } from "../api";

const request = (
  over: Partial<PermissionRequestInfo> = {},
): PermissionRequestInfo => ({
  requestId: "per_1",
  sessionId: "child-1",
  parentSession: "parent-1",
  permission: "run_shell",
  patterns: ["rm -rf build"],
  metadata: {},
  always: ["rm -rf build"],
  tool: { call_id: "call_1" },
  ...over,
});

afterEach(cleanup);

describe("PermissionDock", () => {
  it("names the tool and the matched pattern and sends the three replies", () => {
    const onReply = vi.fn();
    render(<PermissionDock request={request()} responding={false} onReply={onReply} />);

    // The person is looking at the parent: the card says the ask comes from elsewhere.
    expect(screen.getByTestId("permission-dock").textContent).toContain("Asked by a subagent");
    expect(screen.getByTestId("permission-title").textContent).toBe("run_shell");
    expect(screen.getByTestId("permission-detail").textContent).toBe("rm -rf build");

    fireEvent.click(screen.getByTestId("permission-deny"));
    fireEvent.click(screen.getByTestId("permission-always"));
    fireEvent.click(screen.getByTestId("permission-once"));
    expect(onReply.mock.calls.map((c) => c[0])).toEqual(["reject", "always", "once"]);
  });

  it("shows the spawn hint and description for a task ask", () => {
    const req = request({
      permission: "task",
      patterns: ["general"],
      metadata: { description: "delegate the lookup", subagent_type: "general" },
      always: ["*"],
    });
    render(<PermissionDock request={req} responding={false} onReply={vi.fn()} />);
    expect(permissionTitle(req)).toBe("Launch sub-agents");
    expect(permissionDetail(req)).toBe("delegate the lookup");
    expect(screen.getByText("Launch sub-agents")).toBeTruthy();
    expect(screen.getByTestId("permission-detail").textContent).toBe(
      "delegate the lookup",
    );
  });

  it("names the subagent that asks, and 1 is the answer most people give", () => {
    const onReply = vi.fn();
    render(<PermissionDock request={request()} responding={false} onReply={onReply} from="Audit the build script" />);
    const dock = screen.getByTestId("permission-dock");
    expect(dock.textContent).toContain("Asked by Audit the build script");
    fireEvent.keyDown(dock, { key: "1" });
    expect(onReply).toHaveBeenLastCalledWith("once");
    fireEvent.keyDown(dock, { key: "Escape" });
    expect(onReply).toHaveBeenLastCalledWith("reject");
  });

  it("disables every button while a reply is in flight", () => {
    render(
      <PermissionDock request={request()} responding onReply={vi.fn()} />,
    );
    for (const id of ["permission-deny", "permission-always", "permission-once"]) {
      expect((screen.getByTestId(id) as HTMLButtonElement).disabled).toBe(true);
    }
  });
});
