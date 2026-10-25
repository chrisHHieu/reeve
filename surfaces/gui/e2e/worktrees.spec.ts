// `/workspace`: the sidebar's Worktrees section lists
// the current project's git worktrees and opens a fresh session in the chosen one.
import { expect } from "@playwright/test";
import { test } from "./fixtures";

test("/workspace lists git worktrees and opens a session in one", async ({ page }) => {
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();
  const box = page.getByPlaceholder(/Ask anything/);

  await box.fill("/workspace");
  await page.getByTestId("slash-command-workspace").click();

  const band = page.getByTestId("worktrees-band");
  await expect(band).toBeVisible();
  const rows = page.getByTestId("worktree-row");
  await expect(rows).toHaveCount(2);
  await expect(rows.nth(0)).toContainText("launch-note");
  await expect(rows.nth(0)).toContainText("primary");
  await expect(rows.nth(1)).toContainText("feature/statements");

  // "Open" starts a new session bound to that worktree path — the setup row names it.
  await rows.nth(1).click();
  await expect(page.getByTestId("setup-row")).toContainText("launch-note-feature", {
    timeout: 10_000,
  });
});
