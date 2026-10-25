// Making an automation: one form. A template is a way to fill it in, not a second form: picking
// one writes the name, the schedule and the instructions, and adds what those instructions
// need (connections, a repository, a channel). There is one Create button, and the reason it
// cannot be pressed yet is said beside it.
import { expect, type Page } from "@playwright/test";
import { test } from "./fixtures";

/** The new automation form, opened from the rail's Automations, with its templates over it. */
async function openForm(page: Page) {
  await page.goto("/");
  await page.getByTestId("rail-automations").click();
  await page.getByRole("button", { name: "New automation" }).click();
  for (const heading of [page.getByRole("heading", { name: "New automation" }), page.getByText("Start from a template")]) {
    await expect(heading).toBeVisible();
  }
}

test("with no template the form is the whole thing: a name and instructions create it", async ({ page }) => {
  await openForm(page);
  const create = page.getByTestId("ob-create");
  await expect(create).toBeDisabled();
  await expect(page.getByTestId("ob-create-hint")).toContainText("Give it a name.");

  await page.getByLabel("Name").fill("PR watch");
  await expect(page.getByTestId("ob-create-hint")).toContainText("Say what it should do.");
  await page.getByLabel("Instructions").fill("Review the pull requests merged today.");
  await expect(page.getByTestId("ob-create-hint")).toHaveText("");
  await create.click();

  // It lands on the new automation's own page.
  await expect(page.getByRole("heading", { name: "PR watch" })).toBeVisible();
  await expect(page.getByText("It has not run yet.")).toBeVisible();
});

test("a template that needs a connection says so beside Create, and connecting it unlocks it", async ({ page }) => {
  await openForm(page);

  // Email digest needs Gmail, which the fixtures leave disconnected. A card shows what its
  // template needs by the connection's own mark: grey until connected, in colour after.
  const card = page.getByTestId("qs-template-emaildigest");
  await expect(card).toContainText("Email digest");
  const mark = card.getByTestId("qs-mark-gmail");
  await expect(mark.locator(".connector-badge")).toHaveAccessibleName("Gmail: not connected yet");
  await expect(mark).toHaveCSS("filter", "grayscale(1)");
  const github = page.getByTestId("qs-template-github");
  await expect(github.locator(".connector-badge")).toHaveCount(2);
  await expect(github.getByTestId("qs-mark-slack").locator(".connector-badge")).toHaveAccessibleName("Slack: connected");
  await expect(github.getByTestId("qs-mark-github").locator(".connector-badge")).toHaveAccessibleName("GitHub: connected");
  await expect(github.getByTestId("qs-mark-github")).toHaveCSS("filter", "none");
  await expect(page.getByTestId("qs-template-news").locator(".connector-badge")).toHaveCount(0);
  await card.click();
  await expect(card).toHaveAttribute("aria-pressed", "true");
  // The template filled the one form in.
  await expect(page.getByLabel("Name")).toHaveValue("Email digest");
  await expect(page.getByLabel("Instructions")).toHaveValue("Summarize my unread email into one short digest note.");
  await expect(page.getByRole("button", { name: "Repeats" })).toContainText("Weekdays");
  await expect(page.getByLabel("At", { exact: true })).toHaveValue("09:00");

  await expect(page.getByTestId("ob-create")).toBeDisabled();
  await expect(page.getByTestId("ob-create-hint")).toContainText("Connect Gmail to create this.");

  // Connect opens the same sheet the Connectors page uses; the local sign-in finishes at
  // once in the mock, and the form's own read of the connections sees it.
  await page.getByTestId("ob-connect-gmail").click();
  await page.getByTestId("modal-mcp-one-click").click();
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("qs-needs")).toContainText("Connected", { timeout: 15_000 });
  await expect(mark).toHaveAttribute("data-connected", "true");
  await expect(mark).toHaveCSS("filter", "none");
  await expect(page.getByTestId("ob-create")).toBeEnabled();
});

// Posting needs a channel and the person's leave. With the connections in place, the missing
// channel is what Create waits on; it is picked from the channels heard from lately, and the
// consent, ticked by default, becomes the automation's standing approval to post there.
test("a template that posts: the channel is picked by name, and the consent mints the grant", async ({ page }) => {
  await openForm(page);
  await page.getByTestId("qs-template-github").click();
  await expect(page.getByTestId("qs-needs").getByText("Connected").first()).toBeVisible();
  const reason = page.getByTestId("ob-create-hint");
  await expect(reason).toContainText("Pick a channel");

  const channel = page.getByTestId("ob-channel").locator("input");
  await channel.click();
  await page.getByTestId("channel-suggestions").getByText("#reeve-test").click();
  await expect(channel).toHaveValue("#reeve-test");
  // The instructions follow the fields over them.
  await page.getByTestId("ob-repo").fill("acme/agent-platform");
  await expect(page.getByLabel("Instructions")).toHaveValue(/acme\/agent-platform/);
  const consent = page.getByTestId("ob-consent");
  await expect(consent).toBeChecked();
  await page.getByTestId("ob-create").click();

  // Its page, with the grant the consent made.
  await expect(page.getByRole("heading", { name: "GitHub digest" })).toBeVisible();
  await expect(page.getByRole("button", { name: /Run now/ })).toBeVisible();
  await expect(page.getByTestId("task-grants")).toContainText("send_message");
});

// Reading is never asked about, so a template that only reads has no consent to give, and the
// form says that is why.
test("a template that only reads carries no consent, and says why", async ({ page }) => {
  await openForm(page);
  await page.getByTestId("qs-template-brief").click();
  for (const part of ["Today's meetings and gaps", "What arrived overnight", "Reading never needs your approval."]) {
    await expect(page.getByText(part)).toBeVisible();
  }
  await expect(page.getByTestId("ob-consent")).toHaveCount(0);
});

test("a template that needs nothing can be created at once; what the person typed is kept when it is let go", async ({
  page,
}) => {
  await openForm(page);
  const card = page.getByTestId("qs-template-news");
  await expect(card).toContainText("No connection needed");
  await card.click();
  await expect(page.getByTestId("qs-needs")).toHaveCount(0);
  await expect(page.getByTestId("ob-consent")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Repeats" })).toContainText("Every day");

  // Instructions the person edits are theirs: letting the template go does not take them back.
  await page.getByLabel("Instructions").fill("Five points of AI news, as Markdown.");
  await card.click();
  await expect(card).toHaveAttribute("aria-pressed", "false");
  await expect(page.getByLabel("Instructions")).toHaveValue("Five points of AI news, as Markdown.");

  await page.getByLabel("Name").fill("AI news");
  await page.getByTestId("ob-create").click();
  await expect(page.getByRole("heading", { name: "AI news" })).toBeVisible();
});
