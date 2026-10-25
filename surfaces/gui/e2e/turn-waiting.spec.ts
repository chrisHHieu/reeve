// What the turn says about itself while it is blocked on the person.
//
// The pending approval renders over the composer, and the transcript's status line used to keep
// reading "Running a command" with its working animation going — the one moment it was not
// working at all. It now says what it is waiting for, and the animation rests, because the
// motion is what tells "still working" from "your turn".
import { expect } from "@playwright/test";
import { test } from "./fixtures";

test("an open approval turns the status line into 'Waiting for your approval'", async ({ page }) => {
  await page.goto("/");
  const box = page.getByPlaceholder(/Ask anything/);
  await box.fill("please run a tool");
  await box.press("Enter");
  await expect(page.getByRole("button", { name: "Allow once" }).last()).toBeVisible();

  const status = page.getByTestId("turn-status");
  await expect(page.getByTestId("turn-status-label")).toHaveText("Waiting for your approval");
  await expect(status).toHaveAttribute("data-waiting", "");

  // Answering it hands the turn back: the line names the work again.
  await page.getByRole("button", { name: "Allow once" }).last().click();
  await expect(page.getByTestId("turn-status-label")).not.toHaveText(/Waiting/);
  await expect(page.getByTestId("turn-status")).not.toHaveAttribute("data-waiting", "");
});

test("the mode chip shows the permission state without opening the menu", async ({ page }) => {
  await page.goto("/");
  const chip = page.getByRole("button", { name: "Mode", exact: true });
  await expect(chip).toHaveAttribute("data-permission", "ask");

  await chip.click();
  await page.getByTestId("mode-menu").getByText("Full access").click();
  await expect(chip).toHaveAttribute("data-permission", "full");
  await expect(chip).toContainText("Full access");

  await chip.click();
  await page.getByTestId("mode-menu").getByText("Read only").click();
  await expect(chip).toHaveAttribute("data-permission", "read-only");
});
