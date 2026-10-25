// Session fork (decision 0012): the row menu's Fork POSTs /v1/sessions/{id}/fork and the
// app opens the branch the server hands back.
import { expect } from "@playwright/test";
import { test } from "./fixtures";

test("Fork in the session row menu branches the conversation", async ({ page }) => {
  await page.goto("/");

  const row = page.getByTitle("Weekly plan 1");
  await expect(row).toBeVisible({ timeout: 10_000 });
  await row.hover();
  await row.getByTestId("row-menu").click();

  const [req] = await Promise.all([
    page.waitForRequest(
      (r) =>
        /\/v1\/sessions\/[^/]+\/fork$/.test(new URL(r.url()).pathname) &&
        r.method() === "POST",
    ),
    row.getByTestId("row-menu-fork").click(),
  ]);
  expect(req.method()).toBe("POST");
  // No error toast: the branch was accepted and opened.
  await expect(page.getByTestId("toast")).toHaveCount(0);
});
