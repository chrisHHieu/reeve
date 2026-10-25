// The Files tab: a tree over the session's folders. A folder opens and closes in the tree, and
// nothing else (the pane beside it is for one file, not a second listing); a file opens in a tab
// that shows it.
import { expect } from "@playwright/test";
import { test } from "./fixtures";
import { say } from "./steps";

test("the tree lists the session roots and browses into a file", async ({ page }) => {
  await page.goto("/");
  await say(page, "hello");
  await expect(page.getByText(/Echo: hello/)).toBeVisible();

  // The panel opens on its Start guide; Files is what the folders hold now.
  await page.getByTestId("rail-door-files").click();
  const tree = page.getByTestId("file-tree");
  await expect(tree).toBeVisible();
  const root = page.getByTestId("files-root-row").first();
  await expect(root).toContainText("scratch");
  // Only "Read only" and "Not found" are said of a folder; "Read & write" was on every folder,
  // which is the ordinary case (owner, 2026-10-13).
  await expect(root.getByTestId("files-root-access")).toHaveCount(0);

  await root.click();
  await expect(page.getByTestId("artifact-frame")).toHaveCount(0);
  await expect(tree).toBeVisible();
  const notes = page.locator(".tree-label", { hasText: "notes.md" });
  await expect(notes).toBeVisible();
  await notes.click();
  await expect(page.locator(".artifact-md")).toContainText("hello from the explorer");
});
