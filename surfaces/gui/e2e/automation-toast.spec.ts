// When a schedule starts an automation, a toast at the top right says so for five
// seconds. The server sends `automation_run_started` on the app-wide stream; the toast names the
// automation and has View run (its session) and ✕, and a bar that drains as it waits.
import { expect, type Page } from "@playwright/test";
import { sendAppEvent, test } from "./fixtures";
import { composer } from "./steps";

/** The server saying a run of `task_title` has started on its schedule. */
const started = (page: Page, task_title = "Daily AI News") =>
  sendAppEvent(page, {
    type: "automation_run_started",
    data: { task_id: "task-1", task_title, session_id: "run-live-1", workspace: "/tmp/aw", agent: "reeve", trigger: "schedule" },
  });

const toast = (page: Page) => page.getByTestId("automation-toast");

test.beforeEach(async ({ page }) => {
  await page.goto("/");
});

test("a schedule-fired run pops the toast; View run opens its session", async ({ page }) => {
  await started(page);
  await expect(toast(page)).toContainText("Automation started");
  await expect(toast(page)).toContainText("Daily AI News");
  await toast(page).getByTestId("toast-view-run").click();
  await expect(toast(page)).toHaveCount(0);
  // The run's session is the one open now: its composer is there.
  await expect(composer(page)).toBeVisible();
});

test("the toast dismisses on ✕ and by itself after ~5s", async ({ page }) => {
  await started(page);
  await expect(toast(page)).toBeVisible();
  await page.getByTestId("toast-dismiss").click();
  await expect(toast(page)).toHaveCount(0);

  // Left alone, it goes after its five seconds; two more allow for a slow machine.
  await started(page, "Weekly CRM digest");
  await expect(toast(page)).toContainText("Weekly CRM digest");
  await expect(toast(page)).toHaveCount(0, { timeout: 7000 });
});
