// Keys in Settings ▸ Models (the settings redesign of 2026-10-20). A provider is added from
// the Add provider window: a bad key fails where it was typed, a good one is checked, saved, and
// the window closes on it in Connected, whose page then shows the key as kept. The mock's check
// fails any key with "bad" in it, and saving sets `configured`.
import { expect, type Page } from "@playwright/test";
import { test } from "./fixtures";
import { openSettings } from "./steps";

test.beforeEach(async ({ page }) => {
  await page.goto("/");
  await openSettings(page, "Models");
  await expect(page.getByTestId("set-provider-openai")).toBeVisible();
});

const provider = (page: Page, name: string) => page.getByTestId(`set-provider-${name}`);
const addWindow = (page: Page) => page.getByTestId("add-provider");
const keyField = (page: Page) => page.getByTestId("set-field-api_key");

/** Open the Add provider window, which lists the providers not connected yet. */
async function addProvider(page: Page) {
  await page.getByTestId("models-add-provider").click();
  await expect(addWindow(page)).toBeVisible();
}

test("a bad key fails in place; a good key saves, closes the window and lands in Connected", async ({ page }) => {
  await addProvider(page);
  await provider(page, "zai").click();
  await keyField(page).fill("sk-bad-key");
  await page.getByTestId("set-test").click();
  await expect(page.getByText("Invalid API key.")).toBeVisible();
  await expect(addWindow(page)).toBeVisible();

  // Connect checks and saves in one: said in the field, then the window closes.
  await keyField(page).fill("sk-glm-realkey");
  await page.getByTestId("set-test").click();
  await expect(page.getByTestId("set-saved-pill")).toContainText("Tested & saved");
  await expect(addWindow(page)).toHaveCount(0, { timeout: 5_000 });
  await expect(provider(page, "zai")).toContainText("✓ Connected");

  // Its page shows the key as kept, not the typed key put back as a draft (owner, 2026-07-19),
  // and Replace key opens an empty field.
  await provider(page, "zai").click();
  await expect(page.getByTestId("provider-key")).toContainText("Saved on this computer");
  await page.getByTestId("set-replace").click();
  await expect(keyField(page)).toHaveValue("");
  await expect(keyField(page)).toHaveAttribute("placeholder", "••••••••");
});

test("a configured provider's page shows the key as kept, checks it again, and replaces it", async ({ page }) => {
  await provider(page, "openai").click();
  // One heading, the provider's, under a Models crumb.
  await expect(page.locator(".provider-title")).toHaveText("OpenAI");
  await expect(page.locator(".settings-section-title")).toBeHidden();
  // A kept key never comes back as text, nor as a field to type over.
  await expect(page.getByTestId("provider-key")).toContainText("Saved on this computer");
  await expect(keyField(page)).toHaveCount(0);

  await page.getByTestId("set-check").click();
  await expect(page.getByTestId("provider-key")).toContainText("checked just now");
  await page.getByTestId("set-replace").click();
  await expect(page.getByTestId("set-saved-pill")).toContainText("Tested & saved");
  await page.getByTestId("set-replace-cancel").click();
  await expect(keyField(page)).toHaveCount(0);
  await page.getByTestId("set-back").click();
  await expect(provider(page, "openai")).toBeVisible();
});

// Seen by the owner on 2026-07-23: Test was the form's only way to save, so a field that is not
// a secret, typed into before leaving Settings, was lost. Leaving the field saves it.
test("non-secret fields blur-save on a configured provider (ollama endpoint)", async ({ page }) => {
  await addProvider(page);
  await provider(page, "ollama").click();
  const endpoint = page.getByTestId("set-field-base_url");
  await endpoint.fill("http://127.0.0.1:9999");
  await endpoint.blur();
  await expect(page.getByTestId("set-field-saved-base_url")).toBeVisible();
  // Kept with the provider: there again after going back and in.
  await page.getByTestId("set-back").click();
  await provider(page, "ollama").click();
  await expect(page.getByTestId("set-field-base_url")).toHaveValue("http://127.0.0.1:9999");
});

test("the subscription provider signs in from the browser flow, no key form", async ({ page }) => {
  await addProvider(page);
  const codex = provider(page, "openai-codex");
  await expect(codex).toContainText("Sign in with your plan");
  await codex.click();
  // No key to type: the step says what signing in does, and has its button.
  await expect(keyField(page)).toHaveCount(0);
  await expect(addWindow(page)).toContainText("Your browser opens");
  await page.getByTestId("set-oauth-signin").click();

  // The mock finishes the sign-in on the first poll; the window closes on it connected.
  await expect(addWindow(page)).toHaveCount(0, { timeout: 8_000 });
  await codex.click();
  await expect(page.getByTestId("set-oauth-account")).toContainText("sam@example.com");

  // Signed out, it leaves Connected and is back among those to add.
  await page.getByTestId("set-oauth-signout").click();
  await expect(page.getByTestId("set-oauth-signin")).toBeVisible();
  await page.getByTestId("set-back").click();
  await expect(codex).toHaveCount(0);
  await addProvider(page);
  await expect(codex).toContainText("Sign in with your plan");
});
