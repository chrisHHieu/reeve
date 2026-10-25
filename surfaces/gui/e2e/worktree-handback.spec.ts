// A child's work waiting in its own copy: the roster says a child is waiting for
// the person, the row lists what the copy holds, and Apply hands it back. Against the hermetic
// two-child delegation script, with the server's three worktree answers stubbed.
import { expect } from "@playwright/test";
import { test } from "./fixtures";

test("a child's kept copy waits on its row, and Apply hands it back", async ({ page }) => {
  let kept = true;
  const applied: string[] = [];
  await page.route(/\/v1\/sessions\/[^/]+\/children$/, (route) =>
    route.fulfill({
      json: {
        children: [
          { session_id: "probe-a", title: "Probe A", agent: "explore", status: "idle", ...(kept ? { worktree: true } : {}) },
          { session_id: "probe-b", title: "Probe B", agent: "explore", status: "idle" },
        ],
      },
    }),
  );
  await page.route(/\/v1\/sessions\/probe-a\/worktree$/, (route) =>
    route.fulfill({
      json: {
        worktree: kept
          ? {
              path: "/repo/.worktrees/subagent-probe-a",
              branch: "reeve-sub/probe-a",
              busy: false,
              files: [
                { path: "src/alpha.py", change: "modified", added: 4, removed: 1 },
                { path: "tests/test_alpha.py", change: "created", added: 12, removed: 0 },
              ],
            }
          : null,
      },
    }),
  );
  await page.route(/\/v1\/sessions\/probe-a\/worktree\/apply$/, (route) => {
    applied.push("probe-a");
    kept = false;
    return route.fulfill({ json: { ok: true, files: [] } });
  });

  await page.goto("/");
  await page.getByPlaceholder(/Ask anything/).fill("delegate two probes");
  await page.getByRole("button", { name: "Send" }).click();

  // Both children settle; the strip stays because one of them waits on the person.
  const dock = page.getByTestId("subagents-dock");
  await expect(dock).toHaveAttribute("data-state", "waiting", { timeout: 20_000 });
  await expect(page.getByTestId("subagents-dock-progress")).toHaveText("1 waiting for you");

  await page.getByTestId("subagents-dock-toggle").click();
  const panel = page.getByTestId("subagents-dock-body");
  const bar = panel.getByTestId("worktree-handback");
  await expect(bar).toHaveCount(1);
  await expect(bar.getByTestId("handback-summary")).toHaveText(
    "2 files changed in its own copy — not in your folder yet",
  );
  await bar.getByTestId("handback-files-toggle").click();
  await expect(bar.getByTestId("handback-files").locator("li")).toHaveCount(2);

  await bar.getByTestId("handback-apply").click();
  await expect(panel.getByTestId("worktree-handback")).toHaveCount(0);
  expect(applied).toEqual(["probe-a"]);
  // Nothing runs and nothing waits: the segment is the quiet count of what finished.
  await expect(dock).toHaveAttribute("data-state", "done");
  await expect(page.getByTestId("subagents-dock-progress")).toHaveText("2 finished");
});
