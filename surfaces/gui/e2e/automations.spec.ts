import { test, expect } from "./fixtures";

// A run of an automation opens as a live session, which once looked like any other chat with no
// way back (owner, 2026-07-04). The top bar names the automation before the session, and that
// name goes back to the automation's own page.
test("scheduled run session shows the run banner; Back returns to the task detail", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("rail-automations").click();
  await page.getByText("Daily AI News").first().click();
  const runNow = page.getByRole("button", { name: /Run now/ });
  await expect(runNow).toBeVisible();
  await expect(page.getByText("Each run is a conversation", { exact: false })).toBeVisible();

  await page.getByTitle("Open this run's conversation").click();
  const crumb = page.getByTestId("run-crumb");
  await expect(crumb).toHaveText("Daily AI News");
  // Back to this automation's page, not the list of them.
  await crumb.click();
  await expect(runNow).toBeVisible();
  await expect(page.getByText("Daily AI News").first()).toBeVisible();

  // A session that is not a run has no such crumb.
  await page.getByText("Draft the launch note").first().click();
  await expect(crumb).toHaveCount(0);
});
