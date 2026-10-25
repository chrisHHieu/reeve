// The side panel starts hidden, and the top bar's toggle is remembered across a restart (owner,
// 2026-08-21). A link into it (an artifact's chip) shows it for the moment without changing what
// is remembered.
import { expect, type Page } from "@playwright/test";
import { test } from "./fixtures";
import { say } from "./steps";

/** Load the app with nothing remembered about the panel. The flag skips the mock's own choice,
 *  which opens it for the other specs. */
async function nothingRemembered(page: Page) {
  await page.goto("/");
  await page.evaluate(() => {
    localStorage.setItem("reeve-e2e-rail-default", "1");
    localStorage.removeItem("reeve:rail-hidden:v1");
  });
  await page.reload();
}

// The tab strip is always there while the panel is.
const strip = (page: Page) => page.getByTestId("rail-tabs");

test("rail is hidden by default; the toggle persists across restarts", async ({ page }) => {
  await nothingRemembered(page);
  await expect(strip(page)).toHaveCount(0);
  await page.getByRole("button", { name: "Show side panel" }).click();
  await expect(strip(page)).toBeVisible();
  await page.reload();
  await expect(strip(page)).toBeVisible();
  await page.getByRole("button", { name: "Hide side panel" }).click();
  await page.reload();
  await expect(strip(page)).toHaveCount(0);
});

test("an artifact mention force-shows the rail without overwriting the hidden preference", async ({ page }) => {
  await nothingRemembered(page);
  // The answer to this links an artifact.
  await say(page, "show the report");
  await page.getByTestId("artifact-mention").click();
  await expect(page.getByTestId("artifact-frame")).toBeVisible();
  await page.reload();
  await expect(strip(page)).toHaveCount(0);
});
