// An approval is one card in the transcript's own words (one card since 2026-10-12): a
// title in words, what is asked about in an inset (a write shows what it would write at once),
// a short "Always allow" whose full rule is its tooltip. A command is titled by what the model
// said it does, and an action that leaves the machine says so. No "PERMISSION REQUIRED" over it,
// and no arguments dumped as JSON.
import { expect } from "@playwright/test";
import { test } from "./fixtures";
import { say } from "./steps";

test.beforeEach(async ({ page }) => {
  await page.goto("/");
});

test("a file write: humanized title, its content on the card, Allow resolves", async ({ page }) => {
  await say(page, "please write a file");
  const card = page.getByTestId("approval-row");
  await expect(card).toContainText("Write fetch_data.py");
  await expect(card).not.toContainText(/permission required/i);
  await expect(card.getByRole("button", { name: "Always allow", exact: true })).toHaveAttribute("title", /for this session/);
  // The file does not exist yet: what it would hold comes from the call's arguments.
  await expect(card).toContainText("import json");
  await card.getByText("Show all 6 lines").click();
  await expect(card).toContainText("done = True");
  await page.screenshot({ path: "test-results/ux018-compact-row.png", fullPage: false });

  await card.getByRole("button", { name: "Allow", exact: true }).click();
  await expect(page.getByText(/Done via write_file/)).toBeVisible();
});

test("run_shell → full card: description title, command preview, runs-on-this-computer note", async ({ page }) => {
  await say(page, "please run a tool");
  // The mock's call has no description of its own, so the title is "Run a command"; the command
  // is the preview, the reason is there, and a note says where it runs: "runs", not "stays",
  // since it executes here and a script can reach the network by itself.
  await expect(page.getByText("Run a command").last()).toBeVisible();
  await expect(page.getByText("runs on this computer").last()).toBeVisible();
  await expect(page.getByText("Reeve wants to run a command.").first()).toBeVisible();
  await expect(page.getByRole("button", { name: "Always allow this command" }).last()).toBeVisible();
  await expect(page.getByText(/local action/)).toHaveCount(0);
  await page.screenshot({ path: "test-results/ux018-shell-card.png", fullPage: false });

  await page.getByRole("button", { name: "Allow once" }).last().click();
  await expect(page.getByText("The command ran; 1 file found.")).toBeVisible();
});

test("a one-paragraph digest send is clamped to a card, expandable in place", async ({ page }) => {
  await say(page, "post the long digest");
  // The message is in a box of bounded height, not an endless quote.
  const preview = page.getByTestId("approval-preview");
  await expect(preview).toBeVisible();
  await expect(preview).toContainText("aisuite — last 24 hours");
  const clamped = (await preview.boundingBox())!.height;
  expect(clamped).toBeLessThan(200);
  await page.screenshot({ path: "test-results/send-digest-clamped.png", fullPage: false });

  // It opens where it is, and can close again.
  await page.getByText("Show the full message").click();
  expect((await preview.boundingBox())!.height).toBeGreaterThan(clamped);
  await expect(page.getByText("Show less")).toBeVisible();
});
