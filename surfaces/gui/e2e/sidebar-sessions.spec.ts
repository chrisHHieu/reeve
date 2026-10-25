import type { Page } from "@playwright/test";
import { test, expect } from "./fixtures";

// The work queue (2026-10-13): sessions grouped by what each needs, every project under
// them (2026-10-14); archiving that can be undone from the Archived fold; pinning; and a delete
// that asks in a dialog. A row's actions are in its ⋮ menu, shown on hover.

const row = (page: Page, title: string) => page.getByTitle(title);
const project = (page: Page, key: string) => page.locator(`[data-testid="queue-project"][data-project="${key}"]`);

/** Pick `action` from the row's ⋮ menu, after checking its label when one is given. */
async function rowMenu(page: Page, title: string, action: "archive" | "pin" | "delete", label?: string) {
  const target = row(page, title);
  await target.hover();
  await target.getByTestId("row-menu").click();
  const item = target.getByTestId(`row-menu-${action}`);
  if (label) await expect(item).toHaveText(label);
  await item.click();
}

test.beforeEach(async ({ page }) => {
  await page.goto("/");
});

test("the queue groups sessions by what each needs from the person", async ({ page }) => {
  await expect(page.getByTestId("work-queue")).toBeVisible();
  // "Weekly plan 3" waits on an approval: under Needs you, once, saying what it waits for.
  const needs = page.getByTestId("needs-you");
  await expect(needs.getByTitle("Weekly plan 3")).toBeVisible();
  await expect(row(page, "Weekly plan 3")).toHaveCount(1);
  await expect(needs.getByTitle("Weekly plan 3").getByTestId("queue-line")).toHaveText("Approval");
  // The others, active today, are in their projects under it; one recorded under a persona that
  // is gone is listed like the rest.
  await expect(project(page, "/Users/test/Reeve/launch-note").getByTitle("Draft the launch note")).toBeVisible();
  await expect(project(page, "").getByTitle("Weekly plan 1")).toBeVisible();
  await expect(project(page, "/Users/test/Reeve/ops-triage").getByTitle("Ops triage")).toBeVisible();
  await expect(page.getByTestId("sidebar-organize")).toHaveCount(0);
});

test("every project is listed under the queue, and folds and opens in place", async ({ page }) => {
  await expect(page.getByTestId("queue-project").locator(".q-proj-name")).toHaveText(["launch-note", "ops-triage", "Chats"]);
  await expect(page.getByTestId("queue-filter")).toHaveCount(0);
  const ops = project(page, "/Users/test/Reeve/ops-triage").locator("button.q-proj");
  await ops.click();
  await expect(ops).toHaveAttribute("aria-expanded", "false");
  await expect(row(page, "Ops triage")).toHaveCount(0);
  // Kept through a reload.
  await page.reload();
  await expect(page.getByTestId("work-queue")).toBeVisible();
  await expect(row(page, "Ops triage")).toHaveCount(0);
  await ops.click();
  await expect(row(page, "Ops triage")).toBeVisible();
});

test("archive via the row menu is reversible via the Archived disclosure", async ({ page }) => {
  await expect(row(page, "Weekly plan 2")).toBeVisible();
  await rowMenu(page, "Weekly plan 2", "archive");
  await expect(row(page, "Weekly plan 2")).toHaveCount(0);

  await page.getByTestId("archived-toggle").click();
  const archived = row(page, "Weekly plan 2");
  // The fold opens by its height; the row is used once it is open, as a person would. Pressed
  // half way, the runner scrolled the clipped fold to reach it and pressed where another row's
  // ⋮ had moved to.
  await expect
    .poll(() =>
      archived.evaluate((el) => {
        const fold = el.closest(".ui-disclosure") as HTMLElement;
        return fold.getBoundingClientRect().height >= (fold.firstElementChild as HTMLElement).scrollHeight - 1;
      }),
    )
    .toBe(true);

  // The same menu item undoes it, and the fold goes with its last row.
  await rowMenu(page, "Weekly plan 2", "archive", "Unarchive");
  await expect(page.getByRole("button", { name: /Archived/ })).toHaveCount(0);
  await expect(row(page, "Weekly plan 2")).toBeVisible();
});

// Started by a mention in Slack: an ordinary row, once, saying where it came from.
test("a mention-spawned session is a row like any other and says where it came from", async ({ page }) => {
  const mention = row(page, "#general — check the deploy?");
  await expect(mention).toBeVisible();
  await expect(mention).toHaveCount(1);
  await expect(page.getByTestId("from-slack-toggle")).toHaveCount(0);
  await expect(mention.getByTestId("row-meta")).toHaveText(/^Slack · /);
});

test("pin via the row menu puts the session first in its project, and unpin puts it back", async ({ page }) => {
  await expect(row(page, "Weekly plan 4")).toBeVisible();
  await rowMenu(page, "Weekly plan 4", "pin", "Pin");
  // First in its project, and only once.
  await expect(project(page, "").getByTestId("session-row").first()).toHaveAttribute("title", "Weekly plan 4");
  await expect(row(page, "Weekly plan 4").locator(".q-pin")).toBeVisible();
  await expect(row(page, "Weekly plan 4")).toHaveCount(1);

  await rowMenu(page, "Weekly plan 4", "pin", "Unpin");
  await expect(row(page, "Weekly plan 4").locator(".q-pin")).toHaveCount(0);
});

test("delete asks first, in a dialog that names the session; Cancel keeps it", async ({ page }) => {
  await expect(row(page, "Weekly plan 2")).toBeVisible();
  await rowMenu(page, "Weekly plan 2", "delete", "Delete…");
  const dialog = page.getByTestId("confirm-dialog");
  await expect(dialog).toContainText("Delete “Weekly plan 2”?");
  // Focus starts on Cancel: Enter on a dialog that has just appeared keeps what is there.
  await expect(dialog.getByRole("button", { name: "Cancel" })).toBeFocused();
  // It sits in the middle of the window both ways, tall or short (owner, 2026-10-12: placed 12vh
  // from the top, it looked wrong).
  for (const [width, height] of [[1920, 948], [1280, 600], [1280, 720]]) {
    await page.setViewportSize({ width, height });
    await expect
      .poll(async () => {
        const box = (await page.locator(".ui-dialog").boundingBox())!;
        return Math.abs(box.x + box.width / 2 - width / 2) <= 1 && Math.abs(box.y + box.height / 2 - height / 2) <= 1;
      }, { message: `the dialog at ${width}x${height}` })
      .toBe(true);
  }
  // A window with room for the queue beside the open panel (under 1500px it folds away).
  await page.setViewportSize({ width: 1600, height: 900 });
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(row(page, "Weekly plan 2")).toHaveCount(1);
  // The queue comes back with the wider window. Its column finishes opening before a row is used:
  // a menu opened while its list still moves is closed by the move.
  await expect.poll(async () => Math.round((await page.locator(".sidebar").boundingBox())!.width)).toBe(356);
  await page.waitForTimeout(400);

  await rowMenu(page, "Weekly plan 2", "delete");
  await page.getByTestId("confirm-dialog-ok").click();
  await expect(page.getByTestId("confirm-dialog")).toHaveCount(0);
  await expect(row(page, "Weekly plan 2")).toHaveCount(0);
});
