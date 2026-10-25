// GitHub's page. Its tools are GitHub's own MCP server: the page says how this
// computer is signed in, lets the person pick the server's groups of tools, and asks for a
// permission only when a group picked needs one the sign-in does not have.
import { expect, type Page } from "@playwright/test";
import { test, openConnectors } from "./fixtures";

test.beforeEach(async ({ page }) => {
  await page.goto("/");
  await openConnectors(page);
  await page.getByTestId("connector-github").click();
});

const group = (page: Page, id: string) => page.getByTestId(`toolset-${id}`).locator("input");
const grant = (page: Page) => page.getByTestId("toolsets-grant");

test("the page names the sign-in and lists the tool groups, locked ones fixed", async ({ page }) => {
  await expect(page.getByTestId("github-mode-badge")).toContainText("GitHub's own tools");
  const groups = page.getByTestId("toolsets-group");
  await expect(groups.getByTestId("toolset-repos").locator("input")).toBeDisabled();
  await expect(groups.getByTestId("toolset-actions").locator("input")).toBeChecked();
  await expect(groups.getByTestId("toolset-gists").locator("input")).not.toBeChecked();
  // Reeve's own two tools are under the Tools fold.
  await page.getByTestId("tools-fold").click();
  await expect(page.getByText("Clone a repo")).toBeVisible();
});

test("turning on a group the sign-in cannot serve offers Grant access, and granting clears it", async ({ page }) => {
  await expect(grant(page)).toHaveCount(0);
  // Picking it signs in to nothing; its row says what it needs.
  await group(page, "gists").click();
  await expect(group(page, "gists")).toBeChecked();
  await expect(page.getByTestId("toolset-needs-gists")).toBeVisible();
  await expect(grant(page)).toBeVisible();

  // Grant access is the ordinary sign-in once more; the mock shows its device code.
  await grant(page).click();
  await page.getByTestId("modal-mcp-one-click").click();
  await expect(page.getByTestId("modal-device-prompt")).toContainText("ABCD-1234");
  await expect(page.getByTestId("toolset-needs-gists")).toHaveCount(0, { timeout: 15_000 });
  await expect(grant(page)).toHaveCount(0);
});

test("a group that needs no extra permission turns on quietly", async ({ page }) => {
  await group(page, "actions").click();
  await expect(group(page, "actions")).not.toBeChecked();
  await group(page, "actions").click();
  await expect(group(page, "actions")).toBeChecked();
  await expect(page.getByTestId("toolset-needs-actions")).toHaveCount(0);
  await expect(grant(page)).toHaveCount(0);
});

test("before connecting, the page lists the groups the server serves, not just clone and pull", async ({ page }) => {
  await page.getByRole("button", { name: "Disconnect" }).click();
  await page.getByTestId("confirm-dialog-ok").click();
  const groups = page.getByTestId("available-toolsets");
  await expect(groups).toContainText("Repositories", { timeout: 10_000 });
  for (const said of ["Actions", "off until you turn it on"]) await expect(groups).toContainText(said);
  await expect(page.getByText("2 tools Reeve adds on this computer")).toBeVisible();
});
