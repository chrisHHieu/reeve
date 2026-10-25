// Two delegated spawns in one message: ONE delegation card, two nodes, and BOTH doorways
// (each into its own tab of the workspace panel, owner 2026-10-13)
// work — the regression the owner hit (2026-09-20) where the second node was dead because
// its child session id had not arrived yet, and the engine ran the children one after the
// other. The fixture sends each spawn's creation frame immediately, exactly like the
// manager's creation frame (step C).
import { expect } from "@playwright/test";
import { test } from "./fixtures";

test("two delegated spawns: one card, both nodes openable, each opens its own child", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();
  const box = page.getByPlaceholder(/Ask anything/);
  await box.fill("delegate two probes");
  await box.press("Enter");

  // While they run, both nodes exist and both are openable — each creation frame carried
  // its child id, so neither node waits for the other to finish.
  const nodes = page.getByTestId("task-row");
  await expect(nodes).toHaveCount(2, { timeout: 10_000 });
  await expect(page.getByTestId("delegation-metrics")).toContainText("0 of 2 finished");
  for (const node of await nodes.all()) {
    await expect(node).toHaveAttribute("data-openable", "true");
  }

  // Each node names the model its child actually runs on (the creation frame's `model`),
  // so a route override is visible at a glance.
  await expect(nodes.first().getByTestId("task-model")).toHaveText("deepseek:deepseek-flash");
  await expect(nodes.nth(1).getByTestId("task-model")).toHaveText(
    "zai-coding-plan:glm-5.3-flash",
  );

  // Node 1 opens ITS child, in a tab of its own; the chat stays on screen.
  await nodes.first().locator(".delegation-node-header").click();
  await expect(page.getByTestId("child-view")).toBeVisible();
  await expect(page.getByTestId("child-transcript")).toContainText("Probe A done — alpha");
  const childTabs = page.locator('[data-testid^="rail-tab-subagent-"]');
  await expect(childTabs).toHaveCount(1);
  await expect(nodes.first()).toHaveAttribute("data-shown", "true");

  // The turn settles; expand it and the card again.
  await expect(page.getByText("Both probes reported back — see the runs.").first()).toBeVisible({
    timeout: 10_000,
  });
  await page.getByTestId("delegation-card-head").first().click();
  await expect(page.getByTestId("delegation-metrics")).toContainText("2 of 2 finished");

  // Node 2 — the one that used to be dead — opens ITS child, in a second tab.
  await page.getByTestId("task-row").nth(1).locator(".delegation-node-header").click();
  await expect(page.getByTestId("child-view")).toBeVisible();
  await expect(page.getByTestId("child-transcript")).toContainText("Probe B done — beta");
  await expect(childTabs).toHaveCount(2);
  await expect(page.getByTestId("task-row").nth(1)).toHaveAttribute("data-shown", "true");
  await expect(page.getByTestId("task-row").first()).not.toHaveAttribute("data-shown", "true");

  // Opening is never a duplicate: node 1 again brings its tab forward.
  await page.getByTestId("task-row").first().locator(".delegation-node-header").click();
  await expect(childTabs).toHaveCount(2);
  await expect(page.getByTestId("child-transcript")).toContainText("Probe A done — alpha");
  // A finished child's tab wears its check, not the working mark.
  await expect(childTabs.first().locator('.rail-mark[data-state="completed"]')).toBeVisible();
});

// The card's list opens and closes as a motion (owner, 2026-10-13: a body that was mounted and
// unmounted on the click was simply there, or gone, on the next frame). Heights a frame at a
// time: a region that moves is seen between shut and open, both ways.
test("the delegation card's list opens and closes as a motion", async ({ page }) => {
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();
  const box = page.getByPlaceholder(/Ask anything/);
  await box.fill("delegate two probes");
  await box.press("Enter");
  await expect(page.getByText("Both probes reported back — see the runs.").first()).toBeVisible({
    timeout: 10_000,
  });

  const head = page.getByTestId("delegation-card-head").first();
  if ((await head.getAttribute("aria-expanded")) !== "true") await head.click();
  await expect(page.getByTestId("task-row")).toHaveCount(2);
  const card = head.locator("xpath=..");
  const fold = card.locator(".ui-disclosure").first();
  await expect(fold).toHaveAttribute("data-open", "true");
  await page.waitForTimeout(400);
  const full = await fold.evaluate((el) => el.getBoundingClientRect().height);
  expect(full).toBeGreaterThan(30);

  const heightsAfterClick = () =>
    card.evaluate(
      (el) =>
        new Promise<number[]>((resolve) => {
          const seen: number[] = [];
          (el.querySelector("[data-testid=delegation-card-head]") as HTMLElement).click();
          const started = performance.now();
          const frame = () => {
            const region = el.querySelector(".ui-disclosure");
            seen.push(region ? Math.round(region.getBoundingClientRect().height) : 0);
            if (performance.now() - started > 700) resolve(seen);
            else requestAnimationFrame(frame);
          };
          requestAnimationFrame(frame);
        }),
    );
  const between = (heights: number[]) => heights.filter((height) => height > 3 && height < full - 3);

  const closing = await heightsAfterClick();
  expect(between(closing).length, `closing heights: ${closing.join(",")}`).toBeGreaterThan(3);
  expect(closing[closing.length - 1]).toBe(0);
  await expect(page.getByTestId("task-row")).toHaveCount(0);

  const opening = await heightsAfterClick();
  expect(between(opening).length, `opening heights: ${opening.join(",")}`).toBeGreaterThan(3);
  expect(Math.abs(opening[opening.length - 1] - full)).toBeLessThan(2);
});

// The opened list beside the rail, and a child's line in a narrow column (owner, 2026-10-13):
// the list lay flush left on the timeline's rail, and beside an open Workspace panel the task's
// name had no width at all while "Running" was drawn under the row's "Open".
test("the delegation list sits beside the rail, and a narrow column overlaps nothing", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();
  const box = page.getByPlaceholder(/Ask anything/);
  await box.fill("delegate two probes");
  await box.press("Enter");
  await expect(page.getByTestId("task-row")).toHaveCount(2, { timeout: 10_000 });

  const geo = await page.evaluate(() => {
    const box = (el: Element) => el.getBoundingClientRect();
    const icon = box(document.querySelector(".delegation-card-icon")!);
    const list = box(document.querySelector(".delegation-agents")!);
    const rows = [...document.querySelectorAll(".delegation-node")].map((row) => {
      const desc = box(row.querySelector(".delegation-node-desc")!);
      const facts = box(row.querySelector(".delegation-node-facts")!);
      const actions = box(row.querySelector(".delegation-node-actions")!);
      const node = box(row);
      const apart = facts.right <= actions.left + 1 || facts.bottom <= actions.top + 1;
      return { descWidth: desc.width, apart, inside: facts.right <= node.right && actions.right <= node.right };
    });
    return { iconRight: icon.right, listLeft: list.left, rows };
  });
  expect(geo.listLeft).toBeGreaterThanOrEqual(geo.iconRight);
  for (const row of geo.rows) {
    expect(row.descWidth).toBeGreaterThan(40);
    expect(row.apart).toBe(true);
    expect(row.inside).toBe(true);
  }
});
