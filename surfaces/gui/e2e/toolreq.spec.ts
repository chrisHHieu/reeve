// A command-line tool that is missing is a decision the person sees, never a check left out
//. Seen by the owner on 2026-08-13: without gitleaks, a security review left out its
// scan of the git history for secrets, and "could not look" read as "clean".
import { expect } from "@playwright/test";
import { test } from "./fixtures";
import { say } from "./steps";

test.beforeEach(async ({ page }) => {
  await page.goto("/");
});

const transcript = (page: import("@playwright/test").Page) => page.locator(".main-scroll");

test("request_tool surfaces a card naming the tool, the reason and the pinned version", async ({ page }) => {
  await say(page, "scan for secrets");
  const card = page.getByTestId("toolreq-card");
  await expect(card).toContainText("gitleaks");
  // The agent's reason, said as its words.
  await expect(card).toContainText("Reeve: “scan the git history for committed secrets”");
  // The facts are the product speaking, apart from the quoted reason: mixed together they made
  // the card hard to read (2026-08-14).
  await expect(card.locator(".toolreq-facts")).toContainText("8.30.1");
  // Who installs it, from where, and that the person may install it themselves; no supply-chain
  // terms (owner, 2026-08-15).
  await expect(card).toContainText("Reeve installs its own verified copy from github.com/gitleaks, or you install it yourself and continue.");
  // Going on without it is an ordinary choice that carries on, not a failure.
  await expect(card.getByTestId("toolreq-skip")).toHaveText("Continue without it");
});

// Seen by the owner on 2026-08-14: the card promised a "pinned build, checksum-verified" for a
// tool that had none, and approving could only fail. No install facts means no Install.
test("an event without install metadata fails CLOSED — no Install to press, skip offered", async ({ page }) => {
  await say(page, "request an unpinned tool");
  const card = page.getByTestId("toolreq-card");
  await expect(card).toContainText("somescanner");
  await expect(card).toContainText(/no verified build/i);
  await expect(card.getByTestId("toolreq-install")).toHaveCount(0);
  await expect(card.getByTestId("toolreq-skip")).toBeEnabled();
});

test("installing runs the check; skipping still reports coverage", async ({ page }) => {
  await say(page, "scan for secrets");
  await page.getByTestId("toolreq-install").click();
  await expect(transcript(page)).toContainText("Installed gitleaks");

  // Skipped, the check is said to be missing from what was covered.
  await say(page, "scan for secrets");
  await page.getByTestId("toolreq-skip").click();
  await expect(transcript(page)).toContainText(/Coverage:/);
});
