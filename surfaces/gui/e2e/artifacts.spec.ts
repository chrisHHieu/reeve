// An HTML file the agent wrote is run as the page it is: served by the preview door
// from its own folder, at an address that is not the app's. Its script, its stylesheet, its
// storage and its links work; the app's window and the internet stay out of reach until the
// person turns the internet on for that page. The fixture page probes each of these and
// reports into #probe. (It replaced a sealed `srcdoc` box, in which none of a
// page's own files loaded.)
//
// The report is reached through the Files tab: a tree over the session's folders + a preview.
import { expect, type Page } from "@playwright/test";
import { test } from "./fixtures";
import { say } from "./steps";

/** After a turn, the security review opened from the Files tab: the session's folder, then
 *  reports/, then the file. A folder only opens in the tree; the file opens in the preview. */
async function openReport(page: Page) {
  await page.goto("/");
  await say(page, "hello");
  await page.getByTestId("rail-door-files").click();
  await page.getByTestId("files-root-row").first().click();
  for (const label of ["reports", "security-review.html"]) await page.locator(".tree-label", { hasText: label }).click();
}

const viewer = (page: Page) => page.getByTestId("artifact-frame");

test("an HTML file runs as a page: its own files work, the app and the internet stay out of reach", async ({
  page,
}) => {
  await openReport(page);
  const frame = page.getByTestId("artifact-frame");
  await expect(frame).toBeVisible();
  // The page may run, keep its state and send its forms. It is never given leave to move the
  // app's window (allow-top-navigation) or to open one of its own (allow-popups).
  await expect(frame).toHaveAttribute("sandbox", "allow-scripts allow-same-origin allow-forms allow-modals");

  const inner = page.frameLocator('[data-testid="artifact-frame"]').frameLocator("iframe");
  const probe = inner.locator("#probe");
  await expect(probe).toContainText("script ran");
  await expect(probe).toContainText("stylesheet loaded"); // a file beside the page
  await expect(probe).toContainText("storage kept"); // a real address, so it has storage
  await expect(probe).toContainText("parent blocked"); // not the app's address
  await expect(probe).toContainText("network blocked"); // the internet is off
  await expect(page).not.toHaveTitle("ESCAPED");
  await expect(page.getByTestId("page-net")).toHaveText("Internet off · 1 blocked");

  // A link to the next page of the same folder works, and the address line follows it.
  await inner.locator("#next").click();
  await expect(inner.locator("h1")).toHaveText("Details");
  await expect(page.getByTestId("page-where")).toHaveText("reports/details.html");
  await page.locator(".right-rail").screenshot({ path: test.info().outputPath("page-frame.png") });
});

test("a page is seen at a phone's, a tablet's or a desktop's width, scaled to fit the panel", async ({
  page,
}) => {
  await openReport(page);
  const frame = page.getByTestId("artifact-frame");
  await expect(frame).toBeVisible();
  await expect(page.getByTestId("page-width-fit")).toHaveAttribute("aria-pressed", "true");

  await page.getByTestId("page-width-390").click();
  await expect(page.getByTestId("page-width-390")).toHaveAttribute("aria-pressed", "true");
  await expect(frame).toHaveCSS("width", "390px");
  // The page inside lays itself out at 390 px, not at the panel's width.
  const inner = page.frameLocator('[data-testid="artifact-frame"]').frameLocator("iframe");
  await expect.poll(() => inner.locator("body").evaluate(() => document.documentElement.clientWidth)).toBe(390);
  await page.locator(".right-rail").screenshot({ path: test.info().outputPath("page-phone.png") });

  // Wider than the panel: drawn at 1440 and scaled down, and the scale is said.
  await page.getByTestId("page-width-1440").click();
  await expect(frame).toHaveCSS("width", "1440px");
  await expect(page.getByTestId("page-scale")).toHaveText(/^\d+%$/);
  await expect.poll(() => inner.locator("body").evaluate(() => document.documentElement.clientWidth)).toBe(1440);
  await page.locator(".right-rail").screenshot({ path: test.info().outputPath("page-desktop.png") });

  await page.getByTestId("page-width-fit").click();
  await expect(page.getByTestId("page-scale")).toHaveCount(0);
});

test("the internet is turned on for a page only by the person, who is told what it tried to reach", async ({
  page,
}) => {
  await openReport(page);
  const inner = page.frameLocator('[data-testid="artifact-frame"]').frameLocator("iframe");
  await expect(inner.locator("#probe")).toContainText("network blocked");

  await page.getByTestId("page-net-allow").click();
  const ask = page.getByTestId("page-net-dialog");
  await expect(ask).toContainText("Let this page use the internet?");
  await expect(ask).toContainText("security-review.html tried to reach evil.example.");
  await page.locator(".right-rail").screenshot({ path: test.info().outputPath("page-ask.png") });
  await page.getByTestId("page-net-confirm").click();

  await expect(page.getByTestId("page-net")).toHaveText("Internet on for this page");
  await expect(inner.locator("#probe")).toContainText("network reached");
  // Still not the app's address: the internet is all that changed.
  await expect(inner.locator("#probe")).toContainText("parent blocked");

  await page.getByTestId("page-net-off").click();
  await expect(page.getByTestId("page-net")).toContainText("Internet off");
  await expect(inner.locator("#probe")).toContainText("network blocked");
});

