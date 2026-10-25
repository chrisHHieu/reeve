// The rail's Files tab is the file-manager workbench (the reference design's `pi.file-manager`
// anatomy, host-rendered): one pane with the project tree and a name search, one pane for
// the selected file. Review keeps its own door and now defaults to the transcript's
// recorded changes.
import { expect } from "@playwright/test";
import { test } from "./fixtures";

async function openRail(page: import("@playwright/test").Page) {
  // These pin the panel at its narrow width, where a file and the list take turns; the work
  // pane's own default is wider (layout step 3).
  await page.addInitScript(() => localStorage.setItem("reeve:rail-width:v2", "512"));
  await page.goto("/");
  await page.getByPlaceholder(/Ask anything/).fill("hello");
  await page.getByRole("button", { name: "Send" }).click();
}

test("Files tab: name search opens the file in the second pane", async ({ page }) => {
  await openRail(page);
  await page.getByTestId("rail-door-files").click();

  await expect(page.getByTestId("file-manager")).toBeVisible();
  await expect(page.getByTestId("rail-tab-files")).toBeVisible();
  // In a panel this narrow the list and an open file take turns, so there is no empty
  // pane beside the list: the list is the page.
  await expect(page.getByTestId("file-tree")).toBeVisible();

  await page.getByTestId("fm-search").fill("notes");
  await expect(page.getByTestId("fm-result-notes.md")).toBeVisible();
  await page.getByTestId("fm-result-notes.md").click();
  await expect(page.getByTestId("artifact-body")).toContainText("hello from the explorer");

  // A file takes the panel's width; "Files" on it goes back to the list, and clearing the
  // search goes back to the tree.
  await expect(page.getByTestId("fm-search")).toBeHidden();
  await expect(page.getByTestId("artifact-close")).toBeHidden();
  await page.getByTestId("fm-back").click();
  await page.getByTestId("fm-search-clear").click();
  await expect(page.getByTestId("file-tree")).toBeVisible();
});

test("Files tab: one toolbar row, and the view's options behind its menu", async ({ page }) => {
  await openRail(page);
  await page.getByTestId("rail-door-files").click();
  await expect(page.getByTestId("file-manager")).toBeVisible();
  await page.locator(".right-rail").screenshot({ path: test.info().outputPath("files-toolbar.png") });

  // Ignored entries show by default (owner, 2026-10-21); the menu says so and hides them.
  await page.getByTestId("fm-view").click();
  await expect(page.getByTestId("fm-ignored-toggle")).toHaveAttribute("aria-checked", "true");
  await page.getByTestId("fm-ignored-toggle").click();
  await page.getByTestId("fm-view").click();
  await expect(page.getByTestId("fm-ignored-toggle")).toHaveAttribute("aria-checked", "false");

  // Hiding the file list hands the whole pane to the preview.
  await page.getByTestId("fm-toggle-tree").click();
  await expect(page.locator(".fm-body.tree-hidden")).toHaveCount(1);
  await expect(page.getByTestId("fm-empty")).toBeVisible();
  await page.getByTestId("fm-view").click();
  await page.getByTestId("fm-toggle-tree").click();
  await expect(page.getByTestId("file-tree")).toBeVisible();

  // Refresh is wired.
  await page.getByTestId("fm-view").click();
  await page.getByTestId("fm-reload").click();
  await expect(page.getByTestId("file-manager")).toBeVisible();
});

