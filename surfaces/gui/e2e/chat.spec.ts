import type { Page } from "@playwright/test";
import { test, expect } from "./fixtures";
import { composer, say } from "./steps";

// The loop everything else stands on: the app resumes the last session, a message goes out on
// the socket and the reply streams back; and an approval holds the turn until Allow or Deny goes
// back the same way. The fake agent is mock/turns.ts.

// The app opens on the most recent session, "Draft the launch note", and the composer is ready
// once the agent's `ready` arrives.
test.beforeEach(async ({ page }) => {
  await page.goto("/");
  await expect(composer(page)).toBeVisible();
});

const lastButton = (page: Page, name: string) => page.getByRole("button", { name }).last();

test("send → user bubble → streamed echo reply renders", async ({ page }) => {
  await say(page, "hello agent");
  await expect(page.getByText("hello agent", { exact: true }).first()).toBeVisible();
  await expect(page.getByText(/Echo: hello agent/)).toBeVisible();
  // The message went with the model the composer showed: what was seen at send is what answers.
  await expect(page.getByText("[model=anthropic:claude-opus-4-8]")).toBeVisible();
  // After the first turn the picker still works (models switch mid-session), and the
  // session's mode is on the composer's Mode control.
  await expect(page.getByTestId("model-picker-trigger")).toContainText("Claude Opus");
  await expect(page.getByRole("button", { name: "Mode", exact: true })).toContainText("Workspace write");
  // Emptied, for the next message.
  await expect(composer(page)).toHaveValue("");
});

test("approval: tool request suspends the turn; Allow once resumes it", async ({ page }) => {
  await say(page, "please run a tool");
  // The card gives the call and its reason, and the turn waits on it.
  await expect(page.getByText("Reeve wants to run a command.").first()).toBeVisible();
  await lastButton(page, "Allow once").click();
  await expect(page.getByText("The command ran; 1 file found.")).toBeVisible();
});

test("approval: Deny skips the tool and the agent says so", async ({ page }) => {
  await say(page, "please run a tool");
  await expect(lastButton(page, "Deny")).toBeVisible();
  await lastButton(page, "Deny").click();
  await expect(page.getByText("Understood — skipped the command.")).toBeVisible();
});

test("a pasted link with no space to wrap at stays inside its bubble and its column", async ({ page }) => {
  // A paste of cited URLs (2026-10-29): the link widened the bubble past its column, and the
  // right-aligned bubble's overflow went left and was cut off.
  const link = "https://example.org/portale/documents/20143/257829/Allegato+quadro+generale+categorie_QuadroGeneraleCategorie.pdf/caf80eb7-0000-4000-8000-000000009df4";
  await say(page, `see [1] (${link}), [2] (${link}?again)`);
  const bubble = page.locator(".bubble-user").last();
  await expect(bubble).toContainText("caf80eb7");
  const fits = await bubble.evaluate((el) => {
    const own = el.getBoundingClientRect();
    const column = (el.parentElement?.parentElement ?? el).getBoundingClientRect();
    return { inside: own.left >= column.left - 1 && own.right <= column.right + 1, clipped: el.scrollWidth > el.clientWidth + 1 };
  });
  expect(fits).toEqual({ inside: true, clipped: false });
});

test("long user pastes clamp with a more…/less… toggle", async ({ page }) => {
  // About 2,400 characters, with a mark at its end.
  const end = "END-OF-PASTE-MARKER";
  await say(page, "reply OK. " + "lorem ipsum dolor sit amet consectetur ".repeat(60) + end);

  // The bubble shows the start and not the end, with "more…".
  const bubble = page.locator(".bubble-user").last();
  const more = page.getByRole("button", { name: "more…" });
  await expect(more).toBeVisible();
  await expect(bubble).toContainText("reply OK.");
  await expect(bubble).not.toContainText(end);
  await more.click();
  await expect(bubble).toContainText(end);
  await page.getByRole("button", { name: "less…" }).click();
  await expect(bubble).not.toContainText(end);

  // A short message has no such control: there is still one, the paste's.
  await expect(page.getByText("Echo:").first()).toBeVisible();
  await say(page, "short follow-up");
  await expect(page.getByText("short follow-up", { exact: true }).first()).toBeVisible();
  await expect(page.getByRole("button", { name: "more…" })).toHaveCount(1);
});