// The way out to the person's own browser, in the file's ⋯ menu with the two copies.
test("an HTML file offers Open in browser, the way out to the person's own browser", async ({ page }) => {
  await openReport(page);
  await page.getByTestId("artifact-more").click();
  for (const item of ["artifact-open-browser", "artifact-copy-contents", "artifact-copy-path"]) await expect(page.getByTestId(item)).toBeVisible();
});

// Seen by the owner on 2026-08-15: a mention sends one event, and the panel only listened for it
// while shown, so the first click showed an empty panel and lost the file; the viewer came on a
// later click.
test("a transcript mention opens the viewer on the FIRST click even with the rail hidden", async ({ page }) => {
  await page.goto("/");
  await say(page, "show the report");
  await page.getByRole("button", { name: "Hide side panel" }).click();
  await page.getByTestId("artifact-mention").click();
  await expect(viewer(page)).toBeVisible();
});

test("a turn that declared files with present stacks delivery rows under its answer", async ({
  page,
}) => {
  // The reference design shape (2026-10-03): the tool's own card carries the model's description, the row
  // is built from it, and the backticked name in the prose resolves to the same file — so
  // both halves of the tail agree because they read ONE declaration, not two guesses.
  await page.goto("/");
  await page.getByPlaceholder(/Ask anything/).fill("deliver the report");
  await page.getByRole("button", { name: "Send" }).click();

  const rows = page.getByTestId("delivered-file-card");
  await expect(rows).toHaveCount(2);
  await expect(rows.first()).toContainText("security-review.html");
  await expect(rows.first()).toContainText("Báo cáo an ninh, bản đầy đủ");
  // A file the model declared without a line of its own falls back to its kind.
  await expect(rows.nth(1)).toContainText("q3.xlsx");
  await expect(rows.nth(1)).toContainText("Excel");

  // The sentence's mention is the same file, and it opens the viewer on the first click.
  await page.getByTitle("Open reports/security-review.html").first().click();
  await expect(page.getByTestId("artifact-frame")).toBeVisible();
  // The tree shows where the file sits: its folder opened, its row marked (owner, 2026-10-21: the
  // card opened the file and the tree stayed closed). A narrow panel shows the list and the file
  // in turn, so here the row is marked behind the file.
  await expect(
    page.locator('.tree-row.on [data-tree-path="/Users/test/Reeve/launch-note/reports/security-review.html"]'),
  ).toHaveCount(1);

  // The row itself previews; the chevron menu is where the native actions live.
  await page.getByTestId("delivered-file-menu").first().click();
  const menu = page.getByTestId("delivered-file-actions");
  await expect(menu.getByRole("button", { name: /Open in default app/ })).toBeVisible();
  await expect(menu.getByRole("button", { name: /Show in file manager/ })).toBeVisible();
  await expect(menu.getByTestId("copy-button")).toBeVisible();
});

test("a file card opens the tree beside the file to where it sits", async ({ page }) => {
  // A wide window and panel: the list and the open file side by side, as the owner saw it.
  await page.setViewportSize({ width: 1920, height: 1000 });
  await page.addInitScript(() => localStorage.setItem("reeve:rail-width:v2", "1000"));
  await page.goto("/");
  await page.getByPlaceholder(/Ask anything/).fill("deliver the report");
  await page.getByRole("button", { name: "Send" }).click();
  await page.getByTestId("delivered-file-card").first().click();
  await expect(page.getByTestId("artifact-frame")).toBeVisible();
  const row = page.locator('[data-tree-path="/Users/test/Reeve/launch-note/reports/security-review.html"]');
  await expect(row).toBeVisible();
  await expect(row.locator("xpath=..")).toHaveClass(/(^|\s)on(\s|$)/);
  await expect(page.locator('[data-tree-path="/Users/test/Reeve/launch-note/notes.md"]')).toBeVisible();
});

// Seen by the owner on 2026-08-21: opening the preview folds the queue once, to make room, but
// opening the queue again folded it straight back, as the fold was replayed whenever a callback
// changed. What the person chose wins.
test("Show sidebar sticks while the file preview is open", async ({ page }) => {
  await openReport(page);
  const dock = page.getByTestId("rail-dock");
  await expect(dock).toBeVisible();
  await dock.click();
  // Time for a regression to fold it again.
  await page.waitForTimeout(400);
  await expect(page.locator(".app")).not.toHaveClass(/nav-collapsed/);
  // The rail is the frame and stays; the queue beside it is what opened, and the preview is
  // still there: opening the queue goes somewhere, it does not close anything.
  await expect(page.getByTestId("work-queue")).toBeVisible();
  await expect(viewer(page)).toBeVisible();
});

// "Files" on the file's header closes it and goes back to the list.
test("the file's way back closes the preview", async ({ page }) => {
  await openReport(page);
  await expect(viewer(page)).toBeVisible();
  await page.getByTestId("fm-back").click();
  await expect(viewer(page)).toHaveCount(0);
  await expect(page.getByTestId("file-tree")).toBeVisible();
});


