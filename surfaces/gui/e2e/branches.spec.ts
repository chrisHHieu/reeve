// The person picks the folder's branch — from the top bar's crumb in a conversation
// (the composer's strip before the first message) or from Review's scope menu — pulls, and
// opens a pull request whose checks show on Review's bar.
// Before a session's first message the picker can start it in a new worktree instead.
import { expect } from "@playwright/test";
import { test } from "./fixtures";

async function startInRepo(page: import("@playwright/test").Page) {
  await page.goto("/");
  await page.getByPlaceholder(/Ask anything/).fill("edit the upload client");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByText("Added retry and a test.")).toBeVisible();
}

test("the top bar's crumb switches the branch, and Review follows", async ({ page }) => {
  await startInRepo(page);
  const chip = page.getByTestId("where-crumb-branch");
  await expect(chip).toContainText("main");
  await page.getByTestId("where-crumb").click();
  await page.getByTestId("where-switch-branch").click();
  const picker = page.getByTestId("branch-picker");
  await expect(picker).toContainText("This computer");
  await expect(picker).toContainText("On the remote");
  await expect(page.getByTestId("branch-main")).toContainText("↑1");
  // Uncommitted changes are said before a switch, not discovered after.
  await expect(page.getByTestId("branch-dirty")).toBeVisible();
  await page.screenshot({ path: test.info().outputPath("composer-branch-picker.png") });
  await page.getByTestId("branch-feature/statements").click();
  await expect(chip).toContainText("feature/statements");

  // The panel does not open Review by itself (owner, 2026-10-16): the person opens it.
  await page.getByTestId("rail-door-review").click();
  await expect(page.getByTestId("review-pane")).toBeVisible();
  await page.getByTestId("rvw-scope").click();
  await expect(page.getByTestId("rvw-scope-menu") .or(page.locator(".rvw-scope-menu"))).toContainText("Git · feature/statements");
});

test("a new branch from the picker, a pull request, and its checks on Review's bar", async ({ page }) => {
  await startInRepo(page);
  // The panel does not open Review by itself (owner, 2026-10-16): the person opens it.
  await page.getByTestId("rail-door-review").click();
  await expect(page.getByTestId("review-pane")).toBeVisible();
  const review = page.getByTestId("review-pane");

  // Pull while the branch is behind its upstream.
  await expect(page.getByTestId("rvw-sync")).toContainText("1 to pull");
  await expect(page.getByTestId("rvw-pull")).toHaveText("Pull");
  await page.getByTestId("rvw-pull").click();
  await expect(page.getByTestId("rvw-toast")).toContainText("up to date with origin/main");
  await expect(page.getByTestId("rvw-pull")).toHaveCount(0);

  // Switch branch from Review's scope menu, typing a new name.
  await page.getByTestId("rvw-scope").click();
  await page.getByTestId("rvw-switch-branch").click();
  await page.getByTestId("branch-search").fill("fix/retry");
  await review.screenshot({ path: test.info().outputPath("review-branch-create.png") });
  await page.getByTestId("branch-create").click();
  await expect(page.getByTestId("rvw-toast")).toContainText("Switched to fix/retry");
  await expect(page.getByTestId("where-crumb-branch")).toContainText("fix/retry");

  // Open a pull request from the options menu; its title is the last commit's subject.
  await page.getByTestId("rvw-options").click();
  await page.getByTestId("rvw-open-pr").click();
  await expect(page.getByTestId("pr-title")).toHaveValue("Add the upload client");
  await page.getByTestId("pr-title").fill("Retry uploads");
  await page.getByTestId("pr-body").fill("The client gave up on the first 503.");
  await page.screenshot({ path: test.info().outputPath("review-pr-dialog.png") });
  const popup = page.waitForEvent("popup").catch(() => null);
  await page.getByTestId("pr-create").click();
  await popup;
  await expect(page.getByTestId("rvw-toast")).toContainText("Pull request opened");

  // The badge: the number and the checks' verdict; the pop-over lists each check.
  const badge = page.getByTestId("pr-badge");
  await expect(badge).toContainText("#7");
  await expect(badge).toHaveAttribute("data-verdict", "running");
  await badge.click();
  await expect(page.getByTestId("pr-pop")).toContainText("Checks running");
  await expect(page.getByTestId("pr-pop")).toContainText("lint");
  await expect(page.getByTestId("pr-pop")).toContainText("fix/retry → main");
  await review.screenshot({ path: test.info().outputPath("review-pr-badge.png") });
});

test("before the first message, the picker starts the session in a new worktree", async ({ page }) => {
  // A desktop window: at 1280 with the side panel open the composer is under 420px and gives
  // the branch up first (styles.css, "A narrow card gives up the least useful things first").
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.route("**/v1/workspaces/recent", (route) =>
    route.fulfill({ json: { workspaces: [{ path: "/Users/test/Reeve/launch-note", name: "launch-note", exists: true }] } }),
  );
  await startInRepo(page);
  await page.getByTestId("rail-new-session").click();
  // A new session picks its folder first; the branch chip then shows that folder's branch.
  await page.getByTestId("folder-chip").click();
  await page.getByTestId("folder-option").first().click();
  const chip = page.getByTestId("branch-chip");
  await expect(chip).toBeVisible();
  await chip.click();
  await page.getByTestId("branch-as-worktree").check();
  await page.getByTestId("branch-search").fill("spike/uploads");
  await page.getByTestId("branch-create").click();
  await expect(page.getByTestId("setup-row")).toContainText("launch-note-spike-uploads");
});

test("when another session works in the folder, the picker names it and a new session starts on a worktree", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.route("**/v1/workspaces/recent", (route) =>
    route.fulfill({ json: { workspaces: [{ path: "/Users/test/Reeve/launch-note", name: "launch-note", exists: true }] } }),
  );
  await startInRepo(page);
  // Another session with a turn in flight in the same folder (the server's list says so).
  await page.route(/\/v1\/sessions(\?.*)?$/, (route) =>
    route.request().method() !== "GET"
      ? route.fallback()
      : route.fulfill({
          json: {
            sessions: [
              { session_id: "busy-1", title: "Fix the upload retry", workspace: "/Users/test/Reeve/launch-note", agent: "reeve", model: "m", mode: "interactive", updated_at: "2026-10-14 09:00:00", messages: 4, liveness: "working", attention: 0 },
            ],
          },
        }),
  );
  await page.getByTestId("rail-new-session").click();
  await expect(page.getByTitle("Fix the upload retry")).toBeVisible();
  await page.getByTestId("folder-chip").click();
  await page.getByTestId("folder-option").first().click();
  await page.getByTestId("branch-chip").click();
  await expect(page.getByTestId("branch-busy")).toContainText("“Fix the upload retry” is working in this folder. A new worktree");
  await expect(page.getByTestId("branch-as-worktree")).toBeChecked();
  await page.screenshot({ path: test.info().outputPath("branch-busy.png") });
});
