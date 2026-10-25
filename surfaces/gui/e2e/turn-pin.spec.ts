// The question line (session/TurnPin.tsx; owner, 2026-10-13): once the row that started the turn
// you are reading has scrolled away, one line under the top bar says which question it is, and
// moves you between your own messages. It was a floating card that lay on the answer, looked
// like the composer, and was drawn across the top bar's popovers.
import { expect, type Page } from "@playwright/test";
import { test, seedSessionMessages } from "./fixtures";

const TS = 1755600000;
const ASKS = [
  "Which tools does a subagent get by default",
  "What can the subagent tool take",
  "Can a subagent call MCP tools?",
  "OK, test it once more",
];

async function seed(page: Page) {
  const messages: Record<string, unknown>[] = [];
  ASKS.forEach((ask, i) => {
    messages.push({ role: "user", content: ask, ts: TS + i * 600 });
    messages.push({ role: "assistant", content: `Answer ${i + 1}.\n\n` + "A line of detail about the work. ".repeat(70), ts: TS + i * 600 + 50 });
    if (i === 1) {
      // A turn nobody typed: a background subagent reported back.
      messages.push({
        role: "user",
        content: '<task id="c1" state="completed">done</task>',
        source: { synthetic: "background_task", session_id: "c1", title: "Probe MCP access", state: "completed", steps: 6 },
        ts: TS + i * 600 + 120,
      });
      messages.push({ role: "assistant", content: "The probe reported back.\n\n" + "What it found, at length. ".repeat(70), ts: TS + i * 600 + 150 });
    }
  });
  await seedSessionMessages(page, "pinned-session-1", messages);
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();
  await expect(page.getByText(`Answer ${ASKS.length}.`).first()).toBeVisible();
}

/** Scroll into the answer under the `index`-th row matching `selector`. */
async function into(page: Page, index: number, selector = "[data-user-turn]") {
  await page.locator(".main-scroll").evaluate(
    (el, [i, sel]) => {
      const row = el.querySelectorAll<HTMLElement>(sel as string)[i as number];
      el.scrollTop = row.offsetTop + 200;
    },
    [index, selector] as const,
  );
}

/** Where the `index`-th of the person's messages sits, from the top of the view. */
const topOf = (page: Page, index: number) =>
  page.locator(".main-scroll").evaluate((el, i) => {
    const row = el.querySelectorAll<HTMLElement>("[data-user-turn]")[i];
    return Math.round(row.getBoundingClientRect().top - el.getBoundingClientRect().top);
  }, index);

test("the line says whose words they are, which of them, and goes back to the message", async ({ page }) => {
  await seed(page);
  const pin = page.getByTestId("turn-pin");
  await into(page, 2);
  await expect(pin).toHaveAttribute("data-shown", "");
  await expect(page.getByTestId("turn-pin-who")).toHaveText("You asked");
  await expect(page.getByTestId("turn-pin-go")).toHaveText(ASKS[2]);
  await expect(page.getByTestId("turn-pin-count")).toHaveText("3 of 4");

  // It is a line of the frame: flush under the top bar (under the task's head when there is one,
  // 2026-10-13), as wide as the column, no card. Measured
  // once it has arrived: it comes in over 150ms from 4px up, and a reading taken on the way
  // said it was not under the bar.
  await expect.poll(() => pin.evaluate((el) => getComputedStyle(el).transform)).toBe("none");
  const shape = await page.evaluate(() => {
    const line = document.querySelector<HTMLElement>(".turn-pin")!;
    const bar = document.querySelector<HTMLElement>(".main-topbar")!.getBoundingClientRect();
    const head = document.querySelector<HTMLElement>(".act-head")?.getBoundingClientRect();
    const box = line.getBoundingClientRect();
    const style = getComputedStyle(line);
    return {
      under: Math.abs(box.top - (head ?? bar).bottom) < 1.5,
      wide: Math.abs(box.left - bar.left) < 1.5 && Math.abs(box.right - bar.right) < 1.5,
      shadow: style.boxShadow,
      blur: style.backdropFilter || "none",
      radius: style.borderTopLeftRadius,
    };
  });
  expect(shape).toEqual({ under: true, wide: true, shadow: "none", blur: "none", radius: "0px" });

  await page.getByTestId("turn-pin-go").click();
  await expect.poll(() => topOf(page, 2)).toBeGreaterThanOrEqual(0);
  await expect.poll(() => topOf(page, 2)).toBeLessThan(80);
  // The message is on screen again: the line has nothing to say.
  await expect(pin).not.toHaveAttribute("data-shown", "");
});

test("the arrows go to your message above and below, and so do Alt with the arrow keys", async ({ page }) => {
  await seed(page);
  await into(page, 2);
  await expect(page.getByTestId("turn-pin-go")).toHaveText(ASKS[2]);

  // Up: the message this answer is to, then the one before it.
  await page.getByTestId("turn-pin-up").click();
  await expect.poll(() => topOf(page, 2)).toBeLessThan(80);
  await expect.poll(() => topOf(page, 2)).toBeGreaterThanOrEqual(0);
  await page.locator(".main-scroll").hover();
  await page.keyboard.press("Alt+ArrowUp");
  await expect.poll(() => topOf(page, 1)).toBeLessThan(80);
  await expect.poll(() => topOf(page, 1)).toBeGreaterThanOrEqual(0);

  // Down: the next of your messages; a turn nobody typed is passed over.
  await page.keyboard.press("Alt+ArrowDown");
  await expect.poll(() => topOf(page, 2)).toBeLessThan(80);
  await expect.poll(() => topOf(page, 2)).toBeGreaterThanOrEqual(0);

  // While typing, the keys are the text's own (Option with an arrow moves the caret on a Mac).
  const before = await topOf(page, 2);
  await page.getByPlaceholder(/Ask anything/).click();
  await page.keyboard.press("Alt+ArrowUp");
  await page.waitForTimeout(400);
  expect(Math.abs((await topOf(page, 2)) - before)).toBeLessThan(4);
});

