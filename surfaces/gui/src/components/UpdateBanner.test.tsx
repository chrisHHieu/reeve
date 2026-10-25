// The update offer: checked on a timer, put off per version, fetched ahead. Driven through a mocked
// `reeveDesktop` bridge. The browser build draws nothing, so the e2e suite never meets it; these are
// its tests.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { UpdateBanner } from "./UpdateBanner";

const FIRST_CHECK_MS = 15_000;
const RECHECK_MS = 30 * 60_000;

let bridge: ReturnType<typeof vi.fn>;
let release: { version: string; notes: string } | null;
let fetchFiles: () => Promise<void>;

beforeEach(() => {
  vi.useFakeTimers();
  release = { version: "1.2.0", notes: "" };
  fetchFiles = async () => {};
  bridge = vi.fn(async (command: string) => {
    if (command === "check_for_update") return release;
    if (command === "download_update") return fetchFiles();
    return null;
  });
  (globalThis as any).reeveDesktop = { invoke: bridge, on: () => () => {} };
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  delete (globalThis as any).reeveDesktop;
});

const wait = (ms: number) => act(() => vi.advanceTimersByTimeAsync(ms));
const installButton = () => screen.getByTestId("update-install") as HTMLButtonElement;

describe("UpdateBanner", () => {
  it("appears once the first check, after boot, finds a release", async () => {
    render(<UpdateBanner />);
    expect(screen.queryByTestId("update-banner")).toBeNull();
    await wait(FIRST_CHECK_MS);
    expect(screen.getByTestId("update-banner").textContent).toContain("v1.2.0");
    // The files were fetched at once, so the button is ready.
    expect(installButton().textContent).toBe("Restart to update");
    expect(installButton().disabled).toBe(false);
  });

  it("Later hides it, and a check that finds the same version keeps it hidden", async () => {
    render(<UpdateBanner />);
    await wait(FIRST_CHECK_MS);
    fireEvent.click(screen.getByTestId("update-later"));
    expect(screen.queryByTestId("update-banner")).toBeNull();
    await wait(RECHECK_MS);
    expect(screen.queryByTestId("update-banner")).toBeNull();
  });

  it("a NEWER version found later is offered even after Later", async () => {
    render(<UpdateBanner />);
    await wait(FIRST_CHECK_MS);
    fireEvent.click(screen.getByTestId("update-later"));
    release = { version: "1.3.0", notes: "" };
    await wait(RECHECK_MS);
    expect(screen.getByTestId("update-banner").textContent).toContain("v1.3.0");
  });

  it("the button says Downloading… and waits until the files are in", async () => {
    let arrive!: () => void;
    fetchFiles = () => new Promise((resolve) => (arrive = resolve));
    render(<UpdateBanner />);
    await wait(FIRST_CHECK_MS);
    expect(installButton().textContent).toBe("Downloading…");
    expect(installButton().disabled).toBe(true);
    await act(async () => arrive());
    expect(installButton().textContent).toBe("Restart to update");
    expect(installButton().disabled).toBe(false);
  });

  it("when fetching ahead fails, the button downloads on click instead", async () => {
    fetchFiles = () => Promise.reject(new Error("offline"));
    render(<UpdateBanner />);
    await wait(FIRST_CHECK_MS);
    expect(installButton().textContent).toBe("Restart to update");
    expect(installButton().disabled).toBe(false);
    fireEvent.click(installButton());
    expect(bridge).toHaveBeenCalledWith("install_update", undefined);
  });
});
