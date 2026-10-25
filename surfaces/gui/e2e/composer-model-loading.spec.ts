import { test, expect } from "./fixtures";

// The composer offers only models the server has confirmed. Until /v1/settings brings its list (a
// cold start can beat the sidecar), the picker is a disabled "Loading models…" chip. A built-in
// list used to stand in, which went stale and offered ids that did not exist (owner, 2026-07-21).
test("picker shows a disabled Loading-models chip until the list arrives", async ({ page }) => {
  const noModelsYet = { model: "gpt-5.5", models: [], model_labels: {}, has_key: true, model_ready: true, onboarded: true, nav_layout: "flat" };
  await page.route("**/v1/settings", (route) => route.fulfill({ json: noModelsYet }));
  await page.goto("/");
  const loading = page.getByTestId("models-loading");
  await expect(loading).toBeVisible();
  await expect(loading).toBeDisabled();
  await expect(loading).toContainText("Loading models…");
});
