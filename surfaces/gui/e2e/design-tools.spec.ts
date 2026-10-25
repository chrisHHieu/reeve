// Talking about a design by touching it (phase 3): point at a part and it is in the
// message; retype a piece of text and the file is saved with it; tweaks; variants; versions. The
// tools live in a design's own tab: Files shows any other page as a page and nothing more (owner,
// 2026-10-20: a flow drawn as HTML came with all of them). The page runs the real door's design
// bridge (read from its source by the fixture), so these drive the bridge and the app together.
// Comments are design-workspace.spec.ts's.
import { expect } from "@playwright/test";
import { test } from "./fixtures";

async function openReview(page: import("@playwright/test").Page) {
  await page.goto("/");
  await page.getByPlaceholder(/Ask anything/).fill("hello");
  await page.getByRole("button", { name: "Send" }).click();
  await page.getByTestId("rail-door-designs").click();
  await page.getByRole("button", { name: "Open design/review.html" }).click();
  await expect(page.getByTestId("rail-tab-design:design/review.html")).toHaveAttribute("aria-selected", "true");
  const inner = page.frameLocator('[data-testid="design-frame"]').frameLocator("iframe");
  await expect(inner.locator("#probe")).toContainText("script ran");
  return inner;
}

const composerChips = (page: import("@playwright/test").Page) => page.locator(".composer-wrap, .composer").getByText(/review\.html ·/);

test("a page in Files is a page: no design tools, and a design page there has a way to its tab", async ({ page }) => {
  await page.goto("/");
  await page.getByPlaceholder(/Ask anything/).fill("hello");
  await page.getByRole("button", { name: "Send" }).click();
  await page.getByTestId("rail-door-files").click();
  await page.getByTestId("files-root-row").first().click();
  await page.locator(".tree-label", { hasText: "reports" }).click();
  await page.locator(".tree-label", { hasText: "security-review.html" }).click();
  const inner = page.frameLocator('[data-testid="artifact-frame"]').frameLocator("iframe");
  await expect(inner.locator("#probe")).toContainText("script ran");
  await expect(page.getByTestId("page-frame")).toBeVisible();
  await expect(page.getByTestId("page-design")).toHaveCount(0);
  await expect(page.getByTestId("page-mode-point")).toHaveCount(0);
  await expect(page.getByTestId("page-open-design")).toHaveCount(0);
  await page.locator(".right-rail").screenshot({ path: test.info().outputPath("files-page.png") });
});

test("pointing at a part of the design puts it in the message, and the page's own link is not followed", async ({ page }) => {
  const inner = await openReview(page);
  await page.getByTestId("design-mode-point").click();
  await expect(page.getByTestId("design-mode-point")).toHaveAttribute("aria-pressed", "true");
  await page.getByPlaceholder(/Ask anything|Reply|Message/).first().fill("make this bigger");
  await inner.locator("h1").click();
  await expect(composerChips(page).first()).toContainText("“Security review” h1");
  // What the person was typing stays.
  await expect(page.getByPlaceholder(/Ask anything|Reply|Message/).first()).toHaveValue("make this bigger");
  // A link pointed at is named, not followed.
  await inner.locator("#next").click();
  await expect(inner.locator("h1")).toHaveText("Security review");
  // Pressed again, the tool lets go: the page is used as it is.
  await page.getByTestId("design-mode-point").click();
  await expect(page.getByTestId("design-mode-point")).toHaveAttribute("aria-pressed", "false");
  await inner.locator("#next").click();
  await expect(inner.locator("h1")).toHaveText("Details");
});

test("retyped text is saved into the file where it is, and the agent is told", async ({ page }) => {
  const inner = await openReview(page);
  await page.getByTestId("design-mode-text").click();
  const write = page.waitForRequest((request) => request.url().includes("/files/write") && request.method() === "POST");
  await inner.locator("h1").click();
  await inner.locator("h1").press("Control+a");
  await inner.locator("h1").pressSequentially("Security audit");
  await inner.locator("h1").press("Enter");
  const body = (await write).postDataJSON();
  expect(body.content).toContain("<h1>Security audit</h1>");
  expect(body.content).not.toContain("Security review");
  await expect(composerChips(page).first()).toContainText("text retyped");
});

test("tweaks the page declares change it at once, survive a reload, and go to the agent to keep", async ({ page }) => {
  const inner = await openReview(page);
  await page.getByTestId("design-tweaks-toggle").click();
  await expect(page.getByTestId("page-tweaks")).toBeVisible();
  await expect(inner.locator("h1")).toHaveCSS("color", "rgb(116, 53, 140)");
  await page.getByTestId("page-tweak-accent-#0d6a8a").click();
  await expect(inner.locator("h1")).toHaveCSS("color", "rgb(13, 106, 138)");
  await page.getByTestId("page-tweak-density-compact").click();
  await expect(inner.locator("html")).toHaveAttribute("data-density", "compact");
  // A reload of the page keeps what the person chose.
  await page.getByTestId("design-reload").click();
  await expect(inner.locator("#probe")).toContainText("script ran");
  await expect(inner.locator("h1")).toHaveCSS("color", "rgb(13, 106, 138)");
  await page.getByTestId("page-tweaks-keep").click();
  await expect(composerChips(page).first()).toContainText("tweaks");
  await page.locator(".right-rail").screenshot({ path: test.info().outputPath("design-tweaks.png") });
});

test("a design's variants are drawn side by side, and keeping one asks the agent", async ({ page }) => {
  await page.goto("/");
  await page.getByPlaceholder(/Ask anything/).fill("hello");
  await page.getByRole("button", { name: "Send" }).click();
  await page.getByTestId("rail-door-designs").click();
  await page.getByRole("button", { name: "Open design/pricing.html" }).click();
  await page.getByTestId("design-variants-toggle").click();
  await expect(page.getByTestId("page-variants")).toBeVisible();
  const b = page.getByTestId("page-variant-b").frameLocator("iframe").frameLocator("iframe");
  await expect(b.locator("h1")).toHaveText("Table");
  const a = page.getByTestId("page-variant-a").frameLocator("iframe").frameLocator("iframe");
  await expect(a.locator("h1")).toHaveText("Cards");
  await page.locator(".right-rail").screenshot({ path: test.info().outputPath("design-variants.png") });
  await page.getByTestId("page-variant-keep-b").click();
  await expect(page.getByPlaceholder(/Ask anything|Reply|Message/).first()).toHaveValue(
    "Keep pricing.b.html as pricing.html: make pricing.html what pricing.b.html is, and delete the other variants (pricing.a.html).",
  );
  await expect(page.getByTestId("page-variants")).toHaveCount(0);
});

test("a design's versions are listed with what each write changed, and two are drawn side by side", async ({ page }) => {
  await openReview(page);
  await page.getByTestId("design-versions-toggle").click();
  await expect(page.getByTestId("page-versions")).toBeVisible();
  await page.getByTestId("page-version-1").click();
  await page.getByTestId("page-version-2").click();
  await expect(page.getByTestId("page-versions-compare")).toContainText("v1 → v2");
  await expect(page.getByTestId("page-versions-compare")).toContainText("Privacy");
  await page.locator(".right-rail").screenshot({ path: test.info().outputPath("design-versions.png") });
});