test("Files tab: New is named in place; a row's own menu renames, moves and deletes it", async ({ page }) => {
  await openRail(page);
  await page.getByTestId("rail-door-files").click();
  await expect(page.getByTestId("file-manager")).toBeVisible();
  const root = "/Users/test/Reeve/launch-note";

  // New file: the name is typed in the tree where the file will live, then posted.
  await page.getByTestId("fm-new").click();
  await page.getByTestId("fm-new-file").click();
  await page.getByTestId("fm-new-name").fill("draft.md");
  await page.locator(".right-rail").screenshot({ path: test.info().outputPath("files-new-in-place.png") });
  const [created] = await Promise.all([
    page.waitForRequest((r) => r.url().endsWith("/files/create") && r.method() === "POST"),
    page.getByTestId("fm-new-name").press("Enter"),
  ]);
  expect(created.postDataJSON()).toEqual({ path: `${root}/draft.md`, kind: "file" });
  await expect(page.getByTestId("fm-new-row")).toHaveCount(0);
  // The new file opened and holds the panel; back to the list.
  await page.getByTestId("fm-back").click();


  // Rename from the row's menu, pre-filled with its name.
  await page.getByRole("button", { name: "Actions for notes.md" }).click();
  await page.locator(".right-rail").screenshot({ path: test.info().outputPath("files-row-menu.png") });
  await page.getByTestId("tree-menu-rename").click();
  await expect(page.getByTestId("fm-dialog-input")).toHaveValue("notes.md");
  await page.getByTestId("fm-dialog-input").fill("renamed.md");
  const [renamed] = await Promise.all([
    page.waitForRequest((r) => r.url().endsWith("/files/rename") && r.method() === "POST"),
    page.getByTestId("fm-dialog-confirm").click(),
  ]);
  expect(renamed.postDataJSON()).toEqual({ path: `${root}/notes.md`, name: "renamed.md" });

  // Move asks for a destination folder.
  await page.getByRole("button", { name: "Actions for notes.md" }).click();
  await page.getByTestId("tree-menu-move").click();
  await page.getByTestId("fm-dialog-input").fill("docs");
  const [moved] = await Promise.all([
    page.waitForRequest((r) => r.url().endsWith("/files/move") && r.method() === "POST"),
    page.getByTestId("fm-dialog-confirm").click(),
  ]);
  expect(moved.postDataJSON()).toEqual({ path: `${root}/notes.md`, to: "docs" });
  // A move reads the open folders again in place; it does not close them.
  await expect(page.getByRole("button", { name: "Collapse scratch" })).toBeVisible();

  // Delete asks first and says where the file goes.
  await page.getByRole("button", { name: "Actions for notes.md" }).click();
  await page.getByTestId("tree-menu-delete").click();
  await expect(page.getByTestId("fm-delete-dialog")).toContainText("Delete notes.md?");
  await expect(page.getByTestId("fm-delete-dialog")).toContainText(/goes to the (Recycle Bin|Trash)/);
  await page.locator(".right-rail").screenshot({ path: test.info().outputPath("files-delete.png") });
  const [deleted] = await Promise.all([
    page.waitForRequest((r) => r.url().endsWith("/files/delete") && r.method() === "POST"),
    page.getByTestId("fm-delete-confirm").click(),
  ]);
  expect(deleted.postDataJSON()).toEqual({ path: `${root}/notes.md` });
  await expect(page.getByTestId("fm-delete-dialog")).toHaveCount(0);
});

test("Files tab: Edit mode saves through the server", async ({ page }) => {
  await openRail(page);
  await page.getByTestId("rail-door-files").click();
  await page.getByTestId("fm-search").fill("notes");
  await page.getByTestId("fm-result-notes.md").click();

  // Markdown opens in Preview with PI's Edit|Preview toggle.
  await expect(page.getByTestId("fm-mode-preview")).toHaveClass(/on/);
  await expect(page.locator(".artifact-md")).toContainText("hello from the explorer");

  await page.getByTestId("fm-mode-edit").click();
  const editor = page.getByTestId("fm-editor");
  await expect(editor).toHaveValue(/hello from the explorer/);
  await editor.fill("# Edited");
  // What is on screen: the label carries a long and a short form and CSS shows one by
  // width, so textContent read "Unsaved changesUnsaved" (red since the pair was added).
  await expect(page.getByTestId("fm-file-state")).toHaveText(/^Unsaved( changes)?$/, {
    useInnerText: true,
  });

  const [req] = await Promise.all([
    page.waitForRequest((r) => r.url().endsWith("/files/write") && r.method() === "POST"),
    page.getByTestId("fm-save").click(),
  ]);
  expect(req.postDataJSON()).toMatchObject({ path: "notes.md", content: "# Edited" });

  await page.getByTestId("fm-mode-preview").click();
  await expect(page.locator(".artifact-md")).toBeVisible();
});

test("outside git, Review offers only Reeve's own work", async ({ page }) => {
  await openRail(page);
  await page.getByTestId("rail-door-review").click();

  await expect(page.getByTestId("review-pane")).toBeVisible();
  // The fixture's workspace is not a git repository: no commit button, no git scope, and
  // Reeve's own changes still listed (none in this transcript).
  await expect(page.getByTestId("rvw-scope")).toContainText("This session");
  await expect(page.getByTestId("rvw-commit")).toHaveCount(0);
  await expect(page.getByTestId("recorded-empty")).toContainText(
    "Reeve hasn't changed any files in this session",
  );
  await expect(page.getByTestId("review-scope-files")).toHaveCount(0);
  await page.getByTestId("rvw-scope").click();
  await expect(page.getByTestId("rvw-scope-uncommitted")).toHaveCount(0);
});

