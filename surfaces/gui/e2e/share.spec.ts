// `/share`: this app has no hosted sharing service. The composer's row
// uploads to the person's configured `[share] endpoint`, copies the returned URL, and
// `/unshare` appears only when the endpoint can revoke. With no endpoint the row is
// disabled and says exactly why — never a pretend cloud.
import { expect } from "@playwright/test";
import { test } from "./fixtures";

test("/share uploads, copies the link, and /unshare revokes", async ({ page }) => {
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();
  const box = page.getByPlaceholder(/Ask anything/);

  await box.fill("/share");
  const shareRow = page.getByTestId("slash-command-share");
  await expect(shareRow).toBeEnabled();
  await shareRow.click();
  // Clipboard behavior varies by environment; the toast is the same either way.
  await expect(page.getByText(/Session shared/).first()).toBeVisible({ timeout: 10_000 });

  await box.fill("/unshare");
  const unshareRow = page.getByTestId("slash-command-unshare");
  await expect(unshareRow).toBeEnabled();
  await unshareRow.click();
  await expect(page.getByText("Session unshared").first()).toBeVisible({ timeout: 10_000 });

  // After the revoke the row is informational again, not a button that cannot work.
  await box.fill("/unshare");
  await expect(page.getByTestId("slash-command-unshare")).toBeDisabled();
});

test("without a configured endpoint the /share row is disabled with the reason", async ({
  page,
}) => {
  // Later routes win: play the unconfigured harness for this spec.
  await page.route(/\/v1\/sessions\/[^/]+\/share$/, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ configured: false, can_revoke: false, share: null }),
    }),
  );
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();
  const box = page.getByPlaceholder(/Ask anything/);

  await box.fill("/share");
  const row = page.getByTestId("slash-command-share");
  await expect(row).toBeDisabled();
  await expect(row).toContainText("Sharing needs a configured endpoint");

  await box.fill("/unshare");
  await expect(page.getByTestId("slash-command-unshare")).toBeDisabled();
});
