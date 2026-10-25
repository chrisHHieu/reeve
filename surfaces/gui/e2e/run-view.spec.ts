// A subagent's session, end to end (step E; owner, 2026-10-13): the delegated `task`
// call is one row with its children listed under it, and a child's line opens the CHILD's own
// session in the workspace panel, in a tab of its own, beside the chat that started it. It
// used to replace the chat column, so reading a child meant leaving the main agent.
//
// The old run-store route is gone (step E2): the child session is the only way in.
import { expect } from "@playwright/test";
import { test } from "./fixtures";

async function delegateResearch(page: import("@playwright/test").Page) {
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();
  const box = page.getByPlaceholder(/Ask anything/);
  await box.fill("delegate the research");
  await box.press("Enter");
  // The turn settles with the child's tool count (`run_steps` on the presentation).
  await expect(page.getByText("The explorer reported back — see the run.").first()).toBeVisible({
    timeout: 10_000,
  });
  // The settled turn is shown whole (no fold); open the delegation card inside it.
  await page.getByTestId("delegation-card-head").first().click();
}

test("a delegated call opens its child session beside the chat: a tab, its steps, read only", async ({ page }) => {
  await delegateResearch(page);
  const card = page.getByTestId("delegation-card").first();
  await expect(card).toContainText("Explore");
  await expect(card).toContainText("Research repo subagents");
  await expect(card).not.toContainText("Main agent");
  await expect(page.getByTestId("delegation-metrics").first()).toContainText("1 of 1 finished");
  await expect(page.getByTestId("task-steps").first()).toHaveText("3 steps");
  await expect(page.getByTestId("task-spinner")).toHaveCount(0);
  await expect(page.getByTestId("task-open").first()).toHaveText("Open");

  // The node itself is the doorway into the child.
  await page.getByTestId("task-row").first().locator(".delegation-node-header").click();
  const view = page.getByTestId("child-view");
  await expect(view).toBeVisible();
  // It is in the workspace panel, under a tab named by its task.
  await expect(page.locator(".right-rail").getByTestId("child-view")).toBeVisible();
  const tab = page.locator('[data-testid^="rail-tab-subagent-"]');
  await expect(tab).toHaveCount(1);
  await expect(tab).toContainText("Research repo subagents");
  await expect(tab).toHaveAttribute("aria-selected", "true");

  // The head says who it is; the task is the main agent's words, not a bubble of yours.
  await expect(page.getByTestId("child-head")).toContainText("Research repo subagents");
  await expect(page.getByTestId("child-facts")).toContainText("Explore");
  await expect(page.getByTestId("child-brief")).toContainText("Asked by the main agent");
  await expect(view.locator("[data-user-turn]")).toHaveCount(0);

  // The child's steps are a TRANSCRIPT, with the chat's own components, and its last answer
  // is named for what it is.
  const transcript = page.getByTestId("child-transcript");
  await expect(transcript).toContainText("subagent_spec.py");
  await expect(transcript).toContainText("max-steps.ts");
  await expect(page.getByTestId("turn-answer-label").last()).toContainText("Its report");
  await expect(page.getByTestId("child-foot-note")).toHaveText("Read only");

  // The chat did not go anywhere: the answer and the composer are still on screen, and the
  // row of the child being read is marked.
  await expect(page.getByText("The explorer reported back — see the run.").first()).toBeVisible();
  await expect(page.getByPlaceholder(/Ask anything/)).toBeVisible();
  await expect(page.getByTestId("task-row").first()).toHaveAttribute("data-shown", "true");
  await expect(page.getByTestId("task-open").first()).toHaveText("Shown");

  // Closing the tab closes the view, and the mark goes with it.
  await tab.getByRole("button", { name: /^Close/ }).click();
  await expect(page.getByTestId("child-view")).toHaveCount(0);
  await expect(page.getByTestId("task-open").first()).toHaveText("Open");
});

test("opening a child shows a hidden panel, and Esc is not taken from the chat", async ({ page }) => {
  await delegateResearch(page);
  await page.getByTestId("rail-hide").click();
  await expect(page.locator(".right-rail")).toHaveCount(0);

  await page.getByTestId("task-row").first().locator(".delegation-node-header").click();
  await expect(page.locator(".right-rail").getByTestId("child-view")).toBeVisible();

  // Esc used to leave the child's view. The child stays where it is now.
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("child-view")).toBeVisible();
});

test("in a window with no room for two columns the child takes the chat's place and leads back", async ({ page }) => {
  await page.setViewportSize({ width: 900, height: 720 });
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();
  // The panel lies over the chat at this width: put it away to reach the chat's rows.
  await page.keyboard.press("Control+Alt+B");
  await expect(page.locator(".right-rail")).toHaveCount(0);
  const box = page.getByPlaceholder(/Ask anything/);
  await box.fill("delegate the research");
  await box.press("Enter");
  await expect(page.getByText("The explorer reported back — see the run.").first()).toBeVisible({ timeout: 10_000 });
  await page.getByTestId("delegation-card-head").first().click();
  await page.getByTestId("task-row").first().locator(".delegation-node-header").click();

  await expect(page.getByTestId("child-view")).toBeVisible();
  await page.getByTestId("child-back").click();
  await expect(page.getByTestId("child-view")).toHaveCount(0);
  await expect(page.getByPlaceholder(/Ask anything/)).toBeVisible();
});
