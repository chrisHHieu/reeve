import type { Page } from "@playwright/test";
import { test, expect } from "./fixtures";
import { composer, say } from "./steps";

// Standing approvals. The card that approves an automation's creation lists what it asks
// for: writes as grants, reads as what it will see. In a run of the automation, an approval
// offers "Allow every time", kept for that automation and offered nowhere else. Its page lists
// the rules granted, each with Revoke.

const button = (page: Page, name: string) => page.getByRole("button", { name, exact: true });

/** The automation's page, opened from the rail. */
async function automationPage(page: Page) {
  await page.goto("/");
  await page.getByTestId("rail-automations").click();
  await page.getByText("Daily AI News").first().click();
  await expect(page.getByRole("button", { name: /Run now/ })).toBeVisible();
}

/** A new session, ready to type in. */
async function freshSession(page: Page) {
  await page.goto("/");
  await expect(composer(page)).toBeVisible();
}

test("creation consent card renders writes as grants and reads as disclosure", async ({ page }) => {
  await freshSession(page);
  await say(page, "please create an automation for the weekly digest");

  // The proposal, in words, where the arguments' JSON would be.
  const grants = page.getByTestId("approval-grants");
  await expect(grants).toBeVisible();
  for (const said of ["slack:T1/C1", "always allowed once you approve", "acme/agent-platform", "read-only"]) {
    await expect(grants).toContainText(said);
  }
  // Creating it is the grant itself: nothing to allow every time here.
  await expect(button(page, "Allow every time")).toHaveCount(0);
  await page.getByRole("button", { name: "Allow once" }).last().click();
  await expect(page.getByText("Done via create_scheduled_task [decision=once]")).toBeVisible();
});

test("a run session's approval card offers Allow every time and sends always_task", async ({ page }) => {
  await automationPage(page);
  await page.getByRole("button", { name: /Run now/ }).click();
  await expect(page.getByTestId("run-crumb")).toBeVisible();
  // A run sends the automation's prompt by itself; its turn ends before the next message.
  await expect(page.getByText(/Echo: .*Fetch the latest AI news/)).toBeVisible();

  // A write a standing approval can cover, in the run: "every time" takes the place of the
  // session's own Always allow, and is what the socket is told.
  await say(page, "post the digest");
  await expect(button(page, "Allow every time")).toBeVisible();
  await expect(button(page, "Always allow")).toHaveCount(0);
  await button(page, "Allow every time").click();
  await expect(page.getByText("Done via send_message [decision=always_task]")).toBeVisible();
});

test("a plain session never offers Allow every time, even for an eligible call", async ({ page }) => {
  await freshSession(page);
  // The same call to the same place, with no run around it: the session's Always allow only.
  await say(page, "post the digest");
  await expect(page.getByRole("button", { name: "Allow once" }).last()).toBeVisible();
  await expect(button(page, "Allow every time")).toHaveCount(0);
  await expect(button(page, "Always allow").last()).toBeVisible();
});

test("task detail lists standing rules under 'Allowed without asking'; Revoke removes one", async ({ page }) => {
  await automationPage(page);
  const grants = page.getByTestId("task-grants");
  await expect(page.getByText("Allowed without asking")).toBeVisible();
  await expect(grants).toContainText("send_message");
  await expect(grants).toContainText("slack:T1/C1");

  // Its one rule revoked, the section goes with it: nothing is allowed without asking now.
  await grants.getByRole("button", { name: "Revoke" }).click();
  await expect(page.getByTestId("task-grants")).toHaveCount(0);
  await expect(page.getByText("Allowed without asking")).toHaveCount(0);
});
