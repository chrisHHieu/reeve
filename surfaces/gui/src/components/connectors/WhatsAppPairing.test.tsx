// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Connector } from "../../api";
import { WhatsAppPairing } from "./WhatsAppPairing";

const startWhatsAppPairing = vi.fn();
const whatsAppPairStatus = vi.fn();

// A minimal mock: the real api module touches Tauri globals on import.
vi.mock("../../api", () => ({
  startWhatsAppPairing: (ack: boolean, phone?: string) => startWhatsAppPairing(ack, phone),
  whatsAppPairStatus: () => whatsAppPairStatus(),
}));

const c = {
  name: "whatsapp_personal",
  title: "WhatsApp (personal)",
  auth: "pairing",
  risk_notice: "WhatsApp can ban numbers it sees doing this.",
  fields: [],
  tools: [],
} as unknown as Connector;

beforeEach(() => {
  startWhatsAppPairing.mockReset();
  whatsAppPairStatus.mockReset();
});
afterEach(cleanup);

describe("linking a personal WhatsApp number", () => {
  it("says the risk and starts nothing until it is acknowledged", () => {
    render(<WhatsAppPairing c={c} onConnected={() => {}} />);
    expect(screen.getByTestId("whatsapp-risk").textContent).toContain("ban numbers");
    const qr = screen.getByTestId("whatsapp-qr-start") as HTMLButtonElement;
    expect(qr.disabled).toBe(true);
    fireEvent.click(qr);
    expect(startWhatsAppPairing).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("whatsapp-ack"));
    expect(qr.disabled).toBe(false);
  });

  it("shows the drawn QR code and finishes when the phone links", async () => {
    startWhatsAppPairing.mockResolvedValue({ ok: true });
    whatsAppPairStatus
      .mockResolvedValueOnce({ running: true, paired: false, qr_svg: "data:image/svg+xml,qr" })
      .mockResolvedValue({ running: true, paired: true, number: "+84909999999", history_chunks: 2 });
    const done = vi.fn();
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      render(<WhatsAppPairing c={c} onConnected={done} />);
      fireEvent.click(screen.getByTestId("whatsapp-ack"));
      fireEvent.click(screen.getByTestId("whatsapp-qr-start"));
      await waitFor(() => expect(screen.getByTestId("whatsapp-qr").getAttribute("src")).toBe("data:image/svg+xml,qr"));
      expect(startWhatsAppPairing).toHaveBeenCalledWith(true, "");
      await act(async () => {
        vi.advanceTimersByTime(1600);
      });
      await waitFor(() => expect(screen.getByTestId("whatsapp-linked").textContent).toContain("+84909999999"));
      fireEvent.click(screen.getByTestId("whatsapp-done"));
      expect(done).toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });

  it("asks WhatsApp for a code for a phone number and shows it to type", async () => {
    startWhatsAppPairing.mockResolvedValue({ ok: true, code: "ABCD-EFGH" });
    whatsAppPairStatus.mockResolvedValue({ running: true, paired: false });
    render(<WhatsAppPairing c={c} onConnected={() => {}} />);
    fireEvent.click(screen.getByTestId("whatsapp-ack"));
    fireEvent.click(screen.getByTestId("whatsapp-phone-start"));
    const getCode = screen.getByTestId("whatsapp-code-start") as HTMLButtonElement;
    expect(getCode.disabled).toBe(true); // no number yet
    fireEvent.change(screen.getByTestId("whatsapp-phone"), { target: { value: "+84 909 999 999" } });
    fireEvent.click(getCode);
    await waitFor(() => expect(screen.getByTestId("whatsapp-code").textContent).toBe("ABCD-EFGH"));
    expect(startWhatsAppPairing).toHaveBeenCalledWith(true, "+84 909 999 999");
  });

  it("says why when WhatsApp cannot start", async () => {
    startWhatsAppPairing.mockResolvedValue({ ok: false, error: "experimental connectors are disabled" });
    render(<WhatsAppPairing c={c} onConnected={() => {}} />);
    fireEvent.click(screen.getByTestId("whatsapp-ack"));
    fireEvent.click(screen.getByTestId("whatsapp-qr-start"));
    await waitFor(() => expect(screen.getByTestId("whatsapp-error").textContent).toContain("disabled"));
  });
});
