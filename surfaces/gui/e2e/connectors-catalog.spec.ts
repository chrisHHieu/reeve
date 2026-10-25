// The Connectors page. Connected connectors come first, each with its health; a
// row opens the connector's page, with a crumb back. One not connected has a Connect button that
// opens the add dialog where it is, and a page saying what it is, what it reaches and the tools
// it adds. Some connect through the vendor's own MCP server, signed in on this computer, and add
// only a fixed set of its tools.
import { expect, type Page } from "@playwright/test";
import { test, openConnectors } from "./fixtures";

const row = (page: Page, connector: string) => page.getByTestId(`connector-${connector}`);
const connect = (page: Page, connector: string) => row(page, connector).getByRole("button", { name: "Connect" });
const dialog = (page: Page) => page.getByTestId("add-connection-modal");
const backToList = (page: Page) => page.getByTestId("connectors-breadcrumb").click();

test.beforeEach(async ({ page }) => {
  await page.goto("/");
  await openConnectors(page);
});

test.describe("the list", () => {
  test("connected connectors come first with status + health chip", async ({ page }) => {
    await expect(row(page, "slack")).toContainText("acme.example");
    await expect(row(page, "slack")).toContainText("Live");
    await expect(connect(page, "telegram")).toBeVisible();
  });

  test("row navigates to the detail subpage; breadcrumb returns", async ({ page }) => {
    await row(page, "slack").click();
    await expect(page.getByTestId("slack-page")).toBeVisible();
    await backToList(page);
    await expect(row(page, "slack")).toContainText("acme.example");
  });

  // The browser needs no key, so its page has nothing to disconnect.
  test("a keyless connector's page has nothing to disconnect", async ({ page }) => {
    await row(page, "browser").click();
    await expect(page.getByRole("heading", { name: "Browser" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Disconnect" })).toHaveCount(0);
    await backToList(page);
  });

  // Telegram has one way to connect, so its dialog is the form itself.
  test("Connect on a connector with one way in opens its form; Escape closes it", async ({ page }) => {
    await connect(page, "telegram").click();
    await expect(dialog(page)).toBeVisible();
    await expect(dialog(page).locator("input")).not.toHaveCount(0);
    await page.keyboard.press("Escape");
    await expect(dialog(page)).toHaveCount(0);
  });

  test("filter narrows both sections", async ({ page }) => {
    await page.getByPlaceholder("Search connectors", { exact: true }).fill("tele");
    await expect(row(page, "telegram")).toBeVisible();
    await expect(row(page, "slack")).toHaveCount(0);
  });
});

test.describe("before connecting", () => {
  // What it does, what it may reach, said plainly; its tools folded away until asked for.
  test("available row opens the pre-connect detail page", async ({ page }) => {
    await row(page, "gmail").click();
    const detail = page.getByTestId("available-detail");
    await expect(detail).toContainText("Search, summarize, and send over your Gmail.");
    await expect(page.getByTestId("available-access")).toContainText("Reads and searches your mail.");
    await expect(detail).toContainText("Keys and tokens are stored only on this computer");
    await expect(detail).toContainText("2 tools this connector adds");
    await expect(detail).not.toContainText("Send email");
    await page.getByTestId("available-tools-toggle").click();
    await expect(detail).toContainText("Send email");
    // A tool that writes says it asks first.
    await expect(detail).toContainText("asks first");

    await backToList(page);
    await expect(row(page, "gmail")).toBeVisible();
  });

  // Connect on the page opens the dialog; the list's own button opens it without the page.
  test("detail Connect opens the modal; the list pill skips navigation", async ({ page }) => {
    await row(page, "gmail").click();
    await page.getByTestId("available-connect").click();
    await expect(dialog(page)).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(dialog(page)).not.toBeVisible();

    await backToList(page);
    await connect(page, "gmail").click();
    await expect(dialog(page)).toBeVisible();
    await expect(page.getByTestId("available-detail")).not.toBeVisible();
  });
});

test.describe("through the vendor's MCP server", () => {
  // monday has one way in, the local sign-in: no choice of panes and no account of Reeve's to
  // sign into first. The mock's sign-in finishes at once, and the dialog's poll closes it.
  test("monday: one-click MCP connect without cloud sign-in; card flips connected", async ({ page }) => {
    await connect(page, "monday").click();
    await expect(dialog(page)).toBeVisible();
    await expect(dialog(page).getByTestId("modal-pane-manual")).toHaveCount(0);
    await expect(dialog(page).getByTestId("inline-cloud-sign-in")).toHaveCount(0);
    await expect(dialog(page).getByText("sign-in runs entirely on this computer")).toBeVisible();
    await dialog(page).getByTestId("modal-mcp-one-click").click();
    await expect(dialog(page).getByText("Check your browser…")).toBeVisible();
    await expect(dialog(page)).toHaveCount(0, { timeout: 10_000 });
    await expect(row(page, "monday")).toContainText("Connected");
  });

  // Jira has two: the sign-in, and the Atlassian token form.
  test("jira: two modes — MCP one-click pane plus the manual token form", async ({ page }) => {
    await connect(page, "jira").click();
    await expect(dialog(page).getByTestId("modal-pane-one")).toBeVisible();
    await expect(dialog(page).getByTestId("modal-mcp-one-click")).toBeVisible();
    await dialog(page).getByTestId("modal-pane-manual").click();
    await expect(dialog(page).getByText("Atlassian site URL")).toBeVisible();
    await expect(dialog(page).getByText("API token")).toBeVisible();
  });

  test("monday detail page shows the pinned tool subset with approval badges", async ({ page }) => {
    await row(page, "monday").click();
    await expect(page.getByText("2 tools this connector adds")).toBeVisible();
    await page.getByTestId("available-tools-toggle").click();
    await expect(page.getByText("Read board", { exact: true })).toBeVisible();
    await expect(page.getByText("Create item", { exact: true })).toBeVisible();
  });
});
