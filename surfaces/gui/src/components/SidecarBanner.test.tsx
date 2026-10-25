// Local-service health banner: driven through a mocked `reeveDesktop` bridge (the browser
// build has no shell, so it renders nothing there — these unit tests are the coverage,
// the same posture UpdateBanner.test.tsx takes).
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";
import { SidecarBanner } from "./SidecarBanner";

let listeners: Record<string, (event: { payload: unknown }) => void>;
let status: unknown;
let invoke: ReturnType<typeof vi.fn>;

beforeEach(() => {
  listeners = {};
  status = null;
  invoke = vi.fn(async (cmd: string) => (cmd === "sidecar_status" ? status : null));
  (globalThis as any).reeveDesktop = {
    invoke,
    on: (name: string, cb: (payload: unknown) => void) => {
      listeners[name] = (event) => cb(event.payload);
      return () => delete listeners[name];
    },
  };
});

afterEach(() => {
  cleanup();
  delete (globalThis as any).reeveDesktop;
});

const emit = async (payload: unknown) =>
  act(async () => {
    listeners["sidecar-status"]?.({ payload });
  });

describe("SidecarBanner", () => {
  it("renders nothing in the browser build", () => {
    delete (globalThis as any).reeveDesktop;
    render(<SidecarBanner />);
    expect(screen.queryByTestId("sidecar-banner")).toBeNull();
  });

  it("stays hidden while the service is running", async () => {
    status = { state: "started", attempt: 0, max_attempts: 3, reason: null };
    render(<SidecarBanner />);
    await act(async () => {});
    expect(screen.queryByTestId("sidecar-banner")).toBeNull();
  });

  it("shows the restart attempt, then clears when the service is back", async () => {
    render(<SidecarBanner />);
    await act(async () => {});

    await emit({ state: "crashed", attempt: 0, max_attempts: 3, reason: "exit code 3" });
    expect(screen.getByTestId("sidecar-banner").textContent).toContain(
      "The local service stopped"
    );

    await emit({ state: "restarting", attempt: 2, max_attempts: 3, reason: null });
    expect(screen.getByTestId("sidecar-banner").textContent).toContain("(2/3)");

    await emit({ state: "started", attempt: 2, max_attempts: 3, reason: null });
    expect(screen.queryByTestId("sidecar-banner")).toBeNull();
  });

  it("explains a crash loop and offers a retry", async () => {
    render(<SidecarBanner />);
    await act(async () => {});
    await emit({
      state: "stopped",
      attempt: 3,
      max_attempts: 3,
      reason: "crashed 3 times in a row",
    });
    const banner = screen.getByTestId("sidecar-banner");
    expect(banner.textContent).toContain("crashed 3 times in a row");
    expect(screen.getByTestId("sidecar-reload")).toBeTruthy();
  });

  it("picks up a restart that began before this page loaded", async () => {
    // The reload-during-restart case: no event will arrive for the CURRENT state, so the
    // mount has to ask the shell once.
    status = { state: "restarting", attempt: 1, max_attempts: 3, reason: null };
    render(<SidecarBanner />);
    await act(async () => {});
    expect(screen.getByTestId("sidecar-banner").textContent).toContain("(1/3)");
  });
});
