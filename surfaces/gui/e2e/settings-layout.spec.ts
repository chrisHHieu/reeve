import type { Page } from "@playwright/test";
import { test, expect } from "./fixtures";

// Settings must not break at any window size (2026-10-10 redesign): every page, at a wide window, a
// laptop split, a narrow window and a phone, in light and dark, keeps every element inside the
// content pane and never scrolls sideways. Rows answer to the pane's own width (container query),
// so a control drops under its words instead of pushing the row wider.
const WIDTHS = [1366, 900, 640, 400];
const THEMES = ["light", "dark"] as const;
// A provider's own page under Models, in each of its shapes: a key with a model checklist
// (connected), a key with a read-only preview and an endpoint, and a sign-in (subscription).
const PROVIDERS = ["anthropic", "zai", "ark", "openai-codex"];

// What sits outside the content pane, by class (empty when the page fits).
async function overflowIn(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const pane = document.querySelector<HTMLElement>(".settings-content");
    if (!pane) return ["no pane"];
    const box = pane.getBoundingClientRect();
    const out: string[] = [];
    if (pane.scrollWidth > pane.clientWidth + 1) out.push(`pane scrolls sideways (${pane.scrollWidth} > ${pane.clientWidth})`);
    for (const el of pane.querySelectorAll<HTMLElement>("*")) {
      const r = el.getBoundingClientRect();
      if (!r.width || !r.height) continue;
      // Inside a horizontally scrolling strip (a segmented control at its limit) is allowed.
      if (el.closest(".settings-segment") && el.closest(".settings-segment") !== el) continue;
      if (r.right > box.right + 1 || r.left < box.left - 1) out.push(el.className || el.tagName);
    }
    return [...new Set(out)].slice(0, 5);
  });
}

// Every page is one column centred in the pane at one width (owner, 2026-10-20): a column held
// to the rail left the spare width on one side, and two widths made its edge jump between pages.
test("Settings centres one column at one width on every page", async ({ page }) => {
  await page.setViewportSize({ width: 1600, height: 900 });
  await page.goto("/");
  await expect(page.getByTestId("session-surface")).toBeVisible();
  // The frame is drawn while Reeve starts (boot screen redesign): wait for the start to end.
  await expect(page.getByTestId("boot-pane")).toHaveCount(0);
  await page.keyboard.press("Control+,");
  await expect(page.getByTestId("settings-shell")).toBeVisible();
  const widths = new Set<number>();
  for (const label of ["General", "Appearance", "Models", "Connectors", "Web search"]) {
    await page.locator(".settings-rail-item", { hasText: label }).first().click();
    await expect(page.locator(".settings-section-title")).toHaveText(label);
    const gaps = await page.evaluate(() => {
      const pane = document.querySelector<HTMLElement>(".settings-content")!;
      const col = pane.querySelector<HTMLElement>(".settings-content-inner")!;
      const p = pane.getBoundingClientRect();
      const c = col.getBoundingClientRect();
      return { left: c.left - p.left, right: p.right - c.right, width: Math.round(c.width) };
    });
    expect(Math.abs(gaps.left - gaps.right), `${label}: ${JSON.stringify(gaps)}`).toBeLessThanOrEqual(16);
    widths.add(gaps.width);
  }
  expect([...widths]).toHaveLength(1);
});

for (const theme of THEMES) {
  for (const width of WIDTHS) {
    test(`Settings keeps every page inside the window at ${width}px, ${theme}`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      await page.addInitScript((t) => localStorage.setItem("reeve:theme", t), theme);
      await page.goto("/");
      await expect(page.getByTestId("session-surface")).toBeVisible();
      await expect(page.getByTestId("boot-pane")).toHaveCount(0);
      await page.keyboard.press("Control+,");
      await expect(page.getByTestId("settings-shell")).toBeVisible();

      const labels = await page.locator(".settings-rail-item .settings-rail-label").allTextContents();
      expect(labels.length).toBeGreaterThan(8);
      for (const label of labels) {
        await page.locator(".settings-rail-item", { hasText: label }).first().click();
        await expect(page.locator(".settings-section-title")).toHaveText(label);
        expect(await overflowIn(page), `${label} at ${width}px ${theme}`).toEqual([]);
      }

      // A connected provider opens its page; one not connected opens in the Add provider window,
      // which must fit the window too (settings redesign, 2026-10-20).
      await page.locator(".settings-rail-item", { hasText: "Models" }).first().click();
      // Whether a provider is on the page is read only once the Connected list has loaded.
      await expect(page.getByTestId("set-provider-openai")).toBeVisible();
      for (const name of PROVIDERS) {
        const onPage = page.getByTestId(`set-provider-${name}`);
        if (await onPage.count()) {
          await onPage.click();
          await expect(page.getByTestId("set-back")).toBeVisible();
          expect(await overflowIn(page), `Models ▸ ${name} at ${width}px ${theme}`).toEqual([]);
          await page.getByTestId("set-back").click();
          continue;
        }
        await page.getByTestId("models-add-provider").click();
        const dialog = page.getByTestId("add-provider");
        await dialog.getByTestId(`set-provider-${name}`).click();
        await expect(dialog.getByTestId("set-back")).toBeVisible();
        const box = (await dialog.boundingBox())!;
        expect(box.x, `Add provider ▸ ${name} at ${width}px ${theme}`).toBeGreaterThanOrEqual(0);
        expect(box.x + box.width).toBeLessThanOrEqual(width + 1);
        expect(await dialog.evaluate((el) => el.scrollWidth <= el.clientWidth + 1)).toBe(true);
        await page.keyboard.press("Escape");
        await page.keyboard.press("Escape");
        await expect(dialog).toHaveCount(0);
      }

      // A connector's page in its shapes: before connecting, a built-in, a server's own page.
      await page.locator(".settings-rail-item", { hasText: "Connectors" }).first().click();
      for (const id of ["connector-monday", "connector-browser", "mcp-row-chrome-devtools"]) {
        const row = page.getByTestId(id);
        if ((await row.count()) === 0) continue;
        await row.click({ position: { x: 40, y: 20 } });
        await expect(page.getByTestId("connectors-breadcrumb")).toBeVisible();
        expect(await overflowIn(page), `Connectors ▸ ${id} at ${width}px ${theme}`).toEqual([]);
        await page.getByTestId("connectors-breadcrumb").click();
      }
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    });
  }
}
