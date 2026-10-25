// Designs in the panel: the Designs board lists the conversation's pages, a card opens
// the page in its own tab, and the tab's one bar drives the page: its width (menu and drag), its
// code, its variants, and a point that puts a part of it in the message. The page runs the real
// door's design bridge, as in design-tools.spec.ts.
import { expect } from "@playwright/test";
import { test } from "./fixtures";

async function openDesigns(page: import("@playwright/test").Page) {
  await page.goto("/");
  await page.getByPlaceholder(/Ask anything/).fill("hello");
  await page.getByRole("button", { name: "Send" }).click();
  await page.getByTestId("rail-door-designs").click();
  await expect(page.getByTestId("designs-view")).toBeVisible();
}

test("the Designs board shows every page, variants after their page, and a card opens the page's own tab", async ({ page }) => {
  await openDesigns(page);
  await expect(page.getByTestId("design-card-design/pricing.html")).toContainText("2 variants");
  await expect(page.getByTestId("design-card-design/pricing.a.html")).toContainText("Variant A of pricing.html");
  await expect(page.getByTestId("design-card-design/pricing.b.html")).toContainText("Variant B of pricing.html");
  await expect(page.getByTestId("design-card-design-md")).toContainText("3 colours");
  await expect(page.getByTestId("design-card-design/review.html")).toContainText("Locked");
  await expect(page.getByTestId("design-card-design/pricing.html")).not.toContainText("Locked");
  await page.locator(".right-rail").screenshot({ path: test.info().outputPath("designs-board.png") });
  await page.getByRole("button", { name: "Open design/pricing.html" }).click();
  await expect(page.getByTestId("rail-tab-design:design/pricing.html")).toHaveAttribute("aria-selected", "true");
  const inner = page.frameLocator('[data-testid="design-frame"]').frameLocator("iframe");
  await expect(inner.locator("h1")).toHaveText("Plans");
  await expect(page.getByTestId("design-foot")).toContainText("nothing to fix");
  await page.locator(".right-rail").screenshot({ path: test.info().outputPath("design-tab.png") });
});

test("Compare on a page's card opens the page beside its variants", async ({ page }) => {
  await openDesigns(page);
  await page.getByTestId("design-card-compare-design/pricing.html").click();
  await expect(page.getByTestId("page-variants")).toBeVisible();
  const now = page.getByTestId("page-variant-base").frameLocator("iframe").frameLocator("iframe");
  await expect(now.locator("h1")).toHaveText("Plans");
  const b = page.getByTestId("page-variant-b").frameLocator("iframe").frameLocator("iframe");
  await expect(b.locator("h1")).toHaveText("Table");
  await page.locator(".right-rail").screenshot({ path: test.info().outputPath("design-compare.png") });
});

test("a design's width is picked from the menu or dragged, and its code is one click away", async ({ page }) => {
  await openDesigns(page);
  await page.getByRole("button", { name: "Open design/pricing.html" }).click();
  const frame = page.getByTestId("design-frame");
  await expect(frame).toBeVisible();
  await expect(page.getByTestId("design-scale")).toHaveText("100%");
  await page.locator(".design-view .dd .pill").click();
  const menu = page.locator(".design-view .dd-menu");
  await expect(menu.getByRole("menuitemradio")).toHaveText(["Desktop", "Tablet", "Mobile"]);
  // The menu opens down from the bar, inside the panel, not up over the tabs.
  await menu.evaluate((el) => Promise.all(el.getAnimations().map((a) => a.finished)));
  const pill = (await page.locator(".design-view .dd .pill").boundingBox())!;
  expect((await menu.boundingBox())!.y).toBeGreaterThan(pill.y + pill.height);
  await page.locator(".right-rail").screenshot({ path: test.info().outputPath("design-width-menu.png") });
  await page.getByRole("menuitemradio", { name: "Mobile" }).click();
  await expect(page.getByTestId("design-foot")).toContainText("390 px");
  const handle = page.getByTestId("design-width-handle");
  // Desktop draws the page edge to edge; a set width puts the ground's margin back, and the stage
  // is measured again a frame later, so the handle settles before it is grabbed.
  let settled = (await handle.boundingBox())!;
  await expect
    .poll(async () => {
      await page.waitForTimeout(80);
      const now = (await handle.boundingBox())!;
      const same = now.x === settled.x && now.y === settled.y;
      settled = now;
      return same;
    })
    .toBe(true);
  const box = settled;
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width / 2 + 40, box.y + box.height / 2, { steps: 4 });
  await page.mouse.up();
  await expect(page.getByTestId("design-foot")).toContainText("470 px");
  await page.locator(".right-rail").screenshot({ path: test.info().outputPath("design-dragged.png") });
  await page.getByTestId("design-view-code").click();
  await expect(page.getByTestId("design-code")).toContainText("Start free.");
});

