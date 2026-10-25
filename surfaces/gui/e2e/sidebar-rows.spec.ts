import { test, expect } from "./fixtures";

// A queue row (2026-10-13): the title and when it was last active on one line, and under it only
// what is known (what its state says, where its work lives). However long the name, the title is
// cut on its line; the row grows only by the lines it has.
test("a queue row: title and time on one line, then only what is known", async ({ page }) => {
  await page.goto("/");
  // The frame is drawn while Reeve starts (boot screen redesign): wait for the start to end.
  await expect(page.getByTestId("boot-pane")).toHaveCount(0);
  const row = page.locator(".sidebar").getByTitle("Draft the launch note");
  await expect(row).toBeVisible();
  const meta = row.getByTestId("row-meta");
  await expect(meta).toHaveText(/\S/);
  const title = row.locator(".q-title");
  const [t, m] = [(await title.boundingBox())!, (await meta.boundingBox())!];
  // The time sits on the title's line.
  expect(Math.abs(t.y + t.height / 2 - (m.y + m.height / 2))).toBeLessThanOrEqual(2);
  // The title is one line, cut, never wrapped.
  expect(t.height).toBeLessThanOrEqual(20);
  await expect(title).toHaveCSS("white-space", "nowrap");
  // Listed in its project (2026-10-14), it is the title and the time: its folder is the heading
  // above it, and nothing else is news.
  await expect(row.getByTestId("queue-line")).toHaveCount(0);
  await expect(row.getByTestId("queue-facts")).toHaveCount(0);
  expect((await row.boundingBox())!.height).toBeLessThanOrEqual(36);
});

test("a row's time gives way to its menu on hover, and nothing moves", async ({ page }) => {
  await page.goto("/");
  const row = page.locator(".sidebar").getByTitle("Weekly plan 1");
  await expect(row.getByTestId("row-meta")).toBeVisible();
  // Measured in the page: the list is drawn again as its settings load, and a handle taken
  // across that would be of a row that is gone.
  const box = () => row.evaluate((node) => {
    const { x, y, width, height } = node.getBoundingClientRect();
    return { x, y, width, height };
  });
  await expect.poll(async () => JSON.stringify(await box()) === JSON.stringify(await box())).toBe(true);
  const before = await box();
  await expect(row.getByTestId("row-menu").locator("..")).toHaveCSS("opacity", "0");
  await row.hover();
  await expect(row.getByTestId("row-menu").locator("..")).toHaveCSS("opacity", "1");
  await expect(row.getByTestId("row-meta").locator("..")).toHaveCSS("opacity", "0");
  expect(await box()).toEqual(before);
  // Hover changes the ink, never the ground.
  await expect(row).toHaveCSS("background-color", "rgba(0, 0, 0, 0)");
});

// The owner pressed ⋮ on a row and nothing appeared (2026-10-12). The
// menu is `position: fixed` at the button's place in the window, and the wrapper it sat in was
// centred with a transform: a transformed ancestor makes a fixed box answer to that ancestor,
// so the menu was laid out a row's-top-in-the-window further down and cut by the group's
// rounded clip. The other specs press the items through the row and never noticed. This one
// asks what a person does: is each item drawn where it claims to be, right under the button.
test("a row's menu opens under its button and every item of it can be seen", async ({ page }) => {
  await page.goto("/");
  const row = page.locator(".sidebar").getByTitle("Weekly plan 4");
  await row.hover();
  const button = row.getByTestId("row-menu");
  await button.click();
  const menu = row.getByRole("menu");
  await expect(menu).toBeVisible();

  const place = await menu.evaluate((node) => {
    const anchor = node.parentElement!.querySelector('[data-testid="row-menu"]')!.getBoundingClientRect();
    const box = node.getBoundingClientRect();
    // An item is seen when the thing drawn at its middle is the item itself.
    const hidden = [...node.querySelectorAll('[role="menuitem"]')]
      .filter((item) => {
        const r = item.getBoundingClientRect();
        const top = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
        return !top || !item.contains(top);
      })
      .map((item) => item.textContent);
    return {
      // Under the button, or over it when the row is near the foot of the window.
      below: box.top - anchor.bottom,
      above: anchor.top - box.bottom,
      right: box.right - anchor.right,
      inWindow: box.top >= 0 && box.bottom <= window.innerHeight && box.left >= 0 && box.right <= window.innerWidth,
      hidden,
      items: node.querySelectorAll('[role="menuitem"]').length,
    };
  });
  expect(place.items).toBe(5);
  expect(place.hidden).toEqual([]);
  expect(place.inWindow).toBe(true);
  // Beside its button on one side or the other, and never over it.
  const gap = Math.max(place.below, place.above);
  expect(gap).toBeGreaterThanOrEqual(0);
  expect(gap).toBeLessThanOrEqual(32);
  expect(Math.abs(place.right)).toBeLessThanOrEqual(1);
});
