import type { Page } from "@playwright/test";
import { test, expect } from "./fixtures";

// The Mac's overlaid title bar, with room left for its traffic lights, is never laid out on
// Windows, which keeps its own title bar (misaligned there, 2026-07-21). The desktop shell sets
// __REEVE_PLATFORM__; each platform is played here, and the overlay's class read.

/** Load the app as the desktop shell would on `platform`. */
async function asDesktop(page: Page, platform: "windows" | "macos") {
  await page.addInitScript((name) => {
    (window as any).reeveDesktop = { invoke: async () => null, on: () => () => {} };
    (window as any).__REEVE_PLATFORM__ = name;
  }, platform);
  await page.goto("/");
}

test("windows platform gets no tauri-overlay layout", async ({ page }) => {
  await asDesktop(page, "windows");
  await expect(page.locator("html")).toHaveAttribute("data-platform", "windows");
  await expect(page.locator(".app.tauri-overlay")).toHaveCount(0);
});

test("macos platform keeps the overlay layout", async ({ page }) => {
  await asDesktop(page, "macos");
  await expect(page.locator(".app.tauri-overlay").first()).toBeVisible();
});
