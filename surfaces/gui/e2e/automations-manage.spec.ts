// The Automations list is where an automation is run, paused and found out about: each row has
// its switch, what its last run came to, the count of runs not looked at, Run now and a menu.
// Deleting asks first. Seeded with two automations: one running with two new runs, one done.
import { expect } from "@playwright/test";
import { test } from "./fixtures";

async function openAutomations(page) {
  await page.goto("/");
  await page.getByTestId("rail-automations").click();
  await expect(page.getByText("Tasks Reeve runs for you on a schedule.")).toBeVisible();
}

const row = (page, title: string) => page.getByTestId("automation-row").filter({ hasText: title });

test("a row says when it runs, what its last run came to, and how many runs are new", async ({ page }) => {
  await openAutomations(page);
  const news = row(page, "Daily AI News");
  await expect(news).toContainText("Every day at ~5:40 PM");
  await expect(news).toContainText(/next (today|tomorrow) /); // an hour from now
  await expect(news.getByTestId("automation-last-run")).toContainText("Running · since");
  await expect(news.getByLabel("2 new runs")).toHaveText("2");

  const crm = row(page, "Weekly CRM digest");
  await expect(crm.getByTestId("automation-last-run")).toContainText(/Done · (today|yesterday) /); // two hours ago
  await expect(crm.getByLabel(/new run/)).toHaveCount(0);
});

test("Run now on a row starts a run and opens its conversation", async ({ page }) => {
  await openAutomations(page);
  await page.getByRole("button", { name: "Run Daily AI News now" }).click();
  const crumb = page.getByTestId("run-crumb");
  await expect(crumb).toBeVisible();
  await expect(crumb).toContainText("Daily AI News");
});

test("the switch on a row pauses the automation and turns it on again", async ({ page }) => {
  await openAutomations(page);
  const news = row(page, "Daily AI News");
  const onOff = news.getByRole("switch", { name: "Daily AI News: on or off" });
  await expect(onOff).toBeChecked();

  await onOff.click();
  await expect(onOff).not.toBeChecked();
  await expect(news).toContainText("paused");
  // The detail says the same: the switch is the automation's, not the row's.
  await news.getByTitle("Daily AI News").click();
  await expect(page.getByText(/^Paused · /)).toBeVisible();
  await page.getByRole("switch", { name: "Daily AI News: on or off" }).click();
  await expect(page.getByText(/^On · next run (today|tomorrow) /)).toBeVisible();
});

test("the menu's Edit opens the form with the automation in it, and Save is for a change", async ({ page }) => {
  await openAutomations(page);
  await page.getByRole("button", { name: "More for Daily AI News" }).click();
  await page.getByRole("menuitem", { name: "Edit" }).click();

  await expect(page.getByRole("heading", { name: "Edit automation" })).toBeVisible();
  await expect(page.getByText("Start from a template")).toHaveCount(0);
  const name = page.getByLabel("Name");
  await expect(name).toHaveValue("Daily AI News");
  await expect(page.getByRole("button", { name: "Repeats" })).toContainText("Every day");
  await expect(page.getByLabel("At", { exact: true })).toHaveValue("17:40");
  const save = page.getByRole("button", { name: "Save" });
  await expect(save).toBeDisabled(); // nothing changed yet

  await name.fill("Daily AI briefing");
  await save.click();
  await expect(page.getByRole("heading", { name: "Daily AI briefing" })).toBeVisible();
});

test("leaving a changed form asks first, and Cancel keeps what was typed", async ({ page }) => {
  await openAutomations(page);
  await page.getByRole("button", { name: "New automation" }).click();
  await page.getByLabel("Name").fill("PR watch");

  await page.getByTestId("automation-form").getByRole("button", { name: "Automations" }).click();
  const dialog = page.getByTestId("confirm-dialog");
  await expect(dialog).toContainText("Discard your changes?");
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(page.getByLabel("Name")).toHaveValue("PR watch");

  await page.getByTestId("automation-form").getByRole("button", { name: "Automations" }).click();
  await page.getByTestId("confirm-dialog-ok").click();
  await expect(page.getByTestId("automation-row")).toHaveCount(2);
});

test("Delete asks first; the last one gone leaves the empty page with its suggestions", async ({ page }) => {
  await openAutomations(page);
  await page.getByRole("button", { name: "More for Daily AI News" }).click();
  await page.getByRole("menuitem", { name: "Delete…" }).click();
  const dialog = page.getByTestId("confirm-dialog");
  await expect(dialog).toContainText("Delete “Daily AI News”?");
  // Cancel keeps it.
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(row(page, "Daily AI News")).toHaveCount(1);

  await page.getByRole("button", { name: "More for Daily AI News" }).click();
  await page.getByRole("menuitem", { name: "Delete…" }).click();
  await page.getByTestId("confirm-dialog-ok").click();
  await expect(row(page, "Daily AI News")).toHaveCount(0);
  await expect(row(page, "Weekly CRM digest")).toHaveCount(1);

  // From the automation's own page, too.
  await row(page, "Weekly CRM digest").getByTitle("Weekly CRM digest").click();
  await page.getByRole("button", { name: "More for Weekly CRM digest" }).click();
  await page.getByRole("menuitem", { name: "Delete…" }).click();
  await page.getByTestId("confirm-dialog-ok").click();

  const empty = page.getByTestId("automations-empty");
  await expect(empty).toContainText("Nothing runs on a schedule yet");
  // The suggestions are the templates that need no connection; one opens the form filled in.
  await empty.getByTestId("qs-template-news").click();
  await expect(page.getByLabel("Name")).toHaveValue("Morning news briefing");
  await expect(page.getByTestId("ob-create")).toBeEnabled();
});

test("the runs of an automation are a table: result, when, how long, who started it", async ({ page }) => {
  await openAutomations(page);
  await row(page, "Daily AI News").getByTitle("Daily AI News").click();

  const runs = page.getByTestId("automation-runs");
  await expect(runs.getByRole("columnheader")).toHaveText(["Result", "Started", "Took", "Started by", "Open"]);
  const first = runs.locator("tbody tr").first();
  await expect(first).toContainText("Running");
  await expect(first).toContainText("so far");
  await expect(first).toContainText("Schedule");
  await expect(first.getByTestId("run-new")).toBeVisible();
});

test("in a narrow window nothing is pushed out of a row: the result goes under the name", async ({ page }) => {
  await openAutomations(page);
  await page.setViewportSize({ width: 700, height: 800 });
  const news = row(page, "Daily AI News");
  const box = async (locator) => (await locator.boundingBox())!;
  const rowBox = await box(news);
  const menu = await box(news.getByRole("button", { name: "More for Daily AI News" }));
  const title = await box(news.getByTitle("Daily AI News"));
  const status = await box(news.getByTestId("automation-last-run"));
  expect(menu.x + menu.width).toBeLessThanOrEqual(rowBox.x + rowBox.width);
  expect(status.y).toBeGreaterThanOrEqual(title.y + title.height - 1);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});
