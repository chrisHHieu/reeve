import type { Page } from "@playwright/test";
import { test, expect } from "./fixtures";
import { bodySent, openSettings } from "./steps";

// Settings: a page of its own that takes the whole window, its rail and search, and the pages
// under it (Instructions, Models, Advanced, General).

const railItem = (page: Page, name: string) => page.locator(".settings-rail-item", { hasText: name });

/** Models' Add provider window, which lists the providers not connected yet (2026-10-20). */
async function addProvider(page: Page) {
  await page.getByTestId("models-add-provider").click();
  await expect(page.getByTestId("add-provider")).toBeVisible();
}

test.beforeEach(async ({ page }) => {
  await page.goto("/");
});

// A page, not a dialog, with General, Instructions, Models and Advanced down its side.
// Files is a card on General, and there is no agents page: there is one agent (2026-10-12).
test("Settings opens as a full page and navigates sections", async ({ page }) => {
  await openSettings(page);
  await expect(page.getByRole("heading", { name: "General" })).toBeVisible();
  await expect(page.locator(".modal-backdrop")).toHaveCount(0);
  for (const name of ["General", "Instructions", "Models", "Advanced"]) {
    await expect(page.getByRole("button", { name, exact: true })).toBeVisible();
  }
  for (const gone of ["Files", "Agents"]) await expect(page.getByRole("button", { name: gone, exact: true })).toHaveCount(0);
  await expect(page.getByTestId("files-card")).toBeVisible();
  await expect(page.getByTestId("scratch-base")).toBeVisible();

  await page.getByRole("button", { name: "Models", exact: true }).click();
  await expect(page.getByTestId("set-provider-openai")).toBeVisible();
});

// The app's sidebar leaves while Settings is open. Its rail has search, groups and Back to app.
test("Settings is a full-window takeover with a searchable grouped rail", async ({ page }) => {
  await openSettings(page);
  await expect(page.getByTestId("settings-shell")).toBeVisible();
  await expect(page.locator(".sidebar")).toHaveCount(0);
  for (const group of ["App", "Agent", "Connections"]) {
    await expect(page.locator(".settings-rail-group-label", { hasText: group })).toBeVisible();
  }
  await expect(page.getByTestId("settings-back")).toBeVisible();

  // A search finds the page a setting is on and opens at the setting. The theme and fonts have a
  // page of their own, Appearance.
  await page.getByLabel("Search settings").fill("palette");
  await expect(railItem(page, "Appearance")).toBeVisible();
  await expect(railItem(page, "Models")).toHaveCount(0);
  await railItem(page, "Appearance").click();
  await expect(page.locator('[data-settings-title="Theme"]').first()).toBeVisible();

  await page.getByTestId("settings-back").click();
  await expect(page.getByTestId("settings-shell")).toHaveCount(0);
  await expect(page.locator(".sidebar")).toBeVisible();

  // Escape is a way back too, except while typing in a field or with a menu open.
  await openSettings(page);
  await page.getByLabel("Search settings").fill("font");
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("settings-shell")).toBeVisible();
  await page.getByLabel("Search settings").fill("");
  await railItem(page, "Appearance").click();
  await page.getByTestId("font-picker-ui").click();
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("settings-shell")).toBeVisible();
  await page.locator(".settings-section-title").click();
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("settings-shell")).toHaveCount(0);
});

// Instructions, a page of the Agent group, edits the global SOUL.md. It is saved as the person
// pauses, with no Save button to forget (2026-10-20).
test("Instructions edits the global SOUL.md", async ({ page }) => {
  await openSettings(page, "Instructions");
  const editor = page.getByTestId("instructions-input");
  await expect(editor).toBeVisible();
  expect(await bodySent(page, "PUT", "/v1/memory/settings", () => editor.fill("Be brief."))).toEqual({ user_rules: "Be brief." });
  await expect(page.getByTestId("instructions-status")).toContainText("Saved");

  // A starter adds its line, with the words still to write selected to type over.
  await page.getByRole("button", { name: "Date and number format" }).click();
  await expect(editor).toHaveValue("Be brief.\nWrite dates as DD-MM-YYYY.");
});

