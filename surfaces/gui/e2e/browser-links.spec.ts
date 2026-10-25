// A web link in the conversation opens in the panel's Browser, in the desktop app; a session's
// Browser tabs are its own (owner, 2026-10-20). The desktop shell is simulated as
// composer-platform.spec.ts does, with the calls the page makes to it written down.
import type { Page } from "@playwright/test";
import { expect } from "@playwright/test";
import { seedSessionMessages, test } from "./fixtures";

const LINK = "https://docs.example.com/guide";

async function desktop(page: Page) {
  await page.addInitScript(() => {
    (window as any).__shellCalls = [];
    (window as any).reeveDesktop = {
      invoke: async (cmd: string, args?: unknown) => {
        (window as any).__shellCalls.push({ cmd, args });
        return null;
      },
      on: () => () => {},
    };
    (window as any).__REEVE_PLATFORM__ = "windows";
  });
}

const shellCalls = (page: Page) => page.evaluate(() => (window as any).__shellCalls as { cmd: string; args?: any }[]);

test("a link in an answer opens in the panel's Browser; with Ctrl it leaves for the computer's browser", async ({ page }) => {
  await desktop(page);
  await page.setViewportSize({ width: 1440, height: 900 });
  await seedSessionMessages(page, "pinned-session-1", [
    { role: "user", content: "Where are the docs?" },
    { role: "assistant", content: `They are here: [Example guide](${LINK}).` },
  ]);
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();
  const link = page.locator(".main").getByRole("link", { name: /Example guide/ });
  await expect(link).toBeVisible();
  // It stays in the app, so it wears no ↗ (owner, 2026-10-20).
  await expect(link.locator(".md-link-out")).toHaveCount(0);

  await link.click();
  await expect(page.getByTestId("browser-address")).toHaveValue(LINK);
  await expect(page.locator(".rail-chip.on")).toContainText("docs.example.com");
  expect((await shellCalls(page)).some((call) => call.cmd === "open_external")).toBe(false);

  // Followed again: the same tab, not a second one.
  await link.click();
  await expect(page.locator(".rail-chip[data-kind='browser']")).toHaveCount(1);

  await link.click({ modifiers: ["Control"] });
  await expect.poll(async () => (await shellCalls(page)).filter((call) => call.cmd === "open_external").map((call) => call.args?.url)).toEqual([LINK]);
});

test("another session's Browser does not wear this one's page title", async ({ page }) => {
  await desktop(page);
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();
  await page.getByTestId("rail-new").click();
  await page.getByTestId("rail-new-browser").click();
  await page.getByTestId("browser-address").fill(LINK);
  await page.getByTestId("browser-address").press("Enter");
  // The page names itself, as the desktop browser reports it.
  await page.locator("webview").first().evaluate((el) => {
    const event = new Event("page-title-updated") as Event & { title?: string };
    event.title = "Example account settings";
    el.dispatchEvent(event);
  });
  await expect(page.locator(".rail-chip.on")).toContainText("Example account settings");

  await page.getByText("Weekly plan 1").first().click();
  await page.getByTestId("rail-new").click();
  await page.getByTestId("rail-new-browser").click();
  await expect(page.getByTestId("browser-start")).toBeVisible();
  await expect(page.locator(".rail-chip.on")).not.toContainText("Example account settings");
  await expect(page.locator(".rail-chip.on")).toContainText("Browser");
});