test("pointing at a part of the design puts it in the message, and the variants are drawn side by side", async ({ page }) => {
  await openDesigns(page);
  await page.getByRole("button", { name: "Open design/pricing.html" }).click();
  const inner = page.frameLocator('[data-testid="design-frame"]').frameLocator("iframe");
  await expect(inner.locator("h1")).toHaveText("Plans");
  await page.getByTestId("design-mode-point").click();
  await expect(page.getByTestId("design-mode-point")).toHaveAttribute("aria-pressed", "true");
  await inner.locator("h1").click();
  await expect(page.locator(".composer-wrap, .composer").getByText(/pricing\.html ·/)).toBeVisible();
  await page.getByTestId("design-variants-toggle").click();
  const b = page.getByTestId("page-variant-b").frameLocator("iframe").frameLocator("iframe");
  await expect(b.locator("h1")).toHaveText("Table");
});

test("a comment is typed where it was dropped, kept in the dock or put in the message", async ({ page }) => {
  await openDesigns(page);
  await page.getByRole("button", { name: "Open design/pricing.html" }).click();
  const inner = page.frameLocator('[data-testid="design-frame"]').frameLocator("iframe");
  await expect(inner.locator("h1")).toHaveText("Plans");
  await page.getByTestId("design-mode-pin").click();
  await inner.locator("h1").click();
  const box = page.getByTestId("design-comment-popover");
  await expect(box).toBeVisible();
  await page.getByTestId("design-comment-input").fill("Shorter, six words at most");
  await page.locator(".right-rail").screenshot({ path: test.info().outputPath("design-comment-box.png") });
  await page.getByTestId("design-comment-keep").click();
  await expect(page.getByTestId("design-comments-dock")).toBeVisible();
  await expect(page.getByTestId("design-comment-1")).toContainText("Plans");
  await inner.locator("p.note").click();
  await page.getByTestId("design-comment-input").fill("Say what the free plan includes");
  await page.getByTestId("design-comment-keep").click();
  await page.locator(".right-rail").screenshot({ path: test.info().outputPath("design-comments-dock.png") });
  await page.getByTestId("design-comments-send").click();
  await expect(page.locator(".composer-wrap, .composer").getByText(/pricing\.html · 2 comments/)).toBeVisible();
  await expect(page.getByTestId("design-comment-2")).toContainText("In your message");
});

test("a mark is drawn over the design and goes into the message as a picture and words", async ({ page }) => {
  await openDesigns(page);
  await page.getByRole("button", { name: "Open design/pricing.html" }).click();
  const inner = page.frameLocator('[data-testid="design-frame"]').frameLocator("iframe");
  await expect(inner.locator("h1")).toHaveText("Plans");
  await page.getByTestId("design-mode-mark").click();
  const layer = page.getByTestId("design-mark-layer");
  const at = (await layer.boundingBox())!;
  await page.mouse.move(at.x + 20, at.y + 30);
  await page.mouse.down();
  await page.mouse.move(at.x + 200, at.y + 110, { steps: 5 });
  await page.mouse.up();
  await page.getByTestId("design-mark-note").fill("Too much space around the title");
  await page.locator(".right-rail").screenshot({ path: test.info().outputPath("design-mark.png") });
  const shot = page.waitForRequest((request) => request.url().endsWith("/design/shot"));
  await page.getByTestId("design-mark-send").click();
  const body = (await shot).postDataJSON();
  expect(body.path).toBe("design/pricing.html");
  expect(body.area.w).toBeGreaterThan(150);
  await expect(page.locator(".composer-wrap, .composer").getByText(/pricing\.html · a mark/)).toBeVisible();
  await expect(page.getByRole("img", { name: "design/pricing.html · marked area" })).toBeVisible();
});

test("the camera puts a picture of what is seen into the message", async ({ page }) => {
  await openDesigns(page);
  await page.getByRole("button", { name: "Open design/pricing.html" }).click();
  const inner = page.frameLocator('[data-testid="design-frame"]').frameLocator("iframe");
  await expect(inner.locator("h1")).toHaveText("Plans");
  const shot = page.waitForRequest((request) => request.url().endsWith("/design/shot"));
  await page.getByTestId("design-shoot").click();
  const body = (await shot).postDataJSON();
  expect(body.path).toBe("design/pricing.html");
  expect(body.area.y).toBe(0);
  expect(body.area.w).toBe(body.width);
  expect(body.area.h).toBeGreaterThan(300);
  await expect(page.getByTestId("design-notice")).toContainText("A picture of what you see is in your message.");
  await expect(page.getByRole("img", { name: /design\/pricing\.html · \d+ px/ })).toBeVisible();
});

