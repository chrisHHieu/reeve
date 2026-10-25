import { test, expect } from "./fixtures";

// Batch 3/P2 — install from a community repo (`npx skills add`): the mandatory review gate
// (the staged preview installs NOTHING), then confirm lands the skill with its provenance
// badge and a remove affordance. Hermetic: stage/confirm round-trip through fixtures.ts.

test("skills-install: source → staged preview → confirm → provenance badge", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("rail-settings").click();
  await page.getByRole("button", { name: "Skills", exact: true }).click();

  // Add skill ▾ → Install from repo… → the source dialog.
  await page.getByRole("button", { name: /Add skill/ }).click();
  await page.getByText("Install from repo…").click();
  await expect(page.getByText(/uses npx skills/)).toBeVisible();
  await page.getByLabel("Source").fill("owner/repo");
  await page.getByLabel("Skills (optional)").fill("commit-helper");
  await page.getByRole("button", { name: "Run" }).click();

  // The review screen: parsed metadata, nothing installed.
  await expect(page.getByText("Review before installing")).toBeVisible();
  await expect(page.getByText("Writes commit messages")).toBeVisible();
  await expect(page.getByText("23/500")).toBeVisible();
  await expect(page.getByText(/1 bundled file \(notes\.txt\)/)).toBeVisible();
  await expect(page.getByText("commit-helper", { exact: true })).toHaveCount(1); // preview only

  await page.getByRole("button", { name: "Install skills" }).click();

  // Installed: name-first banner, a real row, and the lockfile provenance badge.
  await expect(page.getByRole("status")).toContainText("commit-helper");
  await expect(page.getByText("commit-helper", { exact: true })).toHaveCount(2); // banner + row
  await expect(page.getByTitle(/Installed from owner\/repo/)).toBeVisible();
  await page.getByLabel("Open commit-helper").click();
  await expect(page.getByLabel("Delete commit-helper")).toHaveAttribute("title", "Remove (uninstall)");
});
