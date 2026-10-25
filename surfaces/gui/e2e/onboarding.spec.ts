// First-run setup: a model, then the person's tools, then go. The model step is a
// gallery of providers, each saying its own state, whose card opens its key form in a region of
// fixed height; Test checks the key, saves it and goes back. The tools step makes the case for
// each tool and connects in one click. It is opened here the way it is opened again later, from
// Settings ▸ General ▸ "Run setup again", which is tested by the same path.
import { expect, type Page } from "@playwright/test";
import { test } from "./fixtures";
import { openSettings } from "./steps";

test.beforeEach(async ({ page }) => {
  await page.goto("/");
  await openSettings(page);
  await page.getByRole("button", { name: "Run setup again" }).click();
  await expect(page.getByTestId("ob-step-model")).toBeVisible();
});

const card = (page: Page, provider: string) => page.getByTestId(`ob-provider-${provider}`);
const keyField = (page: Page) => page.getByTestId("ob-field-api_key");

/** From the model step on to the tools step. */
async function toTools(page: Page) {
  await page.getByTestId("ob-continue").click();
  await expect(page.getByTestId("ob-step-tools")).toBeVisible();
}

test("provider gallery: cards wear their state; Next arms off stored credentials", async ({ page }) => {
  // Each card says its state before anything is clicked: "is OpenAI connected already?" is
  // answered by the gallery (2026-07-16).
  await expect(card(page, "openai")).toContainText("✓ Connected");
  await expect(card(page, "anthropic")).toContainText("✓ Connected");
  await expect(card(page, "zai")).toContainText("Not set up");
  await expect(card(page, "ollama")).toContainText("No key needed");
  // The best known first: Anthropic, then OpenAI, then the OpenAI-compatible ones.
  const order = await page.getByTestId("ob-provider-gallery").locator("[data-testid^=ob-provider-]")
    .evaluateAll((cards) => cards.map((c) => c.getAttribute("data-testid")));
  expect(order.indexOf("ob-provider-anthropic")).toBeLessThan(order.indexOf("ob-provider-openai"));
  expect(order.indexOf("ob-provider-openai")).toBeLessThan(order.indexOf("ob-provider-zai"));

  // A provider already set up is enough for Next, with no form to visit.
  await expect(page.getByTestId("ob-continue")).toBeEnabled();
  await toTools(page);
});

test("key form: Test verifies, saves, and returns to the gallery with the ✓", async ({ page }) => {
  await card(page, "zai").click();
  // The frame stays still: the welcome is still there above the form.
  await expect(page.getByRole("heading", { name: "Welcome to Reeve" })).toBeVisible();
  // The endpoint, which needs no change, is behind a quiet link with no explanation (owner, 2026-07-18).
  await expect(page.getByTestId("ob-field-base_url")).toHaveCount(0);
  await page.getByTestId("ob-endpoint-link").click();
  await expect(page.getByTestId("ob-field-base_url")).toHaveValue(/api\.z\.ai/);

  // A bad key is a line under the form, not a change of page.
  await keyField(page).fill("bad-key");
  await page.getByTestId("ob-test").click();
  await expect(page.getByText("Invalid API key.")).toBeVisible();

  // A good one is said in the field, then the form goes back to the gallery by itself, where Z AI
  // now says it is connected.
  await keyField(page).fill("zk-good");
  await page.getByTestId("ob-test").click();
  await expect(page.getByTestId("ob-saved-pill")).toBeVisible();
  await expect(card(page, "zai")).toContainText("✓ Connected", { timeout: 5_000 });
  await expect(page.getByTestId("ob-continue")).toBeEnabled();
});

test("key form: revisiting a connected provider shows the in-field saved state; drafts survive switching", async ({ page }) => {
  // A provider set up shows its kept key in the field, masked. An empty password field read as
  // "not set up" (owner, 2026-07-16).
  await card(page, "openai").click();
  await expect(page.getByTestId("ob-saved-pill")).toBeVisible();
  await expect(keyField(page)).toHaveAttribute("placeholder", "••••••••");

  // A key typed and not saved is still there after looking at another provider.
  const visit = async (provider: string) => {
    await page.getByTestId("ob-back").click();
    await card(page, provider).click();
  };
  await visit("zai");
  await keyField(page).fill("zk-draft");
  await visit("openai");
  await expect(page.getByTestId("ob-saved-pill")).toBeVisible();
  await visit("zai");
  await expect(keyField(page)).toHaveValue("zk-draft");

  // Next from a form with a new key checks and saves it first, then moves on: no separate Test
  // to remember before Continue (2026-07-12).
  await keyField(page).fill("zk-good");
  await toTools(page);
});

test("tools page: the benefit rows connect through the connector sheet", async ({ page }) => {
  await toTools(page);
  // Each row says what the tool is for. Connecting is optional and never stands in the way.
  await expect(page.getByText("Chat can only advise")).toBeVisible();
  const row = (tool: string) => page.getByTestId(`ob-tool-${tool}`);
  await expect(row("slack")).toContainText("Keep up with Slack");
  await expect(row("slack")).toContainText("✓ Connected");
  await expect(row("notion")).toContainText("Keep your notes in reach");
  await expect(row("attio")).toHaveCount(0);
  await expect(page.getByTestId("ob-continue-tools")).toBeEnabled();

  // Connect opens the Connectors page's own dialog; the sign-in finishes in the mock's browser,
  // and the row, which keeps asking, turns connected.
  await row("notion").getByRole("button", { name: "Connect" }).click();
  await page.getByTestId("modal-mcp-one-click").click();
  await page.keyboard.press("Escape");
  await expect(row("notion")).toContainText("✓ Connected", { timeout: 10_000 });
  await page.getByTestId("ob-continue-tools").click();

  // The last step's automation button goes to the Automations quickstart.
  await expect(page.getByTestId("ob-step-done")).toBeVisible();
  await page.getByTestId("ob-cta-automation").click();
  await expect(page.getByTestId("onboarding")).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Automations" })).toBeVisible();
});

test("tools page skips cleanly; Start working lands in a session", async ({ page }) => {
  await page.getByTestId("ob-continue").click();
  await page.getByTestId("ob-continue-tools").click();
  await expect(page.getByTestId("ob-step-done")).toBeVisible();
  await page.getByTestId("ob-start").click();
  await expect(page.getByTestId("onboarding")).toHaveCount(0);
  // A new session's composer, ready to type in.
  await expect(page.getByPlaceholder(/Ask anything/)).toBeVisible();
});
