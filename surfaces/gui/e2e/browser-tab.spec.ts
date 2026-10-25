// The Browser tab says what it knows before it frames a page (workspace panel redesign,
// 2026-10-11): a mistyped address is said under the box, a local address that answers is framed,
// a site that refuses frames is named with a way to open it outside.
import { expect } from "@playwright/test";
import { test } from "./fixtures";

async function openBrowserTab(page: import("@playwright/test").Page) {
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();
  await page.getByTestId("rail-new").click();
  await page.getByTestId("rail-new-browser").click();
  await expect(page.getByTestId("browser-view")).toBeVisible();
}

test("a mistyped address is said under the box; a local one that answers is framed", async ({ page }) => {
  await openBrowserTab(page);
  await expect(page.getByTestId("browser-start")).toContainText("Type an address above");

  const address = page.getByTestId("browser-address");
  await address.fill("not a url ::");
  await address.press("Enter");
  await expect(page.getByTestId("browser-error")).toContainText("“not a url ::” is not a web address");
  await page.locator(".right-rail").screenshot({ path: test.info().outputPath("browser-bad-address.png") });

  // The app's own dev server is a local address that answers.
  const origin = new URL(page.url()).origin;
  await address.fill(origin.replace(/^http:\/\//, ""));
  await address.press("Enter");
  await expect(page.getByTestId("browser-error")).toHaveCount(0);
  await expect(page.getByTestId("browser-frame")).toHaveAttribute("src", origin);
});

test("a site that refuses frames is named, with a way to open it outside", async ({ page }) => {
  await page.route(/\/v1\/browser\/frame-check/, (route) =>
    route.fulfill({ json: { ok: true, frameable: false, reason: "x-frame-options" } }),
  );
  await openBrowserTab(page);
  const address = page.getByTestId("browser-address");
  await address.fill("github.com/ibelick/ui-skills");
  await address.press("Enter");
  await expect(page.getByTestId("browser-blocked")).toContainText("github.com won’t open inside Reeve");
  await expect(page.getByTestId("browser-frame")).toHaveCount(0);
  await page.locator(".right-rail").screenshot({ path: test.info().outputPath("browser-blocked.png") });
});

test("the bar reads the page through Reeve's browser: a picture and its look go into the message", async ({ page }) => {
  await openBrowserTab(page);
  const origin = new URL(page.url()).origin;
  const address = page.getByTestId("browser-address");
  await address.fill(origin.replace(/^http:\/\//, ""));
  await address.press("Enter");
  await expect(page.getByTestId("browser-frame")).toHaveAttribute("src", origin);
  const shot = page.waitForRequest((request) => request.url().endsWith("/v1/browser/capture"));
  await page.getByTestId("browser-shoot").click();
  expect((await shot).postDataJSON()).toEqual({ url: origin, kind: "view", width: 1280 });
  await expect(page.getByTestId("browser-outcome")).toContainText("is in your message.");
  await page.getByTestId("browser-brief").click();
  await expect(page.locator(".composer-wrap, .composer").getByText(/its look/)).toBeVisible();
  await page.getByTestId("browser-more").click();
  await expect(page.getByTestId("browser-menu").getByRole("menuitem")).toHaveText([
    "Copy a picture of the page",
    "Reload",
    "Copy address",
    "Open in your browser",
    "Save the page's HTML",
    "Put its look in your message",
    "Ask Reeve about this page",
    "Clear this tab's history",
    "Run without the sandbox…",
  ]);
  await page.locator(".right-rail").screenshot({ path: test.info().outputPath("browser-menu.png") });
  await page.getByTestId("browser-save").click();
  await expect(page.getByTestId("browser-outcome")).toContainText("Saved example.com.html to Downloads.");
});

test("the open tab's pill grows with its name, close button inside", async ({ page }) => {
  await openBrowserTab(page);
  const chip = page.locator(".rail-chip.on");
  const pill = page.locator(".rail-rule");
  // A browser tab opens on its host, then its page names itself (the desktop shell reports the
  // title): the chip grows with nothing else in the strip changing. Stood in for here by writing
  // the longer name into the chip.
  await chip.locator(".rail-chip-name").evaluate((el) => (el.textContent = "Example account settings"));
  await expect
    .poll(async () => {
      const [c, p] = [(await chip.boundingBox())!, (await pill.boundingBox())!];
      return Math.abs(c.x - p.x) < 1 && Math.abs(c.width - p.width) < 1;
    })
    .toBe(true);
  const close = (await chip.locator(".rail-chip-close").boundingBox())!;
  const box = (await pill.boundingBox())!;
  expect(close.x + close.width).toBeLessThanOrEqual(box.x + box.width + 0.5);
  await page.locator(".rail-strip").screenshot({ path: test.info().outputPath("rail-pill-grown.png") });
});
