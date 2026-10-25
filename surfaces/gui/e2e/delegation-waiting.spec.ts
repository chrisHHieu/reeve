// A delegated call in line for a child slot says so. Two of six in one fan-out read "Running" with
// no child behind them while the session ran its four (owner, 2026-10-21): no model, no live line,
// no doorway. The manager now tells the row it is in line before it waits, and the child's first
// frame turns it into a running row.
import { expect } from "@playwright/test";
import { test } from "./fixtures";

test("a call in line for a slot reads Waiting, then Running once its child starts", async ({ page }) => {
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();
  const box = page.getByPlaceholder(/Ask anything/);
  await box.fill("delegate past the cap");
  await box.press("Enter");

  const nodes = page.getByTestId("task-row");
  await expect(nodes).toHaveCount(2, { timeout: 10_000 });
  const running = nodes.filter({ hasText: "Rewrite docs/API.md" });
  const inLine = nodes.filter({ hasText: "Rewrite docs/DEPLOYMENT.md" });
  await expect(running.getByTestId("task-status")).toHaveText("Running");
  await expect(inLine.getByTestId("task-status")).toHaveText("Waiting");
  await expect(inLine.getByTestId("task-live")).toHaveText("↳ Starts when one of the 4 running finishes");
  await expect(inLine).toHaveAttribute("data-waiting", "true");
  await expect(inLine.getByTestId("task-promote")).toHaveCount(0);
  await expect(page.getByTestId("delegation-metrics")).toContainText("0 of 2 finished");
  // Nothing is working yet, so its name reads in the row's quiet ink, not the working one.
  const ink = (selector: string) =>
    inLine.locator(selector).evaluate((el) => getComputedStyle(el).color);
  expect(await ink(".delegation-node-desc")).toBe(await ink(".delegation-node-facts"));
  await page.getByTestId("delegation-card").screenshot({ path: test.info().outputPath("waiting.png") });

  // A slot frees: the child reports in and the row is a running one, with its model.
  await expect(inLine.getByTestId("task-status")).toHaveText("Running", { timeout: 10_000 });
  await expect(inLine).not.toHaveAttribute("data-waiting", "true");
  await expect(inLine.getByTestId("task-model")).toHaveText("deepseek:deepseek-flash");
});
