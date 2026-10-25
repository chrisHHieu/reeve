// The goal strip (src/components/session/GoalDock.tsx): the standing objective docked
// above the composer, its live/appearance path and its four controls.
//
// The reload source is GET /v1/sessions/{id}/goal (fixtures return null by default, so
// every other spec runs without a strip); this spec drives the LIVE path — the scripted
// agent pushes the driver's own `goal` frame — and then each control through the route.
import { expect } from "@playwright/test";
import { test } from "./fixtures";

test("the goal strip appears live and its controls round-trip", async ({ page }) => {
  await page.goto("/");
  // No goal yet: the strip renders nothing at all.
  await expect(page.getByTestId("goal-dock")).toHaveCount(0);

  await page.getByPlaceholder(/Ask anything/).fill("start a goal");
  await page.getByRole("button", { name: "Send" }).click();

  const dock = page.getByTestId("goal-dock");
  await expect(dock).toBeVisible();
  // The segment says the objective; a goal that is simply running has no word beside it.
  await expect(page.getByTestId("goal-dock-state")).toHaveCount(0);
  await expect(page.getByTestId("goal-dock-objective")).toHaveText("Ship the launch note");
  // The rest is in its panel, which opens under the row.
  await page.getByTestId("goal-dock-toggle").click();
  await expect(page.getByTestId("goal-dock-rounds")).toHaveText("round 2"); // default cap: the round alone

  // Pause → the durable phase moves and the pause button becomes resume.
  await page.getByRole("button", { name: "Pause goal" }).click();
  await expect(page.getByTestId("goal-dock-state")).toHaveText("Paused");
  await expect(page.getByRole("button", { name: "Resume goal" })).toBeVisible();

  await page.getByRole("button", { name: "Resume goal" }).click();
  await expect(page.getByTestId("goal-dock-state")).toHaveCount(0);

  // Edit inline: the objective and the cap, saved together.
  await page.getByRole("button", { name: "Edit goal" }).click();
  await page.getByLabel("Goal objective").fill("Ship the release notes");
  await page.getByLabel("Round limit").fill("8");
  await page.getByRole("button", { name: "Save goal" }).click();
  await expect(page.getByTestId("goal-dock-objective")).toHaveText("Ship the release notes");
  await expect(page.getByTestId("goal-dock-rounds")).toHaveText("round 2 of 8");

  // Clear asks before deleting, in a dialog.
  await page.getByRole("button", { name: "Clear goal" }).click();
  await expect(page.getByTestId("confirm-dialog")).toContainText("Clear this goal?");
  await page.getByTestId("confirm-dialog-ok").click();
  await expect(page.getByTestId("goal-dock")).toHaveCount(0);
});

// The composer's `/goal` command: the popover row runs the bare
// status, a typed objective runs through POST …/goal, every result/refusal is a quiet
// transcript block, and the strip follows the broadcast the route sends.
test("the /goal command reports, creates, refuses a replacement, and clears", async ({
  page,
}) => {
  await page.goto("/");
  const box = page.getByPlaceholder(/Ask anything/);

  // The popover entry runs the bare status: usage only, no strip, no mutation.
  await box.fill("/goal");
  await page.getByTestId("slash-command-goal").click();
  await expect(page.getByTestId("command-notice")).toContainText(
    "No goal is currently set.",
  );
  await expect(page.getByTestId("goal-dock")).toHaveCount(0);
  await expect(box).toHaveValue("");

  // A typed objective creates through the route; the broadcast moves the strip.
  await box.fill("/goal Ship the release notes");
  await box.press("Enter");
  await expect(page.getByTestId("command-notice").last()).toContainText("Goal created");
  await expect(page.getByTestId("command-notice").last()).toContainText(
    "Objective: Ship the release notes",
  );
  await expect(page.getByTestId("goal-dock-objective")).toHaveText("Ship the release notes");
  await expect(page.getByTestId("goal-dock-rounds")).toHaveText("round 0");

  // Replacing an unfinished goal is refused in the transcript, never silently applied.
  await box.fill("/goal A different objective");
  await box.press("Enter");
  await expect(page.getByTestId("command-notice").last()).toContainText(
    "Use /goal edit <objective> to change it or /goal clear before replacing it.",
  );
  await expect(page.getByTestId("goal-dock-objective")).toHaveText("Ship the release notes");

  // `pause` verb → the route; the strip reads paused.
  await box.fill("/goal pause");
  await box.press("Enter");
  await expect(page.getByTestId("command-notice").last()).toContainText("Goal paused");
  await expect(page.getByTestId("goal-dock-state")).toHaveText("Paused");

  // clear ends it.
  await box.fill("/goal clear");
  await box.press("Enter");
  await expect(page.getByTestId("command-notice").last()).toContainText("Goal cleared.");
  await expect(page.getByTestId("goal-dock")).toHaveCount(0);
});

// The harness's own accounting — what the rounds spent against the budget, and a
// stall building before the harness blocks the goal. The row says "Stalled"; the panel says
// the rest in words (no bar), nothing spilling out of either; the budget is edited in the
// words a person types.
test("the goal says the spend, the budget and a stall in words", async ({ page }) => {
  await page.goto("/");
  await page.getByPlaceholder(/Ask anything/).fill("start a stalling goal");
  await page.getByRole("button", { name: "Send" }).click();

  const dock = page.getByTestId("goal-dock");
  await expect(dock).toBeVisible();
  await expect(page.getByTestId("goal-dock-state")).toHaveText("Stalled");
  await page.getByTestId("goal-dock-toggle").click();
  await expect(page.getByTestId("goal-dock-rounds")).toHaveText("round 6 · 3.42M of 5M tokens");
  await expect(page.getByTestId("goal-dock-stall")).toHaveText("No progress in the last 2 rounds, stops at 3");
  // No bar says any of this.
  await expect(page.getByTestId("activity-row").locator("progress, [role=progressbar]")).toHaveCount(0);
  // Everything sits inside the row and its panel: nothing wraps past them or overflows them.
  const row = page.getByTestId("activity-row");
  await expect(page.getByTestId("goal-dock-body")).toHaveAttribute("data-open", "true");
  await page.waitForTimeout(400); // the panel has finished opening
  const box = (await row.boundingBox())!;
  for (const id of ["goal-dock-stall", "goal-dock-rounds", "goal-dock-state", "goal-dock-objective"]) {
    const part = (await page.getByTestId(id).boundingBox())!;
    expect(part.x + part.width).toBeLessThanOrEqual(box.x + box.width + 0.5);
    expect(part.y).toBeGreaterThanOrEqual(box.y - 0.5);
    expect(part.y + part.height).toBeLessThanOrEqual(box.y + box.height + 0.5);
  }
  await row.screenshot({ path: test.info().outputPath("goal-row.png") });
  await page.setViewportSize({ width: 1440, height: 900 });
  await expect(page.getByTestId("goal-dock-spend")).toBeVisible();
  await row.screenshot({ path: test.info().outputPath("goal-row-wide.png") });

  await page.getByRole("button", { name: "Edit goal" }).click();
  const budget = page.getByLabel("Token budget");
  await expect(budget).toHaveValue("5M");
  await budget.fill("8M");
  await page.getByRole("button", { name: "Save goal" }).click();
  await expect(page.getByTestId("goal-dock-rounds")).toHaveText("round 6 · 3.42M of 8M tokens");
});
