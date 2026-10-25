// The side panel opens only when the person opens it (owner, 2026-10-16). Until then a turn that
// ended having changed files opened it on Review by itself (layout step 3, 2026-10-13); that was
// taken out. What stays of "the work is the stage" is the width: in a conversation that changed
// files, a panel the person opens takes the room the conversation does not need, and the
// conversation keeps its floor.
import { expect } from "@playwright/test";
import { test } from "./fixtures";

const RAIL_HIDDEN_KEY = "reeve:rail-hidden:v1";

async function editTurn(page: import("@playwright/test").Page) {
  await page.getByPlaceholder(/Ask anything/).fill("edit the upload client");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByText("Added retry and a test.").last()).toBeVisible();
}

/** The panel starts put away, as the person keeps it (the fixtures otherwise open it). */
async function panelPutAway(page: import("@playwright/test").Page) {
  await page.addInitScript(() => {
    localStorage.setItem("reeve-e2e-rail-default", "1");
    localStorage.setItem("reeve:rail-hidden:v1", "1");
  });
}

async function showPanel(page: import("@playwright/test").Page) {
  await page.getByRole("button", { name: "Show side panel" }).click();
  await expect(page.locator(".right-rail")).toBeVisible();
}

test("a turn that changed files leaves the panel put away, and the stored choice the person's", async ({ page }) => {
  await panelPutAway(page);
  await page.goto("/");
  const stored = () => page.evaluate((key) => localStorage.getItem(key), RAIL_HIDDEN_KEY);
  const before = await stored();
  await editTurn(page);
  await page.waitForTimeout(500);
  await expect(page.locator(".right-rail")).toHaveCount(0);
  await expect(page.getByTestId("review-pane")).toHaveCount(0);
  expect(await stored()).toBe(before);
});

test("opened by the person in a conversation that changed files, the panel is wide", async ({ page }) => {
  await panelPutAway(page);
  await page.setViewportSize({ width: 1920, height: 1000 });
  await page.goto("/");
  await editTurn(page);
  await showPanel(page);
  // The work is the stage: the conversation keeps a third of the room, the pane the rest.
  await expect
    .poll(async () => {
      const chat = (await page.locator(".main-chat").boundingBox())!.width;
      const main = (await page.locator(".main").boundingBox())!.width;
      return Math.abs(chat - Math.max(450, Math.round(main * 0.33))) <= 2;
    })
    .toBe(true);
});

test("opening a conversation from history leaves the panel as it was", async ({ page }) => {
  await panelPutAway(page);
  await page.goto("/");
  await page.getByTitle("Weekly plan 1").first().click();
  await page.waitForTimeout(600);
  await expect(page.locator(".right-rail")).toHaveCount(0);
});

test("on a laptop the open panel folds the queue to the rail; expanded, the queue covers the page and leaves the way it came", async ({ page }) => {
  await panelPutAway(page);
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/");
  const navWidth = async () => Math.round((await page.locator(".sidebar").boundingBox())!.width);
  await expect.poll(navWidth).toBeGreaterThan(300);
  const storedNav = () => page.evaluate(() => localStorage.getItem("reeve:nav-collapsed:v1"));
  const before = await storedNav();
  await editTurn(page);
  await showPanel(page);
  // Folded for the panel, not by the person: their ⌘B choice is what it was.
  await expect.poll(navWidth).toBe(56);
  expect(await storedNav()).toBe(before);
  await page.waitForTimeout(500); // the column's slide (--dur-4) settles
  const chat = (await page.locator(".main-chat").boundingBox())!.width;
  expect(chat).toBeGreaterThanOrEqual(449);
  // The rail's toggle lays the queue over the page; the conversation and the panel do not move.
  await page.getByTestId("rail-dock").click();
  await expect(page.getByTestId("work-queue")).toBeVisible();
  await expect(page.locator(".app")).toHaveClass(/nav-overlay/);
  await page.waitForTimeout(300);
  expect(Math.abs((await page.locator(".main-chat").boundingBox())!.width - chat)).toBeLessThanOrEqual(1);
  // A click beside it closes it, sliding back into the rail.
  await page.getByTestId("nav-scrim").click({ position: { x: 420, y: 450 } });
  await expect(page.locator(".app")).toHaveClass(/nav-leaving/);
  await expect(page.getByTestId("work-queue")).toHaveCount(0);
});

test("above a laptop window the queue stays beside the open panel", async ({ page }) => {
  await panelPutAway(page);
  await page.setViewportSize({ width: 1600, height: 900 });
  await page.goto("/");
  await editTurn(page);
  await showPanel(page);
  await page.waitForTimeout(400);
  expect(Math.round((await page.locator(".sidebar").boundingBox())!.width)).toBeGreaterThan(300);
});
