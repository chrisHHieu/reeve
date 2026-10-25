// `/open`: the composer's file picker searches the session's
// roots and opens the pick in the existing RightRail viewer.
import { expect } from "@playwright/test";
import { test } from "./fixtures";

test("/open searches the session's files and opens the pick in the rail", async ({ page }) => {
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();
  const box = page.getByPlaceholder(/Ask anything/);

  await box.fill("/open");
  await page.getByTestId("slash-command-open").click();

  const dialog = page.getByTestId("open-file-dialog");
  await expect(dialog).toBeVisible({ timeout: 10_000 });
  await expect(page.getByTestId("open-file-row")).toHaveCount(2);

  // Typing narrows through the server's search.
  await page.getByTestId("open-file-input").fill("notes");
  await expect(page.getByTestId("open-file-row")).toHaveCount(1);
  await page.getByTestId("open-file-row").first().click();
  await expect(dialog).toHaveCount(0);

  // The pick opened in the rail viewer (the mock read returns the notes body).
  await expect(page.getByText("hello from the explorer").first()).toBeVisible({
    timeout: 10_000,
  });
});
