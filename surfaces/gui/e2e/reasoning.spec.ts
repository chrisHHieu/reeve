// A reasoning model's trace (design B, the owner's pick of 2026-09-28). While it is written it is
// not a row: its newest line is on the turn's status line, under "Thinking", the one live line at
// the foot. Once done it is a step like the others, a folded "Thought" row in the turn, and stays
// when the work is finished, since turns no longer fold.
import { expect } from "@playwright/test";
import { test } from "./fixtures";
import { openPinned, say } from "./steps";

test("thinking streams live, then persists in the turn as the same collapsed row", async ({ page }) => {
  await openPinned(page);
  await say(page, "think hard about this", "enter");

  await expect(page.getByTestId("turn-status-thought")).toContainText("Weighing options.", { timeout: 10_000 });
  await expect(page.getByTestId("turn-status-label").last()).toContainText("Thinking");
  await expect(page.locator('[data-testid="think-row"][data-state="running"]')).toHaveCount(0);

  await expect(page.getByText("Decision made.").first()).toBeVisible({ timeout: 10_000 });
  const thought = page.getByTestId("think-row").first();
  await expect(thought).toHaveAttribute("data-state", "ok");
  await expect(thought).toContainText("Thought");
  // Folded until opened, then the whole trace.
  await expect(page.getByTestId("think-body")).toHaveCount(0);
  await thought.locator("[data-disclosure-row]").click();
  await expect(page.getByTestId("think-body").first()).toContainText("Weighing options. Comparing tradeoffs. Settling it.");
});
