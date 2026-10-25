// The built-in browser's panel: one engine (Playwright), its mode and headless.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

vi.mock("../../api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api")>()),
  getBrowserConfig: vi.fn(),
  setBrowserConfig: vi.fn(),
}));
import { getBrowserConfig, setBrowserConfig } from "../../api";
import { BrowserBackendPanel } from "./BrowserBackend";

const CONFIG = {
  ok: true,
  backend: "playwright",
  mode: "isolated",
  endpoint: "",
  headless: true,
  connected: true,
  tool_count: 25,
  launch: "npx @playwright/mcp --browser chromium --isolated --headless",
};

beforeEach(() => {
  vi.mocked(getBrowserConfig).mockResolvedValue(CONFIG);
  vi.mocked(setBrowserConfig).mockResolvedValue({
    ...CONFIG,
    mode: "attach",
    endpoint: "http://127.0.0.1:9222",
    launch: "npx @playwright/mcp --browser chromium --cdp-endpoint http://127.0.0.1:9222",
  });
});

afterEach(cleanup);

describe("BrowserBackendPanel", () => {
  it("names the one engine and offers no engine picker", async () => {
    render(<BrowserBackendPanel onChanged={() => {}} />);
    expect(await screen.findByTestId("browser-backend-panel")).toBeTruthy();
    expect(screen.getByTestId("browser-engine").textContent).toContain("Playwright");
    // Chrome DevTools is its own built-in server now, named so the person can find the switch.
    expect(screen.getByTestId("browser-engine").textContent).toContain("built-in chrome-devtools");
    // Neither is an engine of THIS browser any more (2026-09-30): no picker.
    expect(screen.queryByTestId("browser-backend-chrome-devtools")).toBeNull();
    expect(screen.queryByTestId("browser-backend-stagehand")).toBeNull();
    expect(screen.getByTestId("browser-launch").textContent).toContain("@playwright/mcp");
  });

  it("saves attach mode with its endpoint", async () => {
    render(<BrowserBackendPanel onChanged={() => {}} />);
    await screen.findByTestId("browser-backend-panel");

    fireEvent.click(screen.getByTestId("browser-mode-attach"));
    fireEvent.change(screen.getByTestId("browser-endpoint"), {
      target: { value: "http://127.0.0.1:9222" },
    });
    fireEvent.click(screen.getByTestId("browser-save"));

    await waitFor(() =>
      expect(vi.mocked(setBrowserConfig)).toHaveBeenCalledWith(
        expect.objectContaining({ mode: "attach", endpoint: "http://127.0.0.1:9222" }),
      ),
    );
    expect(vi.mocked(setBrowserConfig).mock.calls[0][0]).not.toHaveProperty("backend");
    await waitFor(() =>
      expect(screen.getByTestId("browser-msg").textContent).toContain("Saved"),
    );
  });
});