test("the tab row exports, shares, presents and opens the design, as OpenDesign's does", async ({ page, context }) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await openDesigns(page);
  await page.getByRole("button", { name: "Open design/pricing.html" }).click();
  const inner = page.frameLocator('[data-testid="design-frame"]').frameLocator("iframe");
  await expect(inner.locator("h1")).toHaveText("Plans");
  const actions = page.getByTestId("design-actions");
  await expect(actions).toBeVisible();
  // The actions sit in the tab row, beside the tabs.
  await expect(page.getByTestId("rail-tabs").getByTestId("design-actions")).toBeVisible();

  await page.getByTestId("design-export").click();
  await expect(page.getByTestId("design-export-menu").getByRole("menuitem")).toHaveText([
    "Export as PDF",
    "Export as image",
    "Download as .zip",
    "Export as standalone HTML",
  ]);
  await page.locator(".right-rail").screenshot({ path: test.info().outputPath("design-export-menu.png") });
  const exported = page.waitForRequest((request) => request.url().endsWith("/design/export"));
  await page.getByTestId("design-export-pdf").click();
  const body = (await exported).postDataJSON();
  expect(body).toMatchObject({ path: "design/pricing.html", kind: "pdf", deliver: "file", internet: false });
  expect(body.width).toBeGreaterThanOrEqual(320);
  await expect(page.getByTestId("design-outcome")).toContainText("Saved pricing.pdf to Downloads.");
  const reveal = page.waitForRequest((request) => request.url().endsWith("/downloads/reveal"));
  await page.getByTestId("design-outcome-reveal").click();
  expect((await reveal).postDataJSON().path).toBe("C:/Users/someone/Downloads/pricing.pdf");

  await page.getByTestId("design-share").click();
  await page.getByTestId("design-share-path").click();
  await expect(page.getByTestId("design-outcome")).toContainText("Copied the path of pricing.html.");
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe("design/pricing.html");

  await page.getByTestId("design-open").click();
  await expect(page.getByTestId("design-open-menu").getByRole("menuitem")).toHaveText(["Open in VS Code", "Show in folder"]);
  const opened = page.waitForRequest((request) => request.url().endsWith("/open-in"));
  await page.getByTestId("design-open-vscode").click();
  expect((await opened).postDataJSON()).toEqual({ path: "design/pricing.html", editor: "vscode" });

  await page.getByTestId("design-present").click();
  await expect(page.getByTestId("design-present-menu").getByRole("menuitem")).toHaveText(["In this tab", "Full screen", "In a browser window"]);
  await page.getByTestId("design-present-tab").click();
  await expect(page.getByTestId("design-present-exit")).toBeVisible();
  await expect(page.locator(".dv-bar")).toBeHidden();
  await page.locator(".right-rail").screenshot({ path: test.info().outputPath("design-presenting.png") });
  await page.keyboard.press("Escape");
  await expect(page.locator(".dv-bar")).toBeVisible();
});

test("Lock measures the design, says what the check still finds, and hands it off through the message box", async ({ page }) => {
  await openDesigns(page);
  await page.getByRole("button", { name: "Open design/pricing.html" }).click();
  const inner = page.frameLocator('[data-testid="design-frame"]').frameLocator("iframe");
  await expect(inner.locator("h1")).toHaveText("Plans");
  const button = page.getByTestId("design-lock");
  await expect(button).toHaveText("Lock");

  const measured = page.waitForRequest((request) => request.url().endsWith("/design/lock") && request.method() === "POST");
  await button.click();
  expect((await measured).postDataJSON()).toEqual({ path: "design/pricing.html", preview: true });
  const dialog = page.getByTestId("design-lock-dialog");
  await expect(dialog.getByRole("heading")).toHaveText("Lock pricing.html");
  const summary = page.getByTestId("design-lock-summary");
  await expect(summary).toContainText("6 colors, 2 not in DESIGN.md");
  await expect(summary).toContainText("Newsreader, Be Vietnam Pro · 5 sizes");
  await expect(summary).toContainText("4 · 8 · 16 · 24 · 40 · 64");
  await expect(summary).toContainText("5: header, Plans, Compare, Questions, footer");
  await expect(summary).toContainText("4 controls, 1 open something");
  await expect(page.getByTestId("design-lock-app")).toContainText(
    "React + Vite + Tailwind · theme in tailwind.config.ts · 2 colors and 1 font new to it · 20 components",
  );
  await expect(page.getByTestId("design-lock-accepted")).toContainText("The check still finds 1 thing");
  await expect(page.getByTestId("design-lock-target")).toContainText("Built into northwind-web");
  await expect(page.getByTestId("design-lock-confirm")).toHaveText("Lock and hand off");
  // The panel opens under the button, inside the window, and moves in.
  await dialog.evaluate((el) => Promise.all(el.getAnimations().map((a) => a.finished)));
  const box = (await dialog.boundingBox())!;
  const anchor = (await button.boundingBox())!;
  const view = page.viewportSize()!;
  expect(box.y).toBeGreaterThan(anchor.y + anchor.height);
  expect(box.x).toBeGreaterThanOrEqual(0);
  expect(box.x + box.width).toBeLessThanOrEqual(view.width);
  await page.locator(".right-rail").screenshot({ path: test.info().outputPath("design-lock.png") });

  const locked = page.waitForRequest((request) => request.url().endsWith("/design/lock") && request.method() === "POST");
  await page.getByTestId("design-lock-confirm").click();
  expect((await locked).postDataJSON()).toEqual({ path: "design/pricing.html", preview: false });
  // It leaves as it came, then is gone; the request waits in the message box, not sent.
  await expect(dialog).toHaveClass(/is-closing/);
  await expect(dialog).toHaveCount(0);
  await expect(button).toHaveText("Locked");
  await expect(page.locator(".composer-wrap, .composer").getByText("pricing.html · handoff")).toBeVisible();
  await expect(page.getByPlaceholder(/Ask anything|Reply/)).toHaveValue("Build pricing.html into northwind-web.");
});

