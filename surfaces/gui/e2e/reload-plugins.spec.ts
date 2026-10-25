// `/reload-plugins`: plugins are fixed when a conversation's engine is
// built, so the row asks the server to rebuild it between turns. The server tells every
// view of the conversation (`plugins_reloaded`), and each one reconnects to the new engine.
import { expect } from "@playwright/test";
import { test, sendSessionEvent } from "./fixtures";

test("/reload-plugins rebuilds the conversation and the view reconnects", async ({ page }) => {
  // The session socket is mocked, so count the page's own connects.
  await page.addInitScript(() => {
    const Real = window.WebSocket;
    (window as unknown as { __sessionConnects: number }).__sessionConnects = 0;
    window.WebSocket = class extends Real {
      constructor(url: string | URL, protocols?: string | string[]) {
        super(url, protocols);
        if (String(url).includes("/ws/session/")) (window as unknown as { __sessionConnects: number }).__sessionConnects += 1;
      }
    } as typeof WebSocket;
  });
  const connectCount = () => page.evaluate(() => (window as unknown as { __sessionConnects: number }).__sessionConnects);
  let reloads = 0;
  await page.route(/\/v1\/sessions\/[^/]+\/reload-plugins$/, (route) => {
    reloads += 1;
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ ok: true, plugins: 2, errors: 0 }),
    });
  });
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();
  const box = page.getByPlaceholder(/Ask anything/);
  await expect.poll(connectCount).toBeGreaterThan(0);

  await box.fill("/reload");
  const row = page.getByTestId("slash-command-reload-plugins");
  await expect(row).toBeEnabled();
  const before = await connectCount();
  await row.click();
  await expect.poll(() => reloads).toBe(1);

  await sendSessionEvent(page, "plugins_reloaded", { plugins: 2, errors: 0 });
  await expect(page.getByText(/Plugins reloaded — 2 active/)).toBeVisible();
  await expect.poll(connectCount).toBe(before + 1);
});

test("a refused reload says why in the conversation", async ({ page }) => {
  await page.route(/\/v1\/sessions\/[^/]+\/reload-plugins$/, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ ok: false, error: "a turn is running — reload the plugins when it ends" }),
    }),
  );
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();
  await page.getByPlaceholder(/Ask anything/).fill("/reload");
  await page.getByTestId("slash-command-reload-plugins").click();
  await expect(page.getByText("a turn is running — reload the plugins when it ends")).toBeVisible();
});

// Seen live on :1420: right after a restart this view's socket was still building its
// engine, so no view was registered for the frame. The answer's `views: 0` is then the
// only signal, and the view says it itself.
test("a reload no view was listening for is still reported", async ({ page }) => {
  await page.route(/\/v1\/sessions\/[^/]+\/reload-plugins$/, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ ok: true, plugins: 3, errors: 1, views: 0 }),
    }),
  );
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();
  await page.getByPlaceholder(/Ask anything/).fill("/reload");
  await page.getByTestId("slash-command-reload-plugins").click();
  await expect(page.getByText(/Plugins reloaded — 3 active, 1 with a problem/)).toBeVisible();
});
