// The subagents roster (components/session/SubagentsDock.tsx, a segment of the activity row)
// against the hermetic fixture's delegation scripts: it is the session-wide answer to "what is
// running", which the per-turn delegation card cannot give once the reader has scrolled past
// it, and the one home of the finished children too (a header chip held them until
// 2026-10-12).
import { expect } from "@playwright/test";
import { test } from "./fixtures";

test("the subagents segment lists the session's children and opens one", async ({ page }) => {
  await page.goto("/");
  await page.getByPlaceholder(/Ask anything/).fill("delegate two probes");
  await page.getByRole("button", { name: "Send" }).click();

  const dock = page.getByTestId("subagents-dock");
  await expect(dock).toBeVisible();
  await expect(page.getByTestId("subagents-dock-progress")).toContainText("running");
  // The closed header names the child that is working.
  await expect(page.getByTestId("subagents-dock-toggle")).toContainText("Probe");

  await page.getByTestId("subagents-dock-toggle").click();
  await expect(page.getByTestId("subagent-row")).toHaveCount(2);
  // Running is the moving mark, never a solid dot. The panel's row carries it: the closed row's
  // peek gives way to numbers when the column is narrow (2026-10-18).
  await expect(page.getByTestId("subagent-row").first().getByTestId("subagent-running-mark")).toBeVisible();

  // A row opens the child's own transcript — the same door the delegation card offers — while
  // its chevron reveals what the child was asked to do.
  await page.getByTestId("subagent-row-toggle").first().click();
  await expect(page.getByTestId("subagent-body")).toBeVisible();
  await page.getByTestId("subagent-open-main").first().click();
  await expect(page.getByTestId("child-view")).toBeVisible();
});

test("the header carries no run report: the roster is the row's alone, and stays beside an open child", async ({ page }) => {
  await page.goto("/");
  await page.getByPlaceholder(/Ask anything/).fill("delegate two probes");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByTestId("subagents-dock")).toBeVisible();
  await expect(page.locator(".main-topbar")).not.toContainText(/subagent/i);

  await page.getByTestId("subagents-dock-toggle").click();
  await page.getByTestId("subagents-dock-body").getByTestId("subagent-open-main").first().click();
  await expect(page.getByTestId("child-view")).toBeVisible();
  // The child opened beside the chat (owner, 2026-10-13), so the roster is still there, and
  // the row of the child being read is marked.
  await expect(page.getByTestId("subagents-dock")).toBeVisible();
  await expect(page.locator('[data-testid="subagent-row"][data-shown="true"]')).toHaveCount(1);
});

test("a running child's row moves between its tools, and the finished one waits behind its fold", async ({ page }) => {
  await page.goto("/");
  await page.getByPlaceholder(/Ask anything/).fill("delegate a long think");
  await page.getByRole("button", { name: "Send" }).click();

  // The row says what the child is doing without being opened.
  await expect(page.getByTestId("subagents-dock-live")).toHaveText("↳ Thinking");
  await expect(page.getByTestId("subagents-dock-progress")).toHaveText("1 running");

  await page.getByTestId("subagents-dock-toggle").click();
  const panel = page.getByTestId("subagents-dock-body");
  // The child that is still working is the one listed, though it was spawned second.
  const rows = panel.getByTestId("subagent-row");
  await expect(rows).toHaveCount(1);
  const deep = rows.first();
  await expect(deep).toContainText("Deep dive");
  // Between its tools: not the grep that already ended.
  await expect(deep.getByTestId("subagent-live")).toHaveText("↳ Thinking");
  // What it has spent so far, told after its first step — its result does not exist yet.
  await expect(deep.getByTestId("subagent-fact")).toHaveText("4.2k tok");
  // A clock, counting from when it started (75 s ago), with no frame arriving.
  const clock = deep.getByTestId("elapsed");
  await expect(clock).toHaveText(/^1m \d\ds$/);
  const before = await clock.textContent();
  await expect(clock).not.toHaveText(before ?? "", { timeout: 4_000 });
  // The settled sibling is one line until asked for, and keeps its measured figures.
  const fold = page.getByTestId("subagents-finished-toggle");
  await expect(fold).toHaveText("Finished 1");
  await fold.click();
  await expect(rows.nth(1)).toContainText("Quick look");
  await expect(rows.nth(1).getByTestId("subagent-fact")).toHaveText("1.8k tok");
});
