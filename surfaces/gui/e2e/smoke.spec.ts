import { test, expect } from "./fixtures";
import { composer } from "./steps";

// The app comes up whole: its name, the rail, the work queue of sessions beside it, the composer.
test("app loads with the nav and composer", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("Reeve").first()).toBeVisible();
  for (const part of ["rail-new-session", "work-queue"]) await expect(page.getByTestId(part)).toBeVisible();
  await expect(composer(page)).toBeVisible();
});
