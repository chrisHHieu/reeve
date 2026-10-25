// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Connector } from "../../api";
import { ToolsetsGroup } from "./ToolsetsGroup";

const setConnectorToolsets = vi.fn(async (_name: string, _enabled: string[]) => ({ ok: true }));
// A minimal mock, as in DisconnectButton.test: the real api module's import chain touches
// Tauri globals and hangs the worker, and this component needs one export from it.
vi.mock("../../api", () => ({
  setConnectorToolsets: (n: string, e: string[]) => setConnectorToolsets(n, e),
}));

const SETS = [
  { id: "repos", label: "Repositories", description: "Files.", enabled: true, locked: true, needs_scopes: [] },
  { id: "actions", label: "Actions", description: "Runs.", enabled: true, locked: false, needs_scopes: [] },
  { id: "gists", label: "Gists", description: "Gists.", enabled: false, locked: false, needs_scopes: ["gist"] },
];

function connector(sets = SETS): Connector {
  return { name: "github", title: "GitHub", toolsets: sets } as unknown as Connector;
}

beforeEach(() => setConnectorToolsets.mockClear());
afterEach(cleanup);

describe("GitHub tool groups", () => {
  it("draws one row per group, and a locked group cannot be switched off", () => {
    render(<ToolsetsGroup c={connector()} onChanged={vi.fn()} onGrant={vi.fn()} />);
    expect(screen.getAllByTestId(/^toolset-[a-z_]+$/)).toHaveLength(3);
    expect((screen.getByTestId("toolset-repos").querySelector("input") as HTMLInputElement).disabled).toBe(true);
    expect((screen.getByTestId("toolset-actions").querySelector("input") as HTMLInputElement).disabled).toBe(false);
  });

  it("sends the whole chosen set when one group is switched on", async () => {
    const onChanged = vi.fn();
    render(<ToolsetsGroup c={connector()} onChanged={onChanged} onGrant={vi.fn()} />);
    fireEvent.click(screen.getByTestId("toolset-gists").querySelector("input")!);
    await waitFor(() => expect(setConnectorToolsets).toHaveBeenCalledTimes(1));
    expect(setConnectorToolsets).toHaveBeenCalledWith("github", ["repos", "actions", "gists"]);
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it("offers Grant access only once a group that needs a missing permission is on", () => {
    const onGrant = vi.fn();
    const { rerender } = render(<ToolsetsGroup c={connector()} onChanged={vi.fn()} onGrant={onGrant} />);
    // Gists needs `gist`, but it is off: nothing to grant, and no warning on its row.
    expect(screen.queryByTestId("toolsets-grant")).toBeNull();
    expect(screen.queryByTestId("toolset-needs-gists")).toBeNull();

    rerender(
      <ToolsetsGroup
        c={connector(SETS.map((s) => (s.id === "gists" ? { ...s, enabled: true } : s)))}
        onChanged={vi.fn()}
        onGrant={onGrant}
      />,
    );
    expect(screen.getByTestId("toolset-needs-gists")).toBeTruthy();
    fireEvent.click(screen.getByTestId("toolsets-grant"));
    expect(onGrant).toHaveBeenCalledTimes(1);
    // Choosing a group never starts a sign-in by itself.
    expect(setConnectorToolsets).not.toHaveBeenCalled();
  });

  it("shows nothing for a connection that has no groups", () => {
    const { container } = render(<ToolsetsGroup c={connector([])} onChanged={vi.fn()} onGrant={vi.fn()} />);
    expect(container.textContent).toBe("");
  });

  it("says why when saving fails", async () => {
    setConnectorToolsets.mockResolvedValueOnce({ ok: false, error: "unknown toolset: x" } as never);
    render(<ToolsetsGroup c={connector()} onChanged={vi.fn()} onGrant={vi.fn()} />);
    fireEvent.click(screen.getByTestId("toolset-gists").querySelector("input")!);
    expect((await screen.findByRole("alert")).textContent).toContain("unknown toolset");
  });
});
