// The session's frame (as of 2026-09-18; the Session log action gone 2026-09-19): the top
// bar has the session's name at its left and no tabs under it, and the model is picked in the
// composer. Getting about is the sidebar's, or its 56px rail when folded; the bar has none of it.
import { expect, type Page } from "@playwright/test";
import { test } from "./fixtures";
import { openPinned, say } from "./steps";

const absent = (page: Page, testId: string) => expect(page.getByTestId(testId)).toHaveCount(0);

/** Send a message and wait for its echo. */
async function oneTurn(page: Page) {
  await say(page, "hello");
  await expect(page.getByText(/Echo: hello/)).toBeVisible();
}

test.beforeEach(async ({ page }) => {
  await page.goto("/");
});

test("session header: left title + mode chip, tabs under the bar, no separate log action", async ({ page }) => {
  // The open sidebar is the way about: no second set in the bar, and no floating button to
  // bring it back (the rail is that).
  await expect(page.locator(".sidebar")).toBeVisible();
  await absent(page, "topbar-cluster");
  await expect(page.locator(".nav-reveal-btn")).toHaveCount(0);

  await oneTurn(page);
  await expect(page.locator(".main-title")).toContainText("Draft the launch note");
  // There is one agent, so the bar does not name it; one view of the conversation, so no tabs;
  // and no "Session log" to open beside it.
  await absent(page, "topbar-agent");
  await expect(page.locator(".main-topbar .conv-tabs")).toHaveCount(0);
  await absent(page, "conv-tab-chat");
  await absent(page, "session-log");
  await expect(page.getByRole("button", { name: "Session log" })).toHaveCount(0);
});

test("topbar center: the session name + mode only; the model stays a live composer picker", async ({ page }) => {
  // No subtitle of facts and no folder or branch in the bar; the model is a picker in the
  // composer, before the first turn and after it (models switch mid-session).
  const picker = page.getByTestId("model-picker-trigger");
  await absent(page, "session-subtitle");
  await expect(page.locator(".main-title .title-meta")).toHaveCount(0);
  await absent(page, "topbar-agent");
  await expect(picker).toContainText("Claude Opus 4.8");

  await oneTurn(page);
  await absent(page, "session-subtitle");
  await expect(picker).toContainText("Claude Opus 4.8");
});

// The composer's three controls. The folder and roots popover and the Inbox control it once had
// are gone from it.
test("composer is three controls (+ attach · Mode · send); folder and branch chips are gone", async ({ page }) => {
  await openPinned(page);
  for (const [name, exact] of [["Attach", false], ["Mode", true], ["Send", false]] as const) {
    await expect(page.getByRole("button", { name, exact })).toBeVisible();
  }
  await expect(page.getByTitle(/director(y|ies) the agent can use/)).toHaveCount(0);
  await expect(page.getByTitle("Inbox routing")).toHaveCount(0);
  for (const gone of [".wschip", ".wsbranch"]) await expect(page.locator(gone)).toHaveCount(0);
});
