// Left-nav behavior (2026-10-13): the 56px rail is always there (the brand anchor, New session,
// Search, Automations, Metrics; Settings at the bottom), and collapse (⌘B / the rail's anchor)
// folds the work queue beside it away and brings it back.
import { expect } from "@playwright/test";
import { test } from "./fixtures";

test("collapse turns the sidebar into an icon rail; the rail docks it back", async ({ page }) => {
  await page.goto("/");
  const app = page.locator(".app");
  await expect(page.locator(".sidebar")).toBeVisible();

  // Collapse via the brand row's control.
  await page.getByRole("button", { name: "Collapse sidebar" }).click();
  await expect(app).toHaveClass(/nav-collapsed/);

  // The rail stays visible with its top-down actions; the expanded IA leaves the DOM.
  const rail = page.getByTestId("sidebar-rail");
  await expect(rail).toBeVisible();
  await expect(page.getByTestId("rail-dock")).toBeVisible();
  await expect(page.getByTestId("rail-automations")).toBeVisible();
  await expect(page.getByTestId("rail-new-session")).toBeVisible();
  await expect(page.getByTestId("rail-search")).toBeVisible();
  await expect(page.getByTestId("rail-settings")).toBeVisible();
  // The same order as the wide nav, top to bottom.
  const order = await rail.locator("[data-testid]").evaluateAll((nodes) => nodes.map((node) => node.getAttribute("data-testid")));
  expect(order.filter((id) => id !== "rail-waiting")).toEqual(["rail-dock", "rail-new-session", "rail-search", "rail-automations", "rail-ops", "rail-settings"]);
  // The column slides to the rail's width (owner, 2026-10-12: it had stopped moving), and
  // the page takes the room as it goes.
  expect(await page.locator(".app").evaluate((node) => getComputedStyle(node).transitionProperty)).toContain("grid-template-columns");
  await expect.poll(async () => Math.round((await page.locator(".sidebar").boundingBox())!.width)).toBe(56);
  // The rail's anchor IS the brand toggle: the mark rests, the panel glyph opens it.
  await expect(page.getByTestId("rail-dock")).toHaveAttribute("aria-label", "Open sidebar");
  await expect(page.getByTestId("work-queue")).toHaveCount(0);

  // Dock it back from the rail's own toggle.
  await page.getByTestId("rail-dock").click();
  await expect(app).not.toHaveClass(/nav-collapsed/);
  // The rail stays: it is the frame. The queue comes back beside it.
  await expect(page.getByTestId("sidebar-rail")).toBeVisible();
  await expect(page.getByTestId("work-queue")).toBeVisible();
});

test("⌘B toggles the sidebar collapse", async ({ page }) => {
  await page.goto("/");
  // The frame is drawn while Reeve starts (boot screen redesign): wait for the start to end.
  await expect(page.getByTestId("boot-pane")).toHaveCount(0);
  const app = page.locator(".app");
  await expect(page.locator(".sidebar")).toBeVisible();
  await page.keyboard.press("Meta+b");
  await expect(app).toHaveClass(/nav-collapsed/);
  await expect(page.getByTestId("sidebar-rail")).toBeVisible();
  await page.keyboard.press("Meta+b");
  await expect(app).not.toHaveClass(/nav-collapsed/);
});

test("rail actions run the same handlers as the expanded rows", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator(".sidebar")).toBeVisible();
  await page.getByRole("button", { name: "Collapse sidebar" }).click();
  await expect(page.getByTestId("sidebar-rail")).toBeVisible();

  // Search → the command palette over the whole app.
  await page.getByTestId("rail-search").click();
  await expect(page.getByPlaceholder("Search chats")).toBeVisible();
  await page.keyboard.press("Escape");

  // New session → a fresh session with its setup row back.
  await page.getByTestId("rail-new-session").click();
  await expect(page.getByTestId("setup-row")).toBeVisible();

  // Automations (the rail's clock entry) → the Automations surface.
  await page.getByTestId("rail-automations").click();
  await expect(page.getByText("Tasks Reeve runs for you on a schedule.")).toBeVisible();

  // Settings is one click from the rail's foot.
  await page.getByTestId("rail-settings").click();
  await expect(page.getByTestId("app-menu")).toHaveCount(0);
  await expect(page.getByTestId("settings-shell")).toBeVisible();
  await expect(page.getByLabel("Search settings")).toBeVisible();
});

// The queue (2026-10-13) has no organize menu and no group by agent: the groups are what each
// session needs, and the projects are listed under them (2026-10-14).
test("the queue has no organize menu, no agent filter, and lists the projects", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("button", { name: "Group and filter sessions" })).toHaveCount(0);
  await expect(page.getByTestId("sidebar-organize")).toHaveCount(0);
  await expect(page.getByTestId("queue-projects")).toBeVisible();
});
