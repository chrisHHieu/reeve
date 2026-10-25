// Turns that end early. One stopped keeps what it had streamed; one a provider error ended leaves
// a line with Retry, which runs the turn again with no new message and goes once it recovers.
import { expect, type Page } from "@playwright/test";
import { test } from "./fixtures";
import { openPinned, say } from "./steps";

const PARTIAL = "The epic scrolls ever onward";
const retry = (page: Page) => page.getByTestId("notice-retry");

// The model panel is 560px wide from the right; a desktop-sized window keeps it whole when a test
// switches models.
test.use({ viewport: { width: 1680, height: 900 } });

test.beforeEach(async ({ page }) => {
  await openPinned(page);
});

// Seen by the owner on 2026-07-22: after Stop the partial answer stayed until the next turn
// started, then was gone. It lived only in the stream's buffer, which an `assistant_message`
// turns into a transcript item, and a stopped turn sends none. The buffer is now kept as an
// item when a turn is interrupted or fails.
test("interrupted partial stream survives the next turn", async ({ page }) => {
  await say(page, "stream the epic", "enter");
  await expect(page.getByText(PARTIAL).first()).toBeVisible({ timeout: 10_000 });
  await page.getByRole("button", { name: /Stop/ }).click();
  await expect(page.getByText("Interrupted.").first()).toBeVisible({ timeout: 5_000 });
  await expect(page.getByText(PARTIAL).first()).toBeVisible();

  await say(page, "continue please", "enter");
  await expect(page.getByText("Echo: continue please", { exact: false }).first()).toBeVisible({ timeout: 10_000 });
  await expect(page.getByText(PARTIAL).first()).toBeVisible();
});

test("provider error shows a retriable notice; Retry re-runs without a new user message", async ({ page }) => {
  await say(page, "please fail the turn", "enter");
  await expect(page.getByText("Error: model unreachable").first()).toBeVisible({ timeout: 10_000 });
  await retry(page).click();
  await expect(page.getByText("Recovered after retry.").first()).toBeVisible({ timeout: 10_000 });
  // The run again adds no bubble of its own, and the line, no longer the last thing, has no Retry.
  await expect(page.locator(".bubble-user")).toHaveCount(1);
  await expect(retry(page)).toHaveCount(0);
});

// Changing the model is how one recovers from a provider's error, so Retry has to outlast it. The
// lines a switch once drew used up the Retry (owner, 2026-07-23); since 2026-10-13 a switch draws
// none. The panel opens on the session model's provider, hence "All" first, and stays open for
// the thinking level.
test("Retry survives a model switch — the intended recovery path", async ({ page }) => {
  await say(page, "please fail the turn", "enter");
  await expect(retry(page)).toBeVisible({ timeout: 10_000 });
  await page.getByTestId("model-picker-trigger").click();
  await page.getByTestId("provider-row-all").click();
  await page.getByTestId("model-row-gpt-5.5").click();
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("model-picker-trigger")).toContainText("GPT 5.5");
  await expect(page.getByText(/Model switched/)).toHaveCount(0);

  await retry(page).click();
  await expect(page.getByText("Recovered after retry.").first()).toBeVisible({ timeout: 10_000 });
  await expect(retry(page)).toHaveCount(0);
});
