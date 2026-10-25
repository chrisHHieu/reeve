// Settings and the composer's picker say the same about models (owner, 2026-10-21: Settings said 6
// models were in the picker and the picker listed 33, by raw id in one place and by name in the
// other, with the provider's name cut short). The catalogue here is the real route's shape.
import { expect } from "@playwright/test";
import { test } from "./fixtures";

const row = (id: string, name: string, provider: string, title: string, extra: Record<string, unknown> = {}) => ({
  id,
  name,
  provider,
  provider_title: title,
  context: 1_000_000,
  cost: { input: 0.15, output: 0.6 },
  ...extra,
});

const CATALOG = {
  default: "anthropic:claude-opus-4-8",
  models: [
    row("anthropic:claude-opus-4-8", "Claude Opus 4.8", "anthropic", "Claude subscription (Pro / Max)"),
    row("gpt-5.5", "GPT-5.5", "openai", "OpenAI"),
    row("gpt-4o", "GPT-4o", "openai", "OpenAI"),
    row("gpt-4o-mini", "GPT-4o mini", "openai", "OpenAI"),
    // Marked deprecated by its provider (models.dev's `status`).
    row("o3-mini", "o3-mini", "openai", "OpenAI", { status: "deprecated" }),
    // In the catalogue, not turned on in Settings.
    row("gpt-6-luna", "GPT-6 Luna", "openai", "OpenAI", { free: true }),
    row("gpt-6-sol", "GPT-6 Sol", "openai", "OpenAI"),
  ],
};

test("Settings and the picker name, count and describe the same models", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.route("**/v1/models/catalog", (route) => route.fulfill({ json: CATALOG }));
  await page.goto("/");

  // The picker: only what Settings turned on, counted the same, the provider's name whole.
  await page.getByTestId("model-picker-trigger").click();
  await page.getByTestId("provider-row-all").click();
  await expect(page.getByTestId("provider-row-all")).toContainText("5");
  await expect(page.getByTestId("provider-row-openai")).toContainText("4");
  await expect(page.getByTestId("provider-row-anthropic")).toHaveText(/Claude subscription\s*1/);
  await expect(page.getByTestId("model-row-gpt-6-luna")).toHaveCount(0);
  await expect(page.getByTestId("model-row-gpt-4o")).toContainText("GPT-4o");
  await expect(page.getByTestId("model-row-gpt-4o")).toContainText("1M · $0.15 / $0.6");
  // A model its provider marks deprecated says so, here and in Settings; the others do not.
  await expect(page.getByTestId("model-row-o3-mini").getByTestId("model-retiring-o3-mini")).toHaveText("Retiring");
  await expect(page.getByTestId("model-picker-panel").getByText("Retiring")).toHaveCount(1);
  // The rest are one click away; picking one turns it on.
  await page.getByTestId("model-picker-more").click();
  await expect(page.getByTestId("model-row-gpt-6-luna")).toContainText("Free");
  await page.getByTestId("model-picker-panel").screenshot({ path: test.info().outputPath("picker-all.png") });
  await page.keyboard.press("Escape");

  // Settings ▸ Models: the same names, the same facts, under the same provider names.
  await page.getByTestId("rail-settings").click();
  await page.getByRole("button", { name: "Models", exact: true }).click();
  const card = page.getByTestId("composer-picker");
  await expect(card.getByTestId("composer-picker-group-openai").locator(".model-row")).toHaveCount(4);
  await expect(card.getByTestId("composer-picker-group-anthropic")).toContainText("Claude subscription");
  await expect(card.getByTestId("composer-picker-group-anthropic")).not.toContainText("(Pro / Max)");
  await expect(card.getByTestId("model-setting-gpt-4o")).toContainText("GPT-4o");
  // The picker's words exactly, after the model's id.
  await expect(card.getByTestId("model-facts-gpt-4o")).toContainText("1M · $0.15 / $0.6");
  await expect(card.getByTestId("model-setting-o3-mini").getByTestId("model-retiring-o3-mini")).toHaveText("Retiring");
  await expect(card.getByText("Retiring")).toHaveCount(1);

  // A provider's page: the same row, the ones not turned on drawn off.
  await page.getByTestId("set-provider-openai").click();
  await expect(page.getByTestId("model-setting-gpt-4o")).toContainText("GPT-4o");
  await expect(page.getByTestId("model-switch-gpt-4o")).toHaveAttribute("aria-checked", "true");
  await expect(page.getByTestId("model-setting-o3-mini").getByTestId("model-retiring-o3-mini")).toHaveText("Retiring");
  await page.getByTestId("provider-models").screenshot({ path: test.info().outputPath("provider-models.png") });
});