test("the app is reviewed beside its locked design: two pictures, what differs, fix or keep, and done", async ({ page }) => {
  await openDesigns(page);
  const first = page.waitForRequest((request) => request.url().endsWith("/design/review"));
  // A compare_design row's "Review beside the design" asks the panel for this.
  await page.evaluate(() =>
    window.dispatchEvent(new CustomEvent("reeve:open-design-app", { detail: { path: "design/pricing.html", url: "http://localhost:5173/pricing" } })),
  );
  expect((await first).postDataJSON()).toEqual({ path: "design/pricing.html", url: "http://localhost:5173/pricing", width: 1440, compare: true });
  await expect(page.getByTestId("rail-tab-design-app:design/pricing.html")).toHaveAttribute("aria-selected", "true");
  await expect(page.getByTestId("rail-tab-design-app:design/pricing.html")).toContainText("pricing.html · app");
  const review = page.getByTestId("design-review");
  await expect(review.getByTestId("design-review-side").locator("figcaption")).toHaveText([
    "Locked design · pricing.html",
    "App · localhost:5173/pricing",
  ]);
  const list = page.getByTestId("design-review-list");
  await expect(list.getByRole("heading")).toHaveText("2 to fix, 1 to check");
  await expect(list.getByTestId("design-review-item")).toHaveCount(3);
  await page.locator(".right-rail").screenshot({ path: test.info().outputPath("design-review.png") });

  // Another width changes the pictures only.
  const picture = page.waitForRequest((request) => request.url().endsWith("/design/review"));
  await review.getByRole("button", { name: "Mobile" }).click();
  expect((await picture).postDataJSON()).toMatchObject({ width: 390, compare: false });
  await expect(list.getByTestId("design-review-item")).toHaveCount(3);

  // Overlay: the app over the design, as much as the slider says.
  await review.getByRole("button", { name: "Overlay" }).click();
  await expect(review.getByTestId("design-review-over")).toBeVisible();
  await review.getByRole("slider").fill("80");
  await expect(review.locator(".dr-top")).toHaveCSS("opacity", "0.8");

  // Ask to fix puts it in the message box; keep records it, and it is said as kept.
  const items = list.getByTestId("design-review-item");
  await items.nth(0).getByRole("button", { name: "Ask to fix" }).click();
  await expect(page.locator(".composer-wrap, .composer").getByText("pricing.html · difference")).toBeVisible();
  const kept = page.waitForRequest((request) => request.url().endsWith("/design/review/keep"));
  await items.nth(1).getByRole("button", { name: "Keep as the app has it" }).click();
  expect((await kept).postDataJSON()).toMatchObject({ path: "design/pricing.html", id: "colors-unused:ab12" });
  await expect(items.nth(1)).toContainText("Kept");
  await expect(list.getByRole("heading")).toHaveText("1 to fix, 1 to check");

  const done = page.waitForRequest((request) => request.url().endsWith("/design/review/done"));
  await page.getByTestId("design-review-done").click();
  expect((await done).postDataJSON()).toEqual({ path: "design/pricing.html", url: "http://localhost:5173/pricing", issues: 1 });
  await expect(list).toContainText("with 1 still to fix");
});
