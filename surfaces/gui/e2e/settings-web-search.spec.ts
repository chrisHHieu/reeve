// Settings ▸ Web search, against the hermetic fixture (it keeps the mode and whether a key is
// saved, and answers a test search like the real chain would).
import { expect } from "@playwright/test";
import { test } from "./fixtures";

test("web search: choose where searches go, save a key, and test it", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("rail-settings").click();
  await page.locator(".settings-rail-item", { hasText: "Web search" }).click();

  const card = page.getByTestId("web-search-card");
  await expect(card).toBeVisible();
  await expect(page.getByTestId("web-search-key-status")).toContainText("150 searches a day");

  // With no key, a test shows the fallback for what it is.
  await page.getByTestId("web-search-test").click();
  await expect(page.getByTestId("web-search-verdict")).toHaveAttribute("data-tone", "warn");
  await expect(page.getByTestId("web-search-verdict")).toContainText("DuckDuckGo answered");

  // Add a key: the field opens, and once saved the row says whose account searches use.
  await page.getByTestId("web-search-add").click();
  await page.getByTestId("web-search-key").fill("invented-key-for-the-fixture");
  await page.getByTestId("web-search-save").click();
  await expect(page.getByTestId("web-search-key-status")).toContainText("Saved 2026-10-05");
  await expect(page.getByTestId("web-search-key")).toHaveCount(0); // the field closes
  await page.getByTestId("web-search-test").click();
  await expect(page.getByTestId("web-search-verdict")).toHaveAttribute("data-tone", "ok");
  await expect(page.getByTestId("web-search-verdict")).toContainText("Exa answered");

  // DuckDuckGo only keeps the saved key.
  await page.getByTestId("web-search-mode-duckduckgo").click();
  await expect(page.getByTestId("web-search-mode-duckduckgo")).toHaveAttribute("aria-checked", "true");
  await expect(page.getByTestId("web-search-key-kept")).toContainText("Your Exa key is kept");

  // Back to Exa: the saved key is there to remove (it asks first).
  await page.getByTestId("web-search-mode-auto").click();
  await page.getByTestId("web-search-remove").click();
  await page.getByTestId("confirm-dialog-ok").click();
  await expect(page.getByTestId("web-search-key-status")).toContainText("150 searches a day");
});
