// The transcript follows a streaming turn only while the reader is at its foot.
// Scrolling up holds the view where it is, since reading must never be pulled away, and shows a
// way back to the latest. A bubble's copy and time show under the pointer without moving
// anything. Played on the fake agent's slow "stream the epic" turn.
import { expect, type Page } from "@playwright/test";
import { test } from "./fixtures";
import { composer } from "./steps";

// The copy is checked in the real clipboard.
test.use({ permissions: ["clipboard-write"] });

/** How far the scroller is from its foot, and where it is. */
const scroller = (page: Page) =>
  page.locator(".main-scroll").evaluate((el) => ({
    top: el.scrollTop, height: el.scrollHeight, over: el.scrollHeight - el.clientHeight, gap: el.scrollHeight - el.scrollTop - el.clientHeight,
  }));

/** Open the pinned session and send `text` with Enter. */
async function send(page: Page, text: string) {
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();
  await composer(page).fill(text);
  await composer(page).press("Enter");
}

/** Start the epic and wait until it is taller than the view by a good margin. */
async function streamTallerThanTheView(page: Page) {
  await send(page, "stream the epic");
  await expect.poll(async () => (await scroller(page)).over, { timeout: 10_000 }).toBeGreaterThan(400);
}

test("scrolling up mid-stream pins the viewport; jump-to-latest re-engages", async ({ page }) => {
  await streamTallerThanTheView(page);
  await page.locator(".main-scroll").evaluate((el) => (el.scrollTop = 0));

  // Text keeps arriving below, and the view stays where the reader put it: it once went to the
  // foot on every piece. The way back to the latest is offered.
  const before = (await scroller(page)).height;
  await expect.poll(async () => (await scroller(page)).height).toBeGreaterThan(before);
  expect((await scroller(page)).top).toBeLessThan(50);
  const jump = page.getByTestId("jump-to-latest");
  await expect(jump).toBeVisible();

  await jump.click();
  await expect.poll(async () => (await scroller(page)).gap).toBeLessThan(80);
  await expect(jump).toHaveCount(0);
  // Following again, to the end of the turn.
  await expect(page.getByText("The epic concludes.").first()).toBeVisible({ timeout: 10_000 });
  expect((await scroller(page)).gap).toBeLessThan(80);
});

// With nobody at the wheel, the gap to the foot once grew from 76 to 2565px and the jump button
// covered the text (rendering review, G1): each piece started a smooth scroll that never landed.
test("a reader who never scrolls stays at the bottom for the whole stream", async ({ page }) => {
  await streamTallerThanTheView(page);
  const gaps: number[] = [];
  for (let sample = 0; sample < 20; sample++) {
    gaps.push((await scroller(page)).gap);
    await page.waitForTimeout(150);
  }
  // A piece can land between a sample and the follow it starts; more than a piece behind is the
  // view falling behind.
  expect(Math.max(...gaps)).toBeLessThan(160);
  await expect(page.getByTestId("jump-to-latest")).toHaveCount(0);
});

test("bubbles carry hover copy + timestamp without layout shift", async ({ page }) => {
  await send(page, "hello meta");
  await expect(page.getByText("Echo: hello meta", { exact: false }).first()).toBeVisible();
  // Live messages are stamped by the client, so both bubbles have the strip.
  await page.locator(".bubble-user").last().hover();
  const copy = page.getByTestId("bubble-copy").first();
  await expect(copy).toBeVisible();
  await expect(page.getByTestId("bubble-ts").first()).toBeVisible();
  await copy.click();
  await expect(page.getByText("Copied").first()).toBeVisible();
});
