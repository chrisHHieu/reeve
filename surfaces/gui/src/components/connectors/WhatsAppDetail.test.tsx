// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Connector, WhatsAppKept } from "../../api";
import { keptLine, WhatsAppDetail } from "./WhatsAppDetail";

const whatsAppKept = vi.fn();
const setWhatsAppKeep = vi.fn();
const clearWhatsAppKept = vi.fn();

// A minimal mock: the real api module touches desktop globals on import.
vi.mock("../../api", () => ({
  whatsAppKept: () => whatsAppKept(),
  setWhatsAppKeep: (days: number | null) => setWhatsAppKeep(days),
  clearWhatsAppKept: () => clearWhatsAppKept(),
}));
// The page's other cards are not what these tests are about.
vi.mock("./ConnectorTools", () => ({ ConnectorTools: () => null }));
vi.mock("./DisconnectButton", () => ({ DisconnectRow: () => null }));

const c = {
  name: "whatsapp_personal",
  title: "WhatsApp (personal)",
  auth: "pairing",
  connected: true,
  account: "+84909999999",
  fields: [],
  tools: [],
} as unknown as Connector;

// Invented numbers: 1,240 messages in 38 chats since 3 Mar 2026, 4.2 MB.
const KEPT: WhatsAppKept = { messages: 1240, chats: 38, oldest: 1772496000, bytes: 4.2 * 1024 * 1024, keep_days: 90 };

beforeEach(() => {
  whatsAppKept.mockReset().mockResolvedValue(KEPT);
  setWhatsAppKeep.mockReset();
  clearWhatsAppKept.mockReset();
});
afterEach(cleanup);

describe("what the linked WhatsApp device keeps", () => {
  it("says how many messages, in how many chats, since when and how big", async () => {
    render(<WhatsAppDetail c={c} onChanged={() => {}} />);
    await waitFor(() => expect(screen.getByTestId("whatsapp-kept-count").textContent).toContain("1,240 messages in 38 chats"));
    const line = screen.getByTestId("whatsapp-kept-count").textContent!;
    expect(line).toContain("2026");
    expect(line).toContain("4.2 MB");
    expect(screen.getByTestId("whatsapp-keep-90").getAttribute("aria-checked")).toBe("true");
    expect(keptLine({ ...KEPT, messages: 0, chats: 0, oldest: null, bytes: 0 })).toMatch(/Nothing yet/);
  });

  it("saves a keep choice and shows what is left once it is applied", async () => {
    setWhatsAppKeep.mockResolvedValue({ ...KEPT, ok: true, messages: 300, keep_days: 30 });
    render(<WhatsAppDetail c={c} onChanged={() => {}} />);
    await screen.findByText(/1,240 messages/);
    fireEvent.click(screen.getByTestId("whatsapp-keep-30"));
    expect(setWhatsAppKeep).toHaveBeenCalledWith(30);
    await screen.findByText(/300 messages/);
    fireEvent.click(screen.getByTestId("whatsapp-keep-forever"));
    expect(setWhatsAppKeep).toHaveBeenLastCalledWith(null);
  });

  it("a refused choice goes back to the one that holds and says why", async () => {
    setWhatsAppKeep.mockResolvedValue({ ok: false, error: "keep messages for one of the choices" });
    render(<WhatsAppDetail c={c} onChanged={() => {}} />);
    await screen.findByText(/1,240 messages/);
    fireEvent.click(screen.getByTestId("whatsapp-keep-365"));
    expect(await screen.findByRole("alert")).toHaveProperty("textContent", "keep messages for one of the choices");
    expect(screen.getByTestId("whatsapp-keep-90").getAttribute("aria-checked")).toBe("true");
  });

  it("clears only after asking, and the page then says nothing is kept", async () => {
    clearWhatsAppKept.mockResolvedValue({ ...KEPT, ok: true, messages: 0, chats: 0, oldest: null, bytes: 8192 });
    render(<WhatsAppDetail c={c} onChanged={() => {}} />);
    await screen.findByText(/1,240 messages/);
    fireEvent.click(screen.getByTestId("whatsapp-clear"));
    expect(clearWhatsAppKept).not.toHaveBeenCalled();
    expect(screen.getByTestId("confirm-dialog").textContent).toContain("stays linked");
    fireEvent.click(screen.getByRole("button", { name: "Clear messages" }));
    await screen.findByText(/Nothing yet/);
    expect(clearWhatsAppKept).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId("whatsapp-clear")).toBeNull();
  });
});
