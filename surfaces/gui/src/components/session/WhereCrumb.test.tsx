// The crumb says a folder is a worktree of its own (layout step 2; the fact comes from the session
// list), and says nothing of it for a plain folder or a remote machine.
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { WhereCrumb, type WhereFacts } from "./WhereCrumb";

afterEach(cleanup);

const where = (over: Partial<WhereFacts>): WhereFacts => ({
  folderName: "shop",
  folderPath: "/code/shop",
  branch: "fix/retry",
  environment: null,
  remoteWorkspace: null,
  connected: true,
  problem: "",
  ...over,
});

describe("the crumb's worktree tag", () => {
  it("is there for a linked worktree, after the branch", () => {
    render(<WhereCrumb where={where({ worktree: true })} sessionKey="s" started />);
    expect(screen.getByTestId("where-crumb-branch").textContent).toBe("fix/retry");
    expect(screen.getByTestId("where-crumb-worktree").textContent).toBe("worktree");
  });

  it("is not there for a plain folder or a remote machine", () => {
    render(<WhereCrumb where={where({ worktree: false })} sessionKey="s" started />);
    expect(screen.queryByTestId("where-crumb-worktree")).toBeNull();
    cleanup();
    render(
      <WhereCrumb
        where={where({ worktree: true, environment: { id: "h", name: "box", user: "u", host: "h", port: 22 } as never, remoteWorkspace: "/srv" })}
        sessionKey="s"
        started
      />,
    );
    expect(screen.queryByTestId("where-crumb-worktree")).toBeNull();
  });
});