// Each provider says its state. One not set up opens its key form, with its endpoint filled in
// behind a disclosure and the models it would bring listed.
test("Models: provider gallery states; vendor form previews models", async ({ page }) => {
  await openSettings(page, "Models");
  await expect(page.getByTestId("set-provider-openai")).toContainText("✓ Connected · used 2h ago");
  await expect(page.getByTestId("set-provider-anthropic")).toContainText("✓ Connected");
  await addProvider(page);
  await expect(page.getByTestId("add-connected-openai")).toContainText("✓ Connected");
  await expect(page.getByTestId("set-provider-zai")).toContainText("Not set up");
  await expect(page.getByTestId("set-provider-ollama")).toContainText("No key needed");

  await page.getByTestId("set-provider-zai").click();
  await expect(page.getByText(/Uses Z AI's OpenAI-compatible API/)).toBeVisible();
  await page.getByTestId("set-endpoint-link").click();
  await expect(page.getByTestId("set-field-base_url")).toHaveValue("https://api.z.ai/api/paas/v4");
  const preview = page.getByTestId("model-preview");
  await expect(preview).toContainText("Models it brings");
  await expect(preview).toContainText("GLM-5.2 · Z AI");

  // Back to the list; Escape closes the window, and the picker's card is on the page behind it.
  await page.getByTestId("set-back").click();
  await expect(page.getByTestId("set-provider-zai")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("add-provider")).toHaveCount(0);
  await expect(page.getByTestId("composer-picker")).toContainText("In the composer's picker");
});

// The two Ark providers are told apart: their own marks, endpoints and models.
test("Models: BytePlus and Volcengine Ark stay visually and operationally separate", async ({ page }) => {
  await openSettings(page, "Models");
  await addProvider(page);
  const ark = [
    { id: "ark", title: "BytePlus Ark", endpoint: "https://ark.ap-southeast.bytepluses.com/api/v3", models: ["Dola Seed Evolving · BytePlus Ark", "Dola Seed 2.1 Turbo · BytePlus Ark"], not: "Doubao Seed" },
    { id: "ark-agent-plan-cn", title: "Volcengine Ark Agent Plan", endpoint: "https://ark.cn-beijing.volces.com/api/plan/v3", models: ["Doubao Seed Evolving · Volcengine Agent Plan", "Doubao Seed 2.1 Turbo · Volcengine Agent Plan"], not: "Dola Seed" },
  ];
  const marks: (string | null)[] = [];
  for (const provider of ark) {
    const card = page.getByTestId(`set-provider-${provider.id}`);
    await expect(card).toContainText(provider.title);
    marks.push(await card.locator("img").getAttribute("src"));
  }
  expect(marks[0]).toBeTruthy();
  expect(marks[1]).toBeTruthy();
  expect(marks[0]).not.toBe(marks[1]);

  for (const [i, provider] of ark.entries()) {
    if (i > 0) await page.getByTestId("set-back").click();
    await page.getByTestId(`set-provider-${provider.id}`).click();
    await page.getByTestId("set-endpoint-link").click();
    await expect(page.getByTestId("set-field-base_url")).toHaveValue(provider.endpoint);
    const preview = page.getByTestId("model-preview");
    for (const model of provider.models) await expect(preview).toContainText(model);
    await expect(preview).not.toContainText(provider.not);
  }
});

// A provider with a key kept shows that, with no field to type in. Remove key, in its page's ⋯,
// takes it out of Connected; it is back among those to add, not set up.
test("Models: Remove key reverts a configured provider", async ({ page }) => {
  page.on("dialog", (dialog) => dialog.accept());
  await openSettings(page, "Models");
  await page.getByTestId("set-provider-anthropic").click();
  await expect(page.getByTestId("provider-key")).toContainText("Saved on this computer");
  await expect(page.getByTestId("set-field-api_key")).toHaveCount(0);
  await page.getByTestId("provider-menu").click();
  await page.getByTestId("set-remove-key").click();

  await expect(page.getByTestId("set-provider-anthropic")).toHaveCount(0);
  await addProvider(page);
  await expect(page.getByTestId("set-provider-anthropic")).toContainText("Not set up");
});

// How PDFs are sent (owner, 2026-07-17 and 2026-08-21; on Advanced since 2026-10-10): text or
// page images, and the sizes past which a PDF is not attached whole. Each change is sent.
test("Settings: Token savings card edits PDF fallback and thresholds", async ({ page }) => {
  await openSettings(page, "Advanced");
  const card = page.getByTestId("token-savings-card");
  await expect(card).toBeVisible();
  await expect(card.getByText("PDF attachments")).toBeVisible();

  const fallback = page.getByTestId("pdf-fallback");
  const images = fallback.getByRole("button", { name: "Send page images" });
  await expect(fallback.getByRole("button", { name: "Extract text" })).toHaveClass(/active/);
  expect(await bodySent(page, "POST", "/v1/settings/pdf", () => images.click())).toEqual({ pdf_fallback: "images" });
  await expect(images).toHaveClass(/active/);

  await expect(card.getByTestId("pdf-max-pages")).toHaveValue("2");
  await expect(card.getByTestId("pdf-max-mb")).toHaveValue("10");
  expect(await bodySent(page, "POST", "/v1/settings/pdf", () => card.getByTestId("pdf-max-pages").fill("30"))).toEqual({ pdf_max_pages: 30 });
});

// Choices on General that the server hands to the next session it opens: the permission mode
// (owner, 2026-09-27) and the shell commands run in (2026-10-14), Automatic or one the
// machine has.
test("General sets the permission mode new conversations start in", async ({ page }) => {
  await openSettings(page);
  const card = page.getByTestId("default-mode-card");
  await expect(card).toBeVisible();
  await expect(card.getByTestId("default-mode-workspace-write")).toHaveAttribute("aria-checked", "true");
  const full = card.getByTestId("default-mode-danger-full-access");
  expect(await bodySent(page, "POST", "/v1/settings/default-mode", () => full.click())).toEqual({ mode: "danger-full-access" });
  await expect(full).toHaveAttribute("aria-checked", "true");
});

test("General sets the shell new conversations run commands in", async ({ page }) => {
  await openSettings(page);
  const card = page.getByTestId("shell-card");
  await expect(card).toBeVisible();
  await expect(card.getByTestId("shell-auto")).toHaveAttribute("aria-checked", "true");
  await expect(card.getByTestId("shell-cmd")).toHaveCount(0);
  const bash = card.getByTestId("shell-bash");
  expect(await bodySent(page, "POST", "/v1/settings/shell", () => bash.click())).toEqual({ shell: "bash" });
  await expect(bash).toHaveAttribute("aria-checked", "true");
});
