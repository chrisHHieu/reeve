// The Metrics tab: reached from the sidebar (expanded and rail), it renders the folded
// payload across three views — Usage, Harness, Tools — switches ranges, and a tool
// failure's trace link opens the session it happened in.
import { expect } from "@playwright/test";
import { sendAppEvent, test } from "./fixtures";

test("Metrics: sidebar entry, three views, range switch, failure trace", async ({ page }) => {
  await page.goto("/");

  await page.getByTestId("rail-ops").click();
  await expect(page.getByTestId("ops-view")).toBeVisible();

  // Usage: provider rows over the durable rollup, prefix-stripped model names.
  await expect(page.getByTestId("usage-table")).toBeVisible();
  await expect(page.getByTestId("usage-table").getByText("anthropic").first()).toBeVisible();

  // Harness: a tile picks the number drawn by day; the checks carry their targets; each model has
  // its own turns and tools; the harness's review of its logs says how old it is.
  await page.getByTestId("ops-tab-harness").click();
  await expect(page.getByTestId("health-checks")).toBeVisible();
  await expect(page.getByTestId("harness-tile-completion")).toHaveAttribute("aria-pressed", "true");
  await page.getByTestId("harness-tile-turns").click();
  await expect(page.getByTestId("soft-columns")).toBeVisible();
  await expect(page.getByTestId("harness-by-model")).toContainText("92.9%");
  await expect(page.getByTestId("failure-shapes")).toContainText("cache-drops");
  await expect(page.getByTestId("review-status")).toContainText("as of 1h ago");
  await expect(page.getByText(/2.1σ above/)).toBeVisible();

  // Range pills re-query (the stub answers every range with the same payload).
  await page.getByTestId("ops-range-today").click();
  await expect(page.getByTestId("ops-range-today")).toHaveAttribute("aria-pressed", "true");

  // Tools: health table, and a failure's trace opens the session it happened in.
  await page.getByTestId("ops-tab-tools").click();
  await expect(page.getByTestId("tool-health")).toBeVisible();
  await expect(page.getByText("run_shell").first()).toBeVisible();
  await page.getByRole("button", { name: "Open trace" }).first().click();
  await expect(page.locator(".main-title-text")).toContainText("Draft the launch note");
});

test("Metrics: the collapsed rail carries the entry too", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Collapse sidebar" }).click();
  await expect(page.getByTestId("rail-ops")).toBeVisible();
  await page.getByTestId("rail-ops").click();
  await expect(page.getByTestId("ops-view")).toBeVisible();
});

// The server pushes `metrics_changed` over /ws/events after each step it folds; the open page refetches
// (about once a second at most), so the numbers follow a running agent instead of waiting for its turn to end.
test("Metrics: a pushed step refetches the open page", async ({ page }) => {
  let fetches = 0;
  page.on("request", (req) => {
    if (new URL(req.url()).pathname.endsWith("/v1/metrics")) fetches += 1;
  });
  await page.goto("/");
  await page.getByTestId("rail-ops").click();
  await expect(page.getByTestId("usage-table")).toBeVisible();
  await page.waitForTimeout(1500); // opening may fetch more than once in dev (StrictMode); count from here
  const opened = fetches;

  for (let i = 0; i < 6; i++) await sendAppEvent(page, { type: "metrics_changed", data: { session_id: "s1" } });
  await expect.poll(() => fetches, { timeout: 5000 }).toBe(opened + 1); // a burst is one fetch
  await page.waitForTimeout(1500);
  expect(fetches).toBe(opened + 1);

  await sendAppEvent(page, { type: "metrics_changed", data: { session_id: "s1" } });
  await expect.poll(() => fetches, { timeout: 5000 }).toBe(opened + 2); // and a later one is heard again
});
