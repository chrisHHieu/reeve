// A trusted project that declares plugins or suggests a marketplace
// says so once, above its conversation: what is not in place here, Review → the Plugins
// page, and a dismiss that holds until what the project asks for changes.
import { expect, type Page } from "@playwright/test";
import { test } from "./fixtures";

const VIEW = {
  trusted: true,
  plugins: [
    { id: "formatter@acme", scope: "project", state: "missing" },
    { id: "guard@acme", scope: "project", state: "needs_consent" },
  ],
  marketplaces: [{ name: "team", source: "/Users/test/tools-market", file: ".reeve/plugins.json", known: false }],
  pending: 3,
};

async function serveProject(page: Page, view: unknown) {
  await page.route(/\/v1\/plugins\/project(\?.*)?$/, (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(view) }),
  );
}

test("the project's plugin banner says what is missing and Review opens the Plugins page", async ({ page }) => {
  await serveProject(page, VIEW);
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();
  const banner = page.getByTestId("project-plugins-banner");
  await expect(banner).toContainText("1 plugin not installed · 1 waiting for your yes · 1 marketplace to add");
  await banner.getByRole("button", { name: "Review" }).click();
  await expect(page.getByRole("heading", { name: "Plugins" })).toBeVisible();
});

test("a dismissed banner stays away until the project asks for something new", async ({ page }) => {
  await serveProject(page, VIEW);
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();
  await page.getByTestId("project-plugins-dismiss").click();
  await expect(page.getByTestId("project-plugins-banner")).toHaveCount(0);

  await page.reload();
  await page.getByText("Draft the launch note").first().click();
  await expect(page.getByText("Launch").first()).toBeVisible();
  await expect(page.getByTestId("project-plugins-banner")).toHaveCount(0);

  await serveProject(page, { ...VIEW, plugins: [...VIEW.plugins, { id: "lint@acme", scope: "project", state: "missing" }], pending: 4 });
  await page.reload();
  await page.getByText("Draft the launch note").first().click();
  await expect(page.getByTestId("project-plugins-banner")).toContainText("2 plugins not installed");
});

test("an untrusted project shows no banner", async ({ page }) => {
  await serveProject(page, { trusted: false, plugins: [], marketplaces: [], pending: 0 });
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();
  await expect(page.getByPlaceholder(/Ask anything/)).toBeVisible();
  await expect(page.getByTestId("project-plugins-banner")).toHaveCount(0);
});
