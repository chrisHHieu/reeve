// Connectors that hold several accounts: Gmail, Google Calendar, and Notion for the generic
// page (AccountsDetail) the rest share. One account is the default, each disconnects on its own,
// and another is added from the page through the connect dialog's local sign-in. Gmail also has
// its "Never show agents" lists, and Notion's dialog a token form beside the sign-in.
import { expect, type Page } from "@playwright/test";
import { test, openConnectors } from "./fixtures";

type AccountPage = {
  connector: string;
  page: string;
  /** A row's test id prefix, and the prefix of its buttons'. */
  row: string;
  buttons: string;
  first: string;
  second: string;
  /** What the list's row says once the first account is in. */
  shown: string;
};

const PAGES: AccountPage[] = [
  { connector: "gmail", page: "gmail-detail", row: "gmail-account-", buttons: "gmail-", first: "sam@gmail.com", second: "work@acme.example", shown: "sam@gmail.com" },
  { connector: "google_calendar", page: "gcal-detail", row: "gcal-account-", buttons: "gcal-", first: "sam@gmail.com", second: "work@acme.example", shown: "sam@gmail.com" },
  { connector: "notion", page: "accounts-detail", row: "account-", buttons: "account-", first: "ws-1", second: "ws-2", shown: "Sam's Workspace" },
];

const listRow = (page: Page, connector: string) => page.getByTestId(`connector-${connector}`);

/** Open the Connectors page. */
async function connectors(page: Page) {
  await page.goto("/");
  await openConnectors(page);
}

/** Connect the first account from the list, in one click. The list hears of a sign-in made in
 *  the browser on its poll, every five seconds. */
async function connectFirst(page: Page, at: AccountPage) {
  await connectors(page);
  await listRow(page, at.connector).getByRole("button", { name: "Connect", exact: true }).click();
  await page.getByTestId("modal-mcp-one-click").click();
  await page.keyboard.press("Escape");
  await expect(listRow(page, at.connector)).toContainText(at.shown, { timeout: 15_000 });
}

/** On the connector's page, add the next account: the sign-in, the account named, the dialog closed. */
async function addSecond(page: Page, at: AccountPage) {
  await listRow(page, at.connector).click();
  await expect(page.getByTestId(at.page)).toBeVisible();
  await page.getByTestId("add-account-btn").click();
  await page.getByTestId("modal-mcp-one-click").click();
  await expect(page.getByTestId("modal-connected")).toBeVisible({ timeout: 10_000 });
  // The page has it on its next poll.
  await expect(page.getByTestId(at.row + at.second)).toBeVisible({ timeout: 15_000 });
}

for (const at of PAGES) {
  test.describe(at.connector, () => {
    test("connect, then add a second account from the page; first stays default", async ({ page }) => {
      await connectFirst(page, at);
      await addSecond(page, at);
      const first = page.getByTestId(at.row + at.first);
      if (at.connector === "notion") await expect(first).toContainText("Sam's Workspace");
      await expect(first).toContainText("Default");
      await expect(page.getByTestId(at.row + at.second)).not.toContainText("Default");
      // The list's row says there are two now.
      await page.getByTestId("connectors-breadcrumb").click();
      await expect(listRow(page, at.connector)).toContainText("2 accounts");
    });

    test("Make default moves the badge; disconnecting the default repoints it", async ({ page }) => {
      await connectFirst(page, at);
      await addSecond(page, at);
      const first = page.getByTestId(at.row + at.first);
      const second = page.getByTestId(at.row + at.second);
      await page.getByTestId(`${at.buttons}make-default-${at.second}`).click();
      await expect(second).toContainText("Default");
      await expect(first).not.toContainText("Default");

      // Disconnecting the default, once confirmed, makes the other one the default.
      await page.getByTestId(`${at.buttons}disconnect-${at.second}`).click();
      await page.getByTestId("confirm-dialog-ok").click();
      await expect(second).toHaveCount(0);
      await expect(first).toContainText("Default");
    });
  });
}

// Senders and labels whose mail the agents never see. Kept by the server, so they are still
// there after a reload, and removing one goes back to it too.
test("gmail: Never show agents: sender + label chips round-trip", async ({ page }) => {
  await connectFirst(page, PAGES[0]);
  await listRow(page, "gmail").click();
  const list = (name: string) => page.getByTestId(`gmail-filter-${name}`);
  for (const [name, value] of [["senders", "ceo@corp.com"], ["labels", "Personal"]]) {
    await list(name).getByRole("textbox").fill(value);
    await list(name).getByRole("textbox").press("Enter");
    await expect(list(name)).toContainText(value);
  }

  await page.reload();
  await connectors(page);
  await listRow(page, "gmail").click();
  await expect(list("senders")).toContainText("ceo@corp.com");
  await list("senders").getByRole("button", { name: "Remove ceo@corp.com" }).click();
  await expect(list("senders")).not.toContainText("ceo@corp.com");
});

// The dialog opens on the sign-in in the browser; the integration's token is the other way in.
test("notion: the modal offers the browser sign-in first and a token form as the manual pane", async ({ page }) => {
  await connectors(page);
  await listRow(page, "notion").getByRole("button", { name: "Connect", exact: true }).click();
  await expect(page.getByTestId("modal-mcp-one-click")).toBeVisible();
  await page.getByTestId("modal-pane-manual").click();
  await expect(page.getByPlaceholder("ntn_…")).toBeVisible();
});
