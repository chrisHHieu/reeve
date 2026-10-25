// A turn picked up after a restart names what the restart stopped, and that can be a long command.
// The line's detail could not shrink, so a docker command pushed the whole transcript sideways: a
// horizontal scrollbar under the chat, the text cut at the window's edge, and "Reeve restarted"
// squeezed to nothing (owner's screenshot, 2026-10-21). The detail is what gives way now.
import type { Page } from "@playwright/test";
import { expect } from "@playwright/test";
import { seedSessionMessages, test } from "./fixtures";

const SESSION = "pinned-session-1";
const STOPPED =
  'cd "$REEVE_WORKSPACE" && echo "=== starting Docker Desktop (resident) ==="; docker desktop start 2>&1 | tail -3; ' +
  "for i in $(seq 1 40); do v=$(docker info --format '{{.ServerVersion}}' 2>/dev/null) && break; sleep 3; done; echo $v";

/** Nothing scrolls sideways: not the page, not the scroller the note lives in. */
async function expectNoSideways(page: Page) {
  const note = page.getByTestId("turn-note").first();
  const overflow = await note.evaluate((el) => {
    let node: HTMLElement | null = el.parentElement;
    while (node && !/(auto|scroll)/.test(getComputedStyle(node).overflowX + getComputedStyle(node).overflow)) {
      node = node.parentElement;
    }
    const doc = document.documentElement;
    return {
      scroller: node ? node.scrollWidth - node.clientWidth : 0,
      page: doc.scrollWidth - doc.clientWidth,
    };
  });
  expect(overflow.scroller).toBeLessThanOrEqual(1);
  expect(overflow.page).toBeLessThanOrEqual(1);
}

for (const [width, height] of [
  [1920, 1080],
  [1440, 900],
  [1280, 800],
  [1024, 768],
  [800, 700],
] as const) {
  test(`a restart's line keeps inside the chat at ${width}×${height}, cutting the stopped command`, async ({ page }) => {
    await seedSessionMessages(page, SESSION, [
      { role: "user", content: "Rewrite the docs" },
      { role: "notice", kind: "restart", text: "Reeve restarted", steps: 3, jobs: [STOPPED] },
      { role: "assistant", content: "Picking up where it stopped." },
    ]);
    // Opened in a wide window, where the session list is shown, then the window is resized.
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/");
    await page.getByText("Draft the launch note").first().click();
    await page.setViewportSize({ width, height });

    const note = page.getByTestId("turn-note").first();
    await expect(note).toContainText("3 steps were already done");
    await expectNoSideways(page);
    // "Reeve restarted" is read whole; the stopped command is the part cut, and its whole text
    // is one hover away.
    const verb = note.locator(".wake-title");
    expect(await verb.evaluate((el) => el.scrollWidth <= el.clientWidth + 1 && el.clientWidth > 40)).toBe(true);
    const detail = note.locator(".wake-detail");
    await expect(detail).toHaveAttribute("title", /docker desktop start/);
    expect(await detail.evaluate((el) => el.scrollWidth > el.clientWidth)).toBe(true);
    if (width === 1440) await note.screenshot({ path: test.info().outputPath("restart-note.png") });
  });
}
