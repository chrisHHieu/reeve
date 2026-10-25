// The window is not always a desktop monitor. These pin what the app does when it is small:
//
//   · the sidebar gives way to its rail rather than leaving the conversation a sliver
//   · an expanded sidebar covers a phone-width page instead of squeezing it
//   · Settings' rail becomes a strip across the top
//   · the composer keeps the MODEL readable (it used to be cut to "Cla…" in a rail-open column)
//   · an empty conversation's start page reads from its top
import { expect } from "@playwright/test";
import type { Page } from "@playwright/test";
import { seedSessionMessages, test } from "./fixtures";

/** Past the start: the frame is drawn from the first frame, and usable once the boot pane is gone. */
async function ready(page: Page) {
  await page.goto("/");
  await expect(page.getByTestId("session-surface")).toBeVisible();
  await expect(page.getByTestId("boot-pane")).toHaveCount(0);
}

test.describe("phone width", () => {
  test.use({ viewport: { width: 420, height: 820 } });

  test("the conversation keeps the width; the sidebar is its 56px rail", async ({ page }) => {
    await ready(page);
    const chat = page.locator(".main-chat");
    await expect(chat).toBeVisible();
    await expect.poll(async () => (await chat.boundingBox())!.width).toBeGreaterThan(340);
    await expect.poll(async () => (await page.locator(".sidebar").boundingBox())!.width).toBeLessThan(80);
  });

  test("the sidebar can still be opened; it covers the page and closes when you pick something", async ({ page }) => {
    await ready(page);
    await page.keyboard.press("Control+b");
    await expect(page.getByTestId("nav-scrim")).toBeVisible();
    const open = await page.locator(".sidebar").boundingBox();
    expect(open!.width).toBeGreaterThan(250);
    // ...and the page behind it did not move
    await expect.poll(async () => (await page.locator(".main-chat").boundingBox())!.width).toBeGreaterThan(340);

    await page.getByText("Weekly plan 1").first().click();
    await expect(page.getByTestId("nav-scrim")).toHaveCount(0);
    await expect(page.locator(".main-title-text")).toContainText("Weekly plan 1");
  });

  test("tapping outside the opened sidebar closes it", async ({ page }) => {
    await ready(page);
    await page.keyboard.press("Control+b");
    await expect(page.getByTestId("nav-scrim")).toBeVisible();
    await page.getByTestId("nav-scrim").click({ position: { x: 390, y: 400 } });
    await expect(page.getByTestId("nav-scrim")).toHaveCount(0);
  });

  test("Settings puts its destinations in a strip above the content", async ({ page }) => {
    await ready(page);
    await page.keyboard.press("Control+,");
    const shell = page.getByTestId("settings-shell");
    await expect(shell).toBeVisible();
    const rail = await page.locator(".settings-rail").boundingBox();
    const content = await page.locator(".settings-content").boundingBox();
    // stacked, not side by side
    expect(rail!.width).toBeGreaterThan(380);
    expect(content!.y).toBeGreaterThanOrEqual(rail!.y + rail!.height - 1);
    // the content is the full width, so a row's words and its control both fit
    expect(content!.width).toBeGreaterThan(380);
  });

  test("a new conversation's start page reads from its top", async ({ page }) => {
    // The side panel (open by default in this harness) covers a phone-width window entirely.
    await page.addInitScript(() => {
      localStorage.setItem("reeve-e2e-rail-default", "1");
      localStorage.removeItem("reeve:rail-hidden:v1");
    });
    await ready(page);
    // the rail's own "+" (aria-label "New session")
    await page.getByRole("button", { name: "New session", exact: true }).first().click();
    const scroller = page.locator(".main-scroll");
    await expect(scroller).toBeVisible();
    await expect.poll(() => scroller.evaluate((el) => el.scrollTop)).toBe(0);
  });
});

test.describe("laptop with the panel open", () => {
  test.use({ viewport: { width: 1280, height: 760 } });

  test("the composer shows the model's name, not the first three letters of it", async ({ page }) => {
    await ready(page); // the fixtures open the panel, which is what narrows the composer
    const model = page.getByTestId("model-picker-trigger");
    await expect(model).toBeVisible();
    const label = model.locator(".pill-label");
    await expect(label).toContainText("Claude Opus");
    const clipped = await label.evaluate((el) => el.scrollWidth > el.clientWidth + 1);
    expect(clipped).toBe(false);
  });
});

