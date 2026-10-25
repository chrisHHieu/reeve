// The frameless desktop window's title row (owner, 2026-10-20: "nghiêng xéo không đẹp"). The
// panel was pushed 40px down to clear the 38px – □ ×, so its tab row sat 40px under the session
// header beside it. Now there is one row: the controls are its height and sit at the end of
// whichever bar is under them, and that bar keeps their width clear. Measured in a real layout,
// with the desktop shell simulated as composer-platform.spec.ts does.
import type { Page } from "@playwright/test";
import { expect } from "@playwright/test";
import { test } from "./fixtures";

type Box = { x: number; y: number; width: number; height: number };

async function boxOf(page: Page, selector: string): Promise<Box> {
  const box = await page.locator(selector).first().boundingBox();
  if (!box) throw new Error(`${selector} is not on screen`);
  return box;
}

/** Every control of the session header and the panel's tab row stays clear of the – □ ×. */
async function expectClearOfControls(page: Page) {
  const controls = await boxOf(page, ".window-controls");
  const buttons = page.locator(".main-topbar button, .rail-strip button, .rail-strip [role='tab']");
  for (let i = 0; i < (await buttons.count()); i++) {
    const box = await buttons.nth(i).boundingBox();
    if (!box || box.width === 0) continue;
    const overlaps = box.x < controls.x + controls.width && box.x + box.width > controls.x && box.y < controls.y + controls.height && box.y + box.height > controls.y;
    expect(overlaps, `button ${i} under the window controls`).toBe(false);
  }
}

async function openPanelTab(page: Page) {
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();
  await page.getByTestId("rail-new").click();
  await page.getByTestId("rail-new-browser").click();
  await expect(page.getByTestId("browser-view")).toBeVisible();
}

async function settle(page: Page) {
  await page.evaluate(() => Promise.all(document.getAnimations().map((a) => a.finished.catch(() => null))));
  await page.waitForTimeout(300);
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    (window as any).reeveDesktop = { invoke: async () => null, on: () => () => {} };
    (window as any).__REEVE_PLATFORM__ = "windows";
  });
});

test("beside the session, the panel's tab row is the title row: one line, one hairline", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await openPanelTab(page);
  await settle(page);
  const bar = await boxOf(page, ".main-topbar");
  const strip = await boxOf(page, ".rail-strip");
  const controls = await boxOf(page, ".window-controls");
  // The same row: the same top and the same bottom, so their hairlines are one line.
  expect(strip.y).toBe(bar.y);
  expect(strip.y + strip.height).toBeCloseTo(bar.y + bar.height, 0);
  // The controls are that row's height and sit at its end, centred on its line.
  expect(controls.y).toBe(0);
  expect(controls.height).toBeCloseTo(bar.height, 0);
  await expectClearOfControls(page);
  await page.locator(".app").screenshot({ path: test.info().outputPath("title-row-1440.png"), clip: { x: 0, y: 0, width: 1440, height: 140 } });
});

test("with the panel hidden, the controls sit at the session header's end, on its line", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await openPanelTab(page);
  await page.getByTestId("rail-hide").click();
  await settle(page);
  const title = await boxOf(page, ".main-title");
  const controls = await boxOf(page, ".window-controls");
  expect(Math.abs(controls.y + controls.height / 2 - (title.y + title.height / 2))).toBeLessThanOrEqual(1);
  await expectClearOfControls(page);
  await page.screenshot({ path: test.info().outputPath("title-row-hidden.png"), clip: { x: 0, y: 0, width: 1440, height: 100 } });
});

test("a narrow window: the panel opens under the header, and nothing sits under the controls", async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 760 });
  await openPanelTab(page);
  for (const size of [{ width: 1024, height: 760 }, { width: 900, height: 760 }]) {
    await page.setViewportSize(size);
    await settle(page);
    const rail = await boxOf(page, ".right-rail");
    const strip = page.locator(".rail-strip");
    const bar = await boxOf(page, ".main-topbar");
    if (bar.x + bar.width > rail.x) {
      // The header spans the window here: the panel opens under it, not under it AND over it
      // (the bare `.right-rail` rule used to win and put it at the top, header over its tabs),
      // and its tab row keeps no room for controls that are not over it.
      expect(rail.y).toBeCloseTo(bar.y + bar.height, 0);
      expect(await strip.evaluate((el) => getComputedStyle(el).paddingRight)).toBe("8px");
    } else {
      expect((await strip.boundingBox())!.y).toBe(bar.y);
    }
    await expectClearOfControls(page);
    await page.screenshot({ path: test.info().outputPath(`title-row-${size.width}.png`), clip: { x: 0, y: 0, width: size.width, height: 160 } });
  }
});

test("a full-screen panel keeps its tab row clear of the controls", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await openPanelTab(page);
  await page.getByTestId("rail-fullscreen").click();
  await settle(page);
  await expectClearOfControls(page);
  await page.screenshot({ path: test.info().outputPath("title-row-full.png"), clip: { x: 0, y: 0, width: 1440, height: 100 } });
});
