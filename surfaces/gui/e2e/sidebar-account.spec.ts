// The rail's foot is one button, Settings (2026-10-11; the rail since 2026-10-13). There is no
// Inbox and no account menu any more: a session waiting on the person is at the top of the
// sidebar under "Needs you", and is answered in its own conversation.
import { expect } from "@playwright/test";
import { test } from "./fixtures";
import { openSettings } from "./steps";

test.beforeEach(async ({ page }) => {
  await page.goto("/");
});

test("the foot is one button that opens Settings: no Inbox, no menu", async ({ page }) => {
  const foot = page.getByTestId("rail-settings");
  await expect(foot).toBeVisible();
  await expect(foot).toHaveAttribute("title", /^Settings · (⌘|Ctrl\+),$/);
  await expect(page.getByTestId("inbox-chip")).toHaveCount(0);
  await expect(page.locator(".sidebar").getByText("Inbox")).toHaveCount(0);
  await expect(page.getByRole("button", { name: /Settings & more/i })).toHaveCount(0);
  await foot.click();
  await expect(page.getByTestId("app-menu")).toHaveCount(0);
  await expect(page.getByTestId("settings-shell")).toBeVisible();
});

test("a session that waits on the person is under Needs you, once, and is answered in place", async ({ page }) => {
  // The mock has one waiting: an approval in "Weekly plan 3", listed there and nowhere below.
  const needsYou = page.getByTestId("needs-you");
  await expect(needsYou).toContainText("Needs you");
  const row = needsYou.getByTitle("Weekly plan 3");
  await expect(row.getByTestId("queue-line")).toHaveText("Approval");
  await expect(page.getByTitle("Weekly plan 3")).toHaveCount(1);

  // Its conversation has the question and the buttons that answer it.
  await row.click();
  const view = page.locator(".main").first();
  await expect(view.getByText("Approve: run_shell")).toBeVisible();
  await expect(view.getByRole("button", { name: "Approve", exact: true })).toBeVisible();
});

test("Settings ▸ Connectors holds the servers; nothing routes messages from a page of its own", async ({ page }) => {
  await openSettings(page);
  await page.locator(".settings-rail-item", { hasText: "Connectors" }).click();
  await expect(page.getByTestId("add-custom-server")).toBeVisible();
  for (const gone of ["MCP servers", "Activity"]) await expect(page.getByRole("button", { name: gone, exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /Messaging routing/ })).toHaveCount(0);
});