test.describe("tablet width", () => {
  test.use({ viewport: { width: 900, height: 760 } });

  test("the sidebar is the rail, and a peek opens it beside the page, or over it beside an open panel", async ({ page }) => {
    await ready(page);
    await expect.poll(async () => (await page.locator(".sidebar").boundingBox())!.width).toBeLessThan(80);
    // The fixtures open the side panel: with it open, the peek covers the page.
    await expect(page.locator(".right-rail")).toBeVisible();
    await page.keyboard.press("Control+b");
    await expect.poll(async () => (await page.locator(".sidebar").boundingBox())!.width).toBeGreaterThan(250);
    await expect(page.getByTestId("nav-scrim")).toHaveCount(1);
    await page.keyboard.press("Control+b");
    await expect(page.getByTestId("work-queue")).toHaveCount(0);
    // The panel put away (⌘⌥B, which leaves the queue alone), the peek stands beside the page.
    await page.keyboard.press("Control+Alt+b");
    await expect(page.locator(".right-rail")).toHaveCount(0);
    await expect.poll(async () => (await page.locator(".sidebar").boundingBox())!.width).toBeLessThan(80);
    await page.keyboard.press("Control+b");
    await expect.poll(async () => (await page.locator(".sidebar").boundingBox())!.width).toBeGreaterThan(250);
    await expect(page.getByTestId("nav-scrim")).toHaveCount(0);
  });
});

test.describe("a big window with a panel that was dragged wide", () => {
  test.use({ viewport: { width: 1914, height: 921 } });

  test("the conversation keeps a third of the room, not a 450px sliver", async ({ page }) => {
    // 1170px: what a panel sized on a bigger monitor (or dragged once) leaves in storage.
    await page.addInitScript(() => localStorage.setItem("reeve:rail-width:v2", "1170"));
    await ready(page);
    // 1914 beside the 356px nav: a third of 1558 is 514.
    await expect.poll(async () => (await page.locator(".main-chat").boundingBox())!.width).toBeGreaterThan(505);
  });
});

test.describe("a laptop with the panel open (a 450px conversation)", () => {
  test.use({ viewport: { width: 1366, height: 768 } });

  test("a long MCP failure notice stays inside the column: no horizontal scrollbar", async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem("reeve:rail-width:v2", "1170"));
    await seedSessionMessages(page, "pinned-session-1", [
      { role: "user", content: "hi", ts: 1755600000 },
      { role: "assistant", content: "hello", ts: 1755600001 },
      {
        role: "notice",
        kind: "mcp_error",
        server: "github",
        text: "MCP server “github” didn’t start — its tools are unavailable: spawn npx ENOENT while starting the server",
      },
    ]);
    await ready(page);
    await expect(page.getByTestId("mcp-notice")).toBeVisible();
    // Read once the panel has taken its stored width: measured on the first frame, the column was
    // sometimes still the wider one it had before, and the notice ran past it (6 runs in 20).
    await expect.poll(async () => page.locator(".main-scroll").evaluate((el) => el.scrollWidth - el.clientWidth)).toBeLessThanOrEqual(1);
    await expect
      .poll(async () => {
        const notice = (await page.getByTestId("mcp-notice").boundingBox())!;
        const scroller = (await page.locator(".main-scroll").boundingBox())!;
        return notice.x + notice.width - (scroller.x + scroller.width);
      })
      .toBeLessThanOrEqual(1);
  });

  test("the composer's footer shrinks its labels instead of clipping the row", async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem("reeve:rail-width:v2", "1170"));
    await page.route("**/v1/settings", (route) =>
      route.fulfill({
        json: {
          models: ["anthropic:claude-opus-4-8"],
          model_labels: { "anthropic:claude-opus-4-8": "DeepSeek V4.1 Flash with a very long name" },
          model_ready: true,
          onboarded: true,
          surfaces: { agent: true, chat: false, code: true },
        },
      }),
    );
    await ready(page);
    const chip = page.getByRole("button", { name: "Mode", exact: true });
    await chip.click();
    await page.getByTestId("mode-menu").getByText("Full access").click();
    const footer = page.locator(".composer-footer");
    await expect(footer).toBeVisible();
    const clipped = await footer.evaluate((el) => el.scrollWidth - el.clientWidth);
    expect(clipped).toBeLessThanOrEqual(1);
    // the mode chip keeps its whole name: it is the permission state
    await expect(chip).toContainText("Full access");
    expect(await chip.evaluate((el) => el.scrollWidth <= el.clientWidth + 1)).toBe(true);
  });
});