test("the scope menu names each scope in words, with what it holds", async ({ page }) => {
  // Review audit, V1: the old scope group read "RecordedChanged All" as one run of text.
  // Each scope is its own row with a label and a one-line hint now.
  await openRail(page);
  await page.getByTestId("rail-door-review").click();
  await page.getByTestId("rvw-scope").click();
  const item = page.getByTestId("rvw-scope-session");
  await expect(item).toContainText("This session");
  await expect(item).toContainText("every turn, with Undo");
  const box = (await item.boundingBox())!;
  expect(box.height).toBeGreaterThanOrEqual(36);
});

test("a code file has no Preview: one Edit button, an editor to go into and come out of", async ({ page }) => {
  await openRail(page);
  await page.getByTestId("rail-door-files").click();
  await page.getByTestId("fm-search").fill("totals");
  await page.getByTestId("fm-result-totals.py").click();

  await expect(page.getByTestId("artifact-body")).toContainText("def total(rows):");
  await expect(page.getByTestId("fm-mode-preview")).toHaveCount(0);
  await expect(page.getByTestId("fm-mode-edit")).toHaveCount(0);

  await page.getByTestId("fm-edit").click();
  const editor = page.getByTestId("fm-editor");
  await expect(editor).toHaveValue(/def total/);
  await editor.fill("def total(rows):\n    return 0\n");

  // Leaving with edits asks; Keep editing stays, Discard drops them.
  await page.getByTestId("fm-cancel").click();
  await expect(page.getByTestId("fm-leave-dialog")).toContainText("Save your changes to totals.py?");
  await page.getByTestId("fm-leave-keep").click();
  await expect(editor).toHaveValue(/return 0/);
  await page.getByTestId("fm-back").click();
  await page.getByTestId("fm-leave-discard").click();
  // Back at the list (still the search that found it), with the editor gone.
  await expect(page.getByTestId("fm-results")).toBeVisible();
  await expect(page.getByTestId("fm-editor")).toHaveCount(0);
});

test("the file's own menu is whole and on top: nothing clips it, every action can be pressed", async ({ page }) => {
  await openRail(page);
  await page.getByTestId("rail-door-files").click();
  await page.getByTestId("fm-search").fill("notes");
  await page.getByTestId("fm-result-notes.md").click();
  await page.getByTestId("artifact-more").click();

  const menu = page.getByTestId("artifact-menu");
  await expect(menu).toBeVisible();
  // The old menu was a child of the head, whose overflow cut it to a sliver (owner-hit,
  // 2026-10-11). What is on top at the middle of each action must be that action.
  for (const id of ["artifact-reload", "artifact-copy-path", "fv-rename", "fv-delete", "artifact-reveal"]) {
    const onTop = await page.getByTestId(id).evaluate((el) => {
      const box = el.getBoundingClientRect();
      const hit = document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2);
      return !!hit && (hit === el || el.contains(hit)) && box.bottom <= window.innerHeight && box.right <= window.innerWidth;
    });
    expect(onTop, id).toBe(true);
  }
  await page.screenshot({ path: test.info().outputPath("file-menu.png") });

  // Rename from here is the tree's own Rename dialog, for this file.
  await page.getByTestId("fv-rename").click();
  await expect(page.getByTestId("fm-dialog")).toContainText("Rename notes.md");
  await expect(page.getByTestId("artifact-menu")).toHaveCount(0);
});

