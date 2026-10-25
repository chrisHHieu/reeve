// MCP servers the person adds, on the Connectors page. "Add custom server" opens a
// dialog with two ways in, a remote URL or pasted JSON. An added server joins the "Custom · MCP"
// group with a chip that says only what is known (Testing…, Live, Error, Needs sign-in, Not
// tested), and has a page of its own with Test.
import { expect, type Page } from "@playwright/test";
import { test, openConnectors } from "./fixtures";

/** The add dialog, opened from the Connectors page. */
async function addDialog(page: Page) {
  await page.goto("/");
  await openConnectors(page);
  await page.getByTestId("add-custom-server").click();
  return page.getByTestId("add-mcp-modal");
}

/** Add a server by URL from the open dialog. */
async function addByUrl(dialog: ReturnType<Page["getByTestId"]>, name: string, url: string) {
  await dialog.getByTestId("mcp-add-name").fill(name);
  await dialog.getByTestId("mcp-add-url").fill(url);
  await dialog.getByRole("button", { name: "Add & test" }).click();
}

test("remote URL add: probe flips the row to Live with tool count", async ({ page }) => {
  const dialog = await addDialog(page);
  // A URL that is not whole is refused before anything is added.
  await addByUrl(dialog, "notes", "mcp.example.com/mcp");
  await expect(dialog.getByText("Enter the server's full URL")).toBeVisible();
  await addByUrl(dialog, "notes", "https://mcp.example.com/mcp");

  const row = page.getByTestId("mcp-row-notes");
  await expect(row).toContainText("Testing…");
  await expect(row).toContainText("Live", { timeout: 10_000 });
  await expect(row).toContainText("6 tools");
});

// A guarded server refuses the first, anonymous probe. Its row says it needs signing in; the
// fix is on its page, with the server's own words.
test("guarded server: 401 → Needs sign-in chip → OAuth switch on the detail page", async ({ page }) => {
  await addByUrl(await addDialog(page), "locked-crm", "https://mcp.locked.example/mcp");
  const row = page.getByTestId("mcp-row-locked-crm");
  await expect(row).toContainText("Needs sign-in", { timeout: 10_000 });
  await row.click();

  const detail = page.getByTestId("mcp-detail-locked-crm");
  await expect(detail).toContainText("authentication required");
  await detail.getByTestId("mcp-authfix-locked-crm").click();
  await expect(detail).toContainText("Signing in…");
  await expect(detail).toContainText("Live", { timeout: 10_000 });

  // Signing out forgets the tokens, and it needs signing in again.
  await detail.getByTestId("mcp-signout-locked-crm").click();
  await expect(detail).toContainText("Needs sign-in");
  await expect(detail.getByTestId("mcp-signin-locked-crm")).toBeVisible();
});

// A pasted stdio server is set up, not yet connected, and its chip says so until it is tested.
test("JSON tab adds stdio as Not tested; detail Test flips it to Live", async ({ page }) => {
  const dialog = await addDialog(page);
  await dialog.getByTestId("mcp-add-tab-json").click();
  await dialog.locator("textarea").fill(JSON.stringify({ files: { command: "npx", args: ["-y", "@modelcontextprotocol/server-filesystem"] } }));
  await dialog.getByRole("button", { name: "Add", exact: true }).click();

  const row = page.getByTestId("mcp-row-files");
  await expect(row).toContainText("Not tested");
  await expect(row).toContainText("stdio");
  await row.click();
  const detail = page.getByTestId("mcp-detail-files");
  await detail.getByTestId("mcp-test-files").click();
  await expect(detail).toContainText("Testing…");
  await expect(detail).toContainText("Live", { timeout: 10_000 });
  await expect(detail).toContainText("6 tools");

  // Removing it, once confirmed, goes back to the list without it.
  await detail.getByTestId("mcp-remove-files").click();
  await page.getByTestId("confirm-dialog-ok").click();
  await expect(page.getByTestId("mcp-row-files")).toHaveCount(0);
});

// The name comes from the URL's first telling part of the host (not mcp, api, data or www), and
// never over a name the person typed.
test("the name field prefills from the URL's distinctive host label", async ({ page }) => {
  const dialog = await addDialog(page);
  const name = dialog.getByTestId("mcp-add-name");
  await dialog.getByTestId("mcp-add-url").fill("https://data.acme.link/api/mcp");
  await expect(name).toHaveValue("acme");
  await name.fill("warehouse");
  await dialog.getByTestId("mcp-add-url").fill("https://mcp.linear.app/mcp");
  await expect(name).toHaveValue("warehouse");
});