test("the count opens everything you asked, where you are, and a line of it goes there", async ({ page }) => {
  await seed(page);
  await into(page, 2);
  const count = page.getByTestId("turn-pin-count");
  await count.click();
  const list = page.getByTestId("turn-pin-list");
  await expect(list).toBeVisible();
  const items = page.getByTestId("turn-pin-item");
  // Four messages and the one turn the engine started, in the order they happened.
  await expect(items).toHaveCount(5);
  await expect(items.nth(0)).toContainText(ASKS[0]);
  await expect(items.nth(2)).toContainText("Subagent finished");
  await expect(items.nth(2)).toHaveAttribute("data-kind", "engine");
  await expect(list.locator("[data-on]")).toHaveCount(1);
  await expect(list.locator("[data-on]")).toContainText(ASKS[2]);
  await expect(count).toHaveAttribute("aria-expanded", "true");

  await items.nth(0).click();
  await expect(list).toHaveCount(0);
  await expect.poll(() => topOf(page, 0)).toBeLessThan(80);

  // Escape puts the list away and the keyboard back on the count.
  await into(page, 2);
  await count.click();
  await expect(list).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(list).toHaveCount(0);
  await expect(count).toBeFocused();
});

test("a turn nobody typed says what started it, not your earlier message", async ({ page }) => {
  await seed(page);
  await into(page, 0, '[data-turn-kind="engine"]');
  await expect(page.getByTestId("turn-pin")).toHaveAttribute("data-kind", "engine");
  await expect(page.getByTestId("turn-pin-who")).toHaveText("Subagent finished");
  await expect(page.getByTestId("turn-pin-go")).toHaveText("Probe MCP access");
  // The count stays on your own messages: this turn came after your second.
  await expect(page.getByTestId("turn-pin-count")).toHaveText("2 of 4");
});

test("the top bar's popover opens over the line, and a narrow column keeps the message and the arrows", async ({ page }) => {
  await seed(page);
  await into(page, 2);
  await expect(page.getByTestId("turn-pin")).toHaveAttribute("data-shown", "");
  await page.getByTestId("status-trigger").click();
  const popover = page.getByTestId("status-popover");
  await expect(popover).toBeVisible();
  // Where the two meet, the popover is what is on top.
  const onTop = await page.evaluate(() => {
    const pop = document.querySelector<HTMLElement>('[data-testid="status-popover"]')!;
    const line = document.querySelector<HTMLElement>(".turn-pin")!.getBoundingClientRect();
    const box = pop.getBoundingClientRect();
    const x = Math.min(box.right - 12, line.right - 12);
    const y = (Math.max(box.top, line.top) + Math.min(box.bottom, line.bottom)) / 2;
    return { meet: box.top < line.bottom && box.bottom > line.top, pop: pop.contains(document.elementFromPoint(x, y)) };
  });
  expect(onTop).toEqual({ meet: true, pop: true });
  await page.keyboard.press("Escape");

  // The session menu is portaled to the page, not a child of the bar: it had no layer of its
  // own, and its first item was drawn behind the line (owner-hit, 2026-10-13). Its first item
  // now lies over the task's head or the line, whichever is under the bar: drawn on top of it.
  await page.getByTestId("session-menu").click();
  await expect(page.getByTestId("session-menu-pop")).toBeVisible();
  const menu = await page.evaluate(() => {
    const first = document.querySelector<HTMLElement>('[data-testid="session-menu-rename"]')!;
    const under = document.querySelector<HTMLElement>(".act-head") ?? document.querySelector<HTMLElement>(".turn-pin")!;
    const line = under.getBoundingClientRect();
    const box = first.getBoundingClientRect();
    const hit = document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2);
    return { meet: box.top < line.bottom && box.bottom > line.top, first: first.contains(hit) };
  });
  expect(menu).toEqual({ meet: true, first: true });
  await page.keyboard.press("Escape");

  // A window with no room: the panel is put away, and the chat's column is narrow.
  await page.setViewportSize({ width: 640, height: 720 });
  await page.keyboard.press("Control+Alt+B");
  await into(page, 2);
  await expect(page.getByTestId("turn-pin-go")).toBeVisible();
  await expect(page.getByTestId("turn-pin-up")).toBeVisible();
  await expect(page.getByTestId("turn-pin-down")).toBeVisible();
  const fits = await page.evaluate(() => {
    const line = document.querySelector<HTMLElement>(".turn-pin")!.getBoundingClientRect();
    const nav = document.querySelector<HTMLElement>(".turn-pin-nav")!.getBoundingClientRect();
    const text = document.querySelector<HTMLElement>(".turn-pin-text")!.getBoundingClientRect();
    return nav.right <= line.right + 0.5 && text.right <= nav.left + 0.5 && text.width > 40;
  });
  expect(fits).toBe(true);
});
