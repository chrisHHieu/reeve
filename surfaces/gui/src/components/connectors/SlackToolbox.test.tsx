// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Connector } from "../../api";
import { SlackUserToken } from "./SlackUserToken";
import { ToolsDisclosure } from "./ToolsDisclosure";

const updateConnectorTools = vi.fn(async (_n: string, _e: Record<string, boolean>) => ({ ok: true }));
const setSlackUserToken = vi.fn(async (_t: string) => ({ ok: true }) as { ok: boolean; error?: string });
// A minimal mock, as in the other connector tests: the real api module hangs the worker.
vi.mock("../../api", () => ({
  updateConnectorTools: (n: string, e: Record<string, boolean>) => updateConnectorTools(n, e),
  setSlackUserToken: (t: string) => setSlackUserToken(t),
}));

const tool = (name: string, group: string, enabled: boolean, kind = "read") => ({
  name, label: name, kind, description: name, enabled, requires_approval: true, group,
});

function connector(over: Partial<Connector> = {}): Connector {
  return {
    name: "slack", title: "Slack", connected: true, account: "Acme", enabled: true, fields: [], instructions: [],
    tools: [
      tool("slack_read_thread", "read", true),
      tool("slack_search_messages", "read", true),
      tool("slack_post_message", "write", true, "write"),
      tool("slack_create_channel", "admin", false, "write"),
      tool("slack_kick_from_channel", "admin", false, "write"),
    ],
    ...over,
  } as unknown as Connector;
}

beforeEach(() => {
  updateConnectorTools.mockClear();
  setSlackUserToken.mockClear();
});
afterEach(cleanup);

describe("a grouped tool catalog", () => {
  it("draws one heading per group with how many are on, and says admin starts off", () => {
    render(<ToolsDisclosure c={connector()} onChanged={vi.fn()} />);
    // The tools sit behind a fold that says how many are on.
    expect(screen.getByTestId("tools-fold").textContent).toContain("3 of 5 on");
    expect(screen.queryByTestId("tools-group-read")).toBeNull();
    fireEvent.click(screen.getByTestId("tools-fold"));
    expect(screen.getByTestId("tools-group-read").textContent).toContain("2 of 2");
    expect(screen.getByTestId("tools-group-write").textContent).toContain("asks first");
    const admin = screen.getByTestId("tools-group-admin").textContent ?? "";
    expect(admin).toContain("0 of 2");
    expect(admin).toContain("off until you turn them on");
    expect(screen.getAllByRole("switch")).toHaveLength(5);
  });

  it("turns a whole group on in one call, and off again once it is all on", async () => {
    const onChanged = vi.fn();
    const { rerender } = render(<ToolsDisclosure c={connector()} onChanged={onChanged} />);
    fireEvent.click(screen.getByTestId("tools-fold"));
    fireEvent.click(screen.getByTestId("tools-all-admin"));
    await waitFor(() => expect(updateConnectorTools).toHaveBeenCalledTimes(1));
    expect(updateConnectorTools).toHaveBeenCalledWith("slack", { slack_create_channel: true, slack_kick_from_channel: true });

    const allOn = connector();
    allOn.tools = allOn.tools!.map((t) => ({ ...t, enabled: true }));
    rerender(<ToolsDisclosure c={allOn} onChanged={onChanged} />);
    expect(screen.getByTestId("tools-all-admin").textContent).toBe("Turn all off");
  });

  it("keeps a flat list for a connector whose tools declare no group", () => {
    const flat = connector();
    flat.tools = flat.tools!.map(({ group: _g, ...rest }) => rest as never);
    render(<ToolsDisclosure c={flat} onChanged={vi.fn()} />);
    fireEvent.click(screen.getByTestId("tools-fold"));
    expect(screen.getAllByRole("switch")).toHaveLength(5);
    expect(screen.queryByTestId("tools-group-read")).toBeNull();
  });
});

describe("the Slack user token row", () => {
  it("saves a token and never shows one that is set", async () => {
    const onChanged = vi.fn();
    render(<SlackUserToken c={connector({ has_user_token: false })} onChanged={onChanged} />);
    expect(screen.getByTestId("slack-user-token").textContent).toContain("Add a user token");
    fireEvent.change(screen.getByTestId("slack-user-token-input"), { target: { value: "xoxp-abc" } });
    fireEvent.click(screen.getByTestId("slack-user-token-save"));
    await waitFor(() => expect(setSlackUserToken).toHaveBeenCalledWith("xoxp-abc"));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect((screen.getByTestId("slack-user-token-input") as HTMLInputElement).value).toBe("");
  });

  it("says a token is set without showing it", () => {
    render(<SlackUserToken c={connector({ has_user_token: true })} onChanged={vi.fn()} />);
    expect(screen.getByTestId("slack-user-token").textContent).toContain("A user token is set");
    expect((screen.getByTestId("slack-user-token-input") as HTMLInputElement).value).toBe("");
  });

  it("removes with an empty token, and says why a save failed", async () => {
    const c = connector({ has_user_token: true });
    render(<SlackUserToken c={c} onChanged={vi.fn()} />);
    fireEvent.click(screen.getByTestId("slack-user-token-remove"));
    await waitFor(() => expect(setSlackUserToken).toHaveBeenCalledWith(""));

    setSlackUserToken.mockResolvedValueOnce({ ok: false, error: "that token belongs to a different workspace" });
    fireEvent.change(screen.getByTestId("slack-user-token-input"), { target: { value: "xoxp-x" } });
    fireEvent.click(screen.getByTestId("slack-user-token-save"));
    expect((await screen.findByRole("alert")).textContent).toContain("different workspace");
  });

  it("draws nothing until Slack is connected", () => {
    const { container } = render(<SlackUserToken c={connector({ connected: false })} onChanged={vi.fn()} />);
    expect(container.textContent).toBe("");
  });
});
