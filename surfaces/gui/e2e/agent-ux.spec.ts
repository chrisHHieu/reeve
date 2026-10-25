// The agent-UX behaviours this round added, exercised through the REAL render path: the fake
// agent speaks the production event protocol (fixtures.ts), so a regression in the wiring fails
// here rather than in a screenshot. Four contracts:
//
//   · a running step carries a live clock, and settles into the turn's summary
//   · a message typed mid-turn is queued, shown, and sent when the turn ends
//   · Escape stops the run — but never while the composer has the key
//   · a tool that FAILS opens itself, with its own text available to copy
import { expect } from "@playwright/test";
import { test } from "./fixtures";

const box = (page: import("@playwright/test").Page) => page.locator("textarea").first();
const send = async (page: import("@playwright/test").Page, text: string) => {
  await box(page).fill(text);
  await page.getByRole("button", { name: "Send" }).click();
};

test("a running step counts up, and the settled turn says what it did", async ({ page }) => {
  await page.goto("/");
  await send(page, "please run a tool");

  // The call is written but waits on approval, so it has not been dispatched: the row says it
  // waits its turn instead of counting a clock for work that has not started (no tool_started yet).
  const row = page.getByTestId("turn-step");
  await expect(row).toBeVisible();
  await expect(row.getByTestId("tool-queued")).toHaveText("waiting to run");
  await expect(row.getByTestId("elapsed")).toHaveCount(0);

  await page.getByRole("button", { name: "Allow once" }).last().click();
  await expect(page.getByText(/The command ran/)).toBeVisible();

  // Settled, the turn has no line of its own: the answer's footer closes it, and a clean
  // turn's footer says nothing failed.
  await expect(page.getByTestId("turn-status")).toHaveCount(0);
  await expect(page.getByTestId("turn-summary-failed")).toHaveCount(0);
  // The row moved into the folded review view with the rest of the work, so the clock is gone
  // (it was the LIVE duration; the server's measured one is what the row shows now).
  await expect(page.getByTestId("elapsed")).toHaveCount(0);
});

test("a message typed while the agent works is queued, then sent", async ({ page }) => {
  await page.goto("/");
  await send(page, "stream the epic");

  // Mid-turn there are two things the user may want, and both are offered: the composer keeps
  // accepting text, and what it accepts is queued rather than refused.
  await expect(page.getByRole("button", { name: /Stop/ })).toBeVisible();
  await box(page).fill("and then summarize it");
  // Enter is the queue path. The corner is Stop alone: no Queue button, and no unlabeled
  // button beside it (the placeholder says what Enter and Ctrl+Enter do).
  await expect(page.getByTestId("queue-send")).toHaveCount(0);
  await expect(page.getByTestId("steer-send")).toHaveCount(0);
  await expect(box(page)).toHaveAttribute("placeholder", "Enter queues it, Ctrl+Enter sends it now");
  await box(page).press("Enter");

  // What is queued is a row of the composer's own card, with what happens to it and two
  // ways out: into the running turn now, or off the queue.
  const row = page.locator(".composer").getByTestId("queued-prompt");
  await expect(row).toContainText("and then summarize it");
  await expect(row).toContainText("after this turn");
  await expect(row.getByRole("button", { name: "Send now" })).toBeVisible();
  await expect(row.getByRole("button", { name: "Remove from the queue" })).toBeVisible();
  await expect(box(page)).toHaveValue("");

  // The drain is automatic: when the turn ends the queued text is sent as its own message, the
  // fake agent echoes it, and the banner empties.
  await expect(page.getByText("Echo: and then summarize it")).toBeVisible({ timeout: 20_000 });
  await expect(page.getByTestId("queued-prompts")).toHaveCount(0);
});

test("Escape stops the run, and leaves the composer's own Escape alone", async ({ page }) => {
  await page.goto("/");
  await send(page, "stream the epic");
  await expect(page.getByRole("button", { name: /Stop/ })).toBeVisible();

  // With the composer focused, Escape belongs to editing (it closes the slash popup and clears
  // a draft). The run must still be going.
  await box(page).press("Escape");
  await expect(page.getByRole("button", { name: /Stop/ })).toBeVisible();
  await expect(page.getByText("Interrupted.")).toHaveCount(0);

  // Focus the page instead: now it is the global cancel.
  await box(page).blur();
  await page.keyboard.press("Escape");
  await expect(page.getByText("Interrupted.")).toBeVisible();
  await expect(page.getByRole("button", { name: /Stop/ })).toHaveCount(0);
});

test("by default a running command stays one line and a click shows its streamed output", async ({ page }) => {
  await page.goto("/");
  await send(page, "please run a tool");
  await page.getByRole("button", { name: "Allow once" }).last().click();

  // Settings > General > Open tools when they finish is off: nothing opens on its own, so no
  // row opens while it runs and folds back when it ends (owner, 2026-10-21).
  const toggle = page.getByTestId("turn-step").first().locator("[data-disclosure-row]");
  await expect(page.getByText(/The command ran/)).toBeVisible();
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await toggle.click();
  await expect(page.getByTestId("terminal-output")).toContainText("README.md");
});

test("with Open tools when they finish on, a running command opens itself and its output streams into the card", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("reeve:open-finished-tools", "1"));
  await page.goto("/");
  await send(page, "please run a tool");
  await page.getByRole("button", { name: "Allow once" }).last().click();

  // A command still running after half a second opens itself: it has output to show, and the
  // collapsed one-line row is exactly where that output would be hidden. (A command that
  // returns in 80ms never triggers it — the timer is cancelled when the call settles first.)
  const row = page.getByTestId("turn-step").first();
  await expect(row.locator("[data-disclosure-row]")).toHaveAttribute("aria-expanded", "true");

  // The deltas arrived while the call was RUNNING (the fixture sends them before the result),
  // so the card is filling in rather than showing a wall of text at the end.
  const card = page.getByTestId("terminal-output");
  await expect(card).toContainText("collecting");
  await expect(card).toContainText("README.md");

  // And the result still lands: the call's own output, then its verdict.
  await expect(page.getByText(/The command ran/)).toBeVisible();
  await expect(page.getByTestId("turn-status")).toHaveCount(0);
});

test("a tool that fails opens itself, with the failure's text to copy", async ({ page }) => {
  await page.goto("/");
  await send(page, "please fail a tool");
  await page.getByRole("button", { name: "Allow once" }).last().click();

  // The failure lands while the turn is still running (the model has not given up yet), which is
  // exactly when the row must open on its own: the reader is being told something went wrong,
  // and making them click to find out WHAT is the difference between an actionable error and a
  // red line. The turn header counts it too, without unfolding anything.
  const row = page.getByTestId("turn-step");
  await expect(row.locator("[data-disclosure-row]")).toHaveAttribute("aria-expanded", "true");
  await expect(row).toContainText("1 failed, 3 passed");
  await expect(row.getByTestId("copy-button")).toBeVisible();
  await expect(page.getByTestId("turn-summary-failed")).toHaveText("1 failed");
});
