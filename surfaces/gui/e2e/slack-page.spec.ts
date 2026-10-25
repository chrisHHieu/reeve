// Slack's page under Connectors. Reeve connects through the person's own Slack app in
// Socket Mode: one workspace, one flat list of who may write. On it: people waiting to be allowed
// (Allow & deliver, Allow, ✕), the sessions listening in a channel, the user token, and people
// picked from the workspace's directory, a read the install's scopes already cover, so nothing
// new is asked of the person.
import { expect, type Page } from "@playwright/test";
import { test, openConnectors } from "./fixtures";

const card = (page: Page) => page.getByTestId("slack-manual-card");

/** The Connectors list, and Slack's page from it unless `listOnly`. */
async function slackPage(page: Page, listOnly = false) {
  await page.goto("/");
  await openConnectors(page);
  if (!listOnly) await page.getByTestId("connector-slack").click();
}

/** Slack's page, with the people picker open. */
async function pickPeople(page: Page) {
  await slackPage(page);
  await page.getByTestId("add-person").click();
  return page.getByTestId("person-picker");
}

test("list row status + navigation to the Slack page", async ({ page }) => {
  await slackPage(page, true);
  const row = page.getByTestId("connector-slack");
  await expect(row).toContainText("acme.example");
  await expect(row).toContainText("Live");
  await row.click();
  await expect(page.getByTestId("slack-page")).toBeVisible();
  await expect(page.getByTestId("slack-mode-badge")).toContainText("Socket Mode");
});

// A prompt is answered in its conversation. There is no list of who approves, and so
// nobody is kept on the list for being one.
test("the Slack page names who may message, and nobody who may approve from Slack", async ({ page }) => {
  await slackPage(page);
  await expect(card(page)).toContainText("People");
  for (const gone of ["Approvals", "owner"]) await expect(card(page)).not.toContainText(gone);
  await card(page).getByTitle("remove", { exact: true }).click();
  await expect(card(page)).toContainText("nobody yet");
});

test("a parked sender: Allow & deliver adds them to the allow-list", async ({ page }) => {
  await slackPage(page);
  const waiting = card(page).getByTestId("waiting-pk1");
  for (const said of ["Maya", "in #reeve-test", "hey reeve, can you summarize this thread?"]) await expect(waiting).toContainText(said);
  await page.getByTestId("parked-allow-deliver-pk1").click();
  await expect(page.getByTestId("waiting-pk1")).toHaveCount(0);
  await expect(card(page)).toContainText("U0NEW");
});

test("parked sender can be dismissed without allowing", async ({ page }) => {
  await slackPage(page);
  await page.getByTestId("parked-dismiss-pk1").click();
  await expect(page.getByTestId("waiting-pk1")).toHaveCount(0);
  await expect(card(page)).not.toContainText("U0NEW");
});

test("sessions listening in a channel: listed with unsubscribe", async ({ page }) => {
  await slackPage(page);
  const listening = card(page).getByTestId("listening-slack");
  await expect(listening).toContainText("Weekly plan 1");
  await expect(listening).toContainText("#reeve-test");
  // Its last session gone, the row goes too.
  await card(page).getByTitle("Unsubscribe this session").click();
  await expect(listening).toHaveCount(0);
});

test("the user token is added, shown only as present, and removed", async ({ page }) => {
  await slackPage(page);
  const token = page.getByTestId("slack-user-token");
  const input = page.getByTestId("slack-user-token-input");
  await expect(token).toContainText("Add a user token");
  await input.fill("xoxp-abc");
  await page.getByTestId("slack-user-token-save").click();
  await expect(token).toContainText("A user token is set");
  await expect(input).toHaveValue("");
  await page.getByTestId("slack-user-token-remove").click();
  await expect(token).toContainText("Add a user token");
});

test("people picker: type a name, pick it, chip lands with the display name", async ({ page }) => {
  const picker = await pickPeople(page);
  await picker.getByPlaceholder("Type a name…").fill("sa");
  await page.getByTestId("pick-person-U8SAMOK").click();
  // The name shows at once, before the person has written. Read once the picker is gone, since
  // its own list has the name too.
  await expect(page.getByTestId("person-picker")).toHaveCount(0);
  await expect(card(page)).toContainText("Sam Okafor");
});

test("people picker: guests are tagged, allowed users drop out of the list", async ({ page }) => {
  const picker = await pickPeople(page);
  await expect(picker.getByTestId("pick-person-U7CAL")).toContainText("guest");
  await picker.getByPlaceholder("Type a name…").fill("maya");
  await picker.getByTestId("pick-person-U9MAYA").click();
  await expect(page.getByTestId("person-picker")).toHaveCount(0);
  await expect(card(page)).toContainText("Maya Chen");
  // Allowed now, she is not offered again.
  await page.getByTestId("add-person").click();
  await expect(page.getByTestId("person-picker")).toBeVisible();
  await expect(page.getByTestId("pick-person-U9MAYA")).toHaveCount(0);
  await expect(page.getByTestId("pick-person-U8SAMOK")).toBeVisible();
});
