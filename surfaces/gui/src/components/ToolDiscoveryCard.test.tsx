// The Tool discovery card: reads the prefs-backed setting, and a click posts
// the new value. Off is the default and the fallback, so an absent or unknown value must
// render unchecked.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ToolDiscoveryCard } from "./SettingsView";

let calls: { url: string; init?: RequestInit }[] = [];

function stubFetch(settings: Record<string, unknown>) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      if (url.includes("/v1/settings/tool-discovery")) return { json: async () => ({ ok: true }) } as Response;
      return { json: async () => settings } as Response;
    }),
  );
}

afterEach(cleanup);

beforeEach(() => {
  calls = [];
});

describe("ToolDiscoveryCard", () => {
  it("renders unchecked when the setting is off", async () => {
    stubFetch({ tool_discovery: false });
    render(<ToolDiscoveryCard />);
    const toggle = await screen.findByTestId("tool-discovery-toggle");
    expect(toggle.getAttribute("aria-checked")).toBe("false");
  });

  it("renders unchecked when an older backend omits the field", async () => {
    stubFetch({});
    render(<ToolDiscoveryCard />);
    const toggle = await screen.findByTestId("tool-discovery-toggle");
    expect(toggle.getAttribute("aria-checked")).toBe("false");
  });

  it("renders checked when the setting is on", async () => {
    stubFetch({ tool_discovery: true });
    render(<ToolDiscoveryCard />);
    const toggle = await screen.findByTestId("tool-discovery-toggle");
    expect(toggle.getAttribute("aria-checked")).toBe("true");
  });

  it("posts true when ticked and false when unticked", async () => {
    stubFetch({ tool_discovery: false });
    render(<ToolDiscoveryCard />);
    const toggle = await screen.findByTestId("tool-discovery-toggle");

    fireEvent.click(toggle);
    await waitFor(() => {
      const posted = calls.filter((c) => c.url.includes("/v1/settings/tool-discovery"));
      expect(posted).toHaveLength(1);
      expect(JSON.parse(String(posted[0].init?.body))).toEqual({ enabled: true });
    });
    expect(toggle.getAttribute("aria-checked")).toBe("true");

    fireEvent.click(toggle);
    await waitFor(() => {
      const posted = calls.filter((c) => c.url.includes("/v1/settings/tool-discovery"));
      expect(posted).toHaveLength(2);
      expect(JSON.parse(String(posted[1].init?.body))).toEqual({ enabled: false });
    });
  });
});
