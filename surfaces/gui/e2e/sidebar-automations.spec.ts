// Automations on the rail: its clock under Search, with the count of runs not yet looked at. There is no "Scheduled" band (redesign of 2026-10-11): it listed again what
// the Automations page lists. Opening an automation's detail marks its runs seen, and the
// count drops at once (the AUTOMATIONS_CHANGED broadcast, not the 15s poll).
import { expect } from "@playwright/test";
import { test } from "./fixtures";

test("the nav row counts the new runs; an automation is a row only while its run runs, and opens the automation", async ({
  page,
}) => {
  await page.goto("/");
  const nav = page.getByTestId("rail-automations");
  await expect(nav).toHaveAttribute("aria-label", "Automations");
  await expect(nav).toContainText("2"); // fixtures: two unseen runs of Daily AI News
  await expect(nav).toHaveAttribute("aria-description", "2 new runs");

  await expect(page.getByTestId("scheduled-band")).toHaveCount(0);
  // The fixtures' Daily AI News run is running: one row under Running, and none for the
  // automation that is not (Weekly CRM digest).
  const run = page.getByTestId("automation-run-row");
  await expect(run).toHaveCount(1);
  await expect(page.getByTestId("queue-group-running").getByTestId("automation-run-row")).toHaveAttribute("title", "Daily AI News");
  await expect(page.locator(".sidebar").getByText("Weekly CRM digest")).toHaveCount(0);
  // Runs never appear as session rows (their sessions are __run__-prefixed and hidden).
  await expect(page.getByTitle("__run__r1")).toHaveCount(0);
  await run.getByRole("button").click();
  await expect(page.getByRole("heading", { name: "Daily AI News" })).toBeVisible();
});

test("opening an automation marks its runs seen, and the count leaves the nav row", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("rail-automations").click();
  await expect(page.getByRole("heading", { name: "Automations" })).toBeVisible();
  await page.getByText("Daily AI News").first().click();

  await expect(page.getByRole("heading", { name: "Daily AI News" })).toBeVisible();
  // Runs newer than the mark it had before it was opened are told apart…
  await expect(page.getByTestId("run-new").first()).toBeVisible();
  // …and the count is gone without waiting for a poll.
  await expect(page.getByTestId("rail-automations")).not.toContainText("2");
});

test("after an automation is deleted, the nav row lands on the list, never on its dead detail", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByTestId("rail-automations").click();
  await page.getByText("Weekly CRM digest").first().click();
  await expect(page.getByRole("heading", { name: "Weekly CRM digest" })).toBeVisible();
  await page.getByRole("button", { name: "More for Weekly CRM digest" }).click();
  await page.getByRole("menuitem", { name: "Delete…" }).click();
  await page.getByTestId("confirm-dialog-ok").click();
  await expect(page.getByTestId("automation-row")).toHaveCount(1);

  await page.getByTitle("Weekly plan 1").click();
  await page.getByTestId("rail-automations").click();
  await expect(page.getByRole("heading", { name: "Automations" })).toBeVisible();
  await expect(page.getByText("Loading…")).toHaveCount(0);
});
