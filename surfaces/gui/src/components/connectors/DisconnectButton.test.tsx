// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi, beforeEach } from "vitest";

import type { Connector } from "../../api";
import { DisconnectConnector } from "./DisconnectButton";

const disconnectConnector = vi.fn(async (_name: string) => ({ ok: true }));

// A minimal mock — pulling the real api module through importOriginal hangs the worker
// (the module's import chain touches Tauri globals), and this component needs exactly
// one export from it.
vi.mock("../../api", () => ({ disconnectConnector: (n: string) => disconnectConnector(n) }));

function connector(over: Partial<Connector> = {}): Connector {
  return {
    name: "notion",
    title: "Notion",
    icon: "N",
    blurb: "",
    auth: "oauth",
    two_way: false,
    channels: false,
    available: true,
    connected: true,
    enabled: true,
    fields: [],
    instructions: [],
    tools: [],
    ...over,
  } as unknown as Connector;
}

beforeEach(() => disconnectConnector.mockClear());
afterEach(cleanup);

describe("connector-level disconnect", () => {
  it("is offered for a connector signed in through MCP, which has no accounts", async () => {
    // The bug: GitHub's page offered only "disconnect installation" and Notion's only
    // "disconnect account". An MCP sign-in creates neither, so both pages showed a
    // connected card with no way back out — while the backend could disconnect fine.
    render(
      <DisconnectConnector
        c={connector({ mode: "mcp" } as Partial<Connector>)}
        onChanged={() => {}}
      />,
    );
    expect(screen.getByTestId("disconnect-connector-notion")).toBeTruthy();
  });

  it("calls the connector-level endpoint, not an account or installation one", async () => {
    const onChanged = vi.fn();
    const onGone = vi.fn();
    render(
      <DisconnectConnector c={connector()} onChanged={onChanged} onGone={onGone} />,
    );
    fireEvent.click(screen.getByTestId("disconnect-connector-notion"));
    // It asks first; nothing is sent until the question is answered.
    expect(screen.getByText("Disconnect Notion?")).toBeTruthy();
    expect(disconnectConnector).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("confirm-dialog-ok"));
    await waitFor(() => expect(disconnectConnector).toHaveBeenCalledWith("notion"));
    expect(onChanged).toHaveBeenCalled();
    expect(onGone).toHaveBeenCalled();
  });

  it("is hidden while the connector is not connected", () => {
    render(
      <DisconnectConnector c={connector({ connected: false })} onChanged={() => {}} />,
    );
    expect(screen.queryByTestId("disconnect-connector-notion")).toBeNull();
  });

  it("is hidden for a built-in that has nothing to disconnect", () => {
    render(
      <DisconnectConnector c={connector({ auth: "none" })} onChanged={() => {}} />,
    );
    expect(screen.queryByTestId("disconnect-connector-notion")).toBeNull();
  });

  it("cannot be double-fired while the request is in flight", async () => {
    let release: (v: { ok: boolean }) => void = () => {};
    disconnectConnector.mockImplementationOnce(
      () => new Promise((r) => (release = r)),
    );
    render(<DisconnectConnector c={connector()} onChanged={() => {}} />);
    fireEvent.click(screen.getByTestId("disconnect-connector-notion"));
    const ok = screen.getByTestId("confirm-dialog-ok");
    fireEvent.click(ok);
    await waitFor(() => expect((ok as HTMLButtonElement).disabled).toBe(true));
    fireEvent.click(ok);
    expect(disconnectConnector).toHaveBeenCalledTimes(1);
    release({ ok: true });
  });

  it("Cancel leaves the connector connected", () => {
    render(<DisconnectConnector c={connector()} onChanged={() => {}} />);
    fireEvent.click(screen.getByTestId("disconnect-connector-notion"));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByTestId("confirm-dialog")).toBeNull();
    expect(disconnectConnector).not.toHaveBeenCalled();
  });
});