test("a spreadsheet is drawn as its sheets: the file's look, frozen rows that stay, a bar for what a cell holds", async ({ page }) => {
  await openRail(page);
  await page.getByTestId("rail-door-files").click();
  await page.getByTestId("fm-search").fill("budget");
  await page.getByTestId("fm-result-budget.xlsx").click();

  // Read only: no Edit, no second view.
  const grid = page.getByTestId("sheet-grid");
  await expect(grid).toBeVisible();
  await expect(page.getByTestId("fm-edit")).toHaveCount(0);
  await expect(page.getByTestId("fm-mode-edit")).toHaveCount(0);
  // The workbook is read off the page's thread: a large one must not stop the app.
  expect(page.workers().some((worker) => /sheet\/worker/.test(worker.url()))).toBe(true);

  // The head row is drawn with the file's own fill and type, not as plain values.
  const head = page.locator('[data-cell="1:0"]');
  await expect(head).toHaveText("Item");
  await expect(head).toHaveCSS("background-color", "rgb(31, 78, 121)");
  await expect(head).toHaveCSS("color", "rgb(255, 255, 255)");
  await expect(page.locator('[data-cell="3:2"]')).toHaveCSS("color", "rgb(192, 0, 0)");
  // The merged title spans its three columns: it ends where column C does.
  const edges = await page.evaluate(() => {
    const right = (selector: string) => (document.querySelector(selector) as HTMLElement).getBoundingClientRect().right;
    return { title: right('[data-cell="0:0"]'), cost: right('[data-cell="1:2"]') };
  });
  expect(Math.abs(edges.title - edges.cost)).toBeLessThanOrEqual(1);

  // A cell's formula is in the bar; its result is in the cell.
  await page.locator('[data-cell="2:2"]').click();
  await expect(page.getByTestId("sheet-address")).toHaveText("C3");
  await expect(page.getByTestId("sheet-holds")).toHaveText("=B3*110");
  await expect(page.locator('[data-cell="2:2"]')).toHaveText("$26,400.00");
  // A number wider than its column is #### (as in Excel), never the half of it that fits;
  // the bar still says what it is.
  await expect(page.locator('[data-cell="2:3"]')).toHaveText(/^#+$/);
  await page.locator('[data-cell="2:3"]').click();
  await expect(page.getByTestId("sheet-holds")).toHaveText("123456789012");
  await page.locator('[data-cell="2:2"]').click();
  // The selection is drawn on the cell it names.
  const drawn = await page.evaluate(() => {
    const box = (selector: string) => (document.querySelector(selector) as HTMLElement).getBoundingClientRect();
    const cell = box('[data-cell="2:2"]');
    const selection = box('[data-testid="sheet-selection"]');
    return Math.abs(cell.left - selection.left) + Math.abs(cell.top - selection.top);
  });
  expect(drawn).toBeLessThanOrEqual(2);

  // The two frozen rows stay where they are while the rest scrolls under them, and the
  // column letters stay over their columns.
  await page.setViewportSize({ width: 1280, height: 420 });
  await page.getByTestId("sheet-zoom-in").click();
  await page.getByTestId("sheet-zoom-in").click();
  await page.getByTestId("sheet-zoom-in").click();
  await expect(page.getByTestId("sheet-zoom")).toHaveText("200%");
  const before = await head.boundingBox();
  await grid.evaluate((element) => {
    element.scrollTop = 60;
  });
  await expect.poll(() => grid.evaluate((element) => element.scrollTop)).toBeGreaterThan(0);
  const after = await head.boundingBox();
  expect(after?.y).toBe(before?.y);
  await page.getByTestId("sheet-zoom").click();
  await expect(page.getByTestId("sheet-zoom")).toHaveText("100%");

  // The other sheet: what is not drawn is said, with the way to open the file outside.
  await page.getByRole("tab", { name: "By month" }).click();
  await expect(page.getByTestId("sheet-note")).toContainText("1 chart, 1 picture and some conditional formatting, which are not drawn here");
  await expect(page.getByTestId("sheet-note").getByRole("button", { name: "Open in Excel" })).toBeVisible();
  await expect(page.locator('[data-cell="1:0"]')).toHaveText("January");
  await expect(page.getByTestId("sheet-tabs").getByRole("tab")).toHaveCount(3); // the hidden sheet has no tab

  // A table in Excel's default style (the file names it, Excel carries it), a rule of
  // conditional formatting that is met, a data bar, and a picture where it is anchored.
  await page.getByRole("tab", { name: "Orders" }).click();
  await expect(page.locator('[data-cell="0:0"]')).toHaveCSS("background-color", "rgb(68, 114, 196)");
  await expect(page.locator('[data-cell="0:0"]')).toHaveCSS("color", "rgb(255, 255, 255)");
  await expect(page.locator('[data-cell="3:2"]')).toHaveCSS("background-color", "rgb(255, 199, 206)");
  await expect(page.getByTestId("sheet-note")).toHaveCount(0);
  const sizes = await page.evaluate(() => {
    const box = (selector: string) => (document.querySelector(selector) as HTMLElement).getBoundingClientRect();
    const long = box('[data-cell="3:1"] .sheet-bar').width / box('[data-cell="3:1"]').width;
    const short = box('[data-cell="4:1"] .sheet-bar').width / box('[data-cell="4:1"]').width;
    const picture = box('[data-testid="sheet-picture"]');
    return { long, short, width: picture.width, height: picture.height };
  });
  expect(sizes.long).toBeGreaterThan(0.8);
  expect(sizes.short).toBeLessThan(0.2);
  expect(sizes.width).toBe(100);
  expect(sizes.height).toBe(50);
});
