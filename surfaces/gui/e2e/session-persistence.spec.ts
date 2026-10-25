// A session is a place you leave and come back to, not a page that is rebuilt (owner report
// 2026-09-27): going to Settings or Automations and back used to reload the transcript,
// drop the stream and mark a still-running turn as stopped, because picking the open
// session in the nav ran the full switch, and the session view itself was unmounted
// while another page was up. Now the view stays mounted and the open session is only
// brought back into view; another session is brought back from its own live state (it has
// kept receiving events the whole time, see session-background.spec.ts), so it needs no
// re-read at all.
import { expect, type Page } from "@playwright/test";
import { test, seedSessionMessages } from "./fixtures";

const TS = 1755600000;

function longHistory(label: string, turns = 30) {
  const out: Record<string, unknown>[] = [];
  for (let i = 1; i <= turns; i++) {
    out.push({ role: "user", content: `${label} question ${i}`, ts: TS + i * 10 });
    out.push({ role: "assistant", content: `${label} answer ${i}.\n\n${"A line of detail. ".repeat(20)}`, ts: TS + i * 10 + 5 });
  }
  return out;
}

/** Count GETs of one session's transcript: a reload is exactly one of these. */
function countTranscriptReads(page: Page, sessionId: string): { n: number } {
  const counter = { n: 0 };
  page.on("request", (req) => {
    if (req.method() === "GET" && new RegExp(`/v1/sessions/${sessionId}/messages$`).test(req.url())) counter.n += 1;
  });
  return counter;
}

async function openSettingsAndComeBack(page: Page, sessionTitle: string) {
  await page.getByTestId("rail-settings").click();
  await expect(page.locator(".main").first()).toBeHidden();
  await page.getByTestId("settings-back").click();
  await expect(page.locator(".main").first()).toBeVisible();
  // …and the other way back people use: another page, then the conversation's own row in
  // the nav (Settings has no nav; Automations does).
  await page.getByRole("button", { name: "Automations", exact: true }).click();
  await expect(page.locator(".main").first()).toBeHidden();
  await page.getByText(sessionTitle).first().click();
  await expect(page.locator(".main").first()).toBeVisible();
}

test("leaving for Settings and coming back keeps the transcript, the draft and the place", async ({ page }) => {
  await seedSessionMessages(page, "pinned-session-1", longHistory("Launch"));
  const reads = countTranscriptReads(page, "pinned-session-1");
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();
  await expect(page.getByText("Launch answer 30.").first()).toBeVisible();
  const loaded = reads.n; // boot may have read it too; what matters is no read after this
  expect(loaded).toBeGreaterThan(0);

  const box = page.getByPlaceholder(/Ask anything/);
  await box.fill("a half-written thought");
  const scroller = page.locator(".main-scroll");
  await scroller.evaluate((el) => {
    el.scrollTop = Math.floor(el.scrollHeight / 3);
    el.dispatchEvent(new Event("scroll"));
  });
  const before = await scroller.evaluate((el) => el.scrollTop);
  expect(before).toBeGreaterThan(200);

  await openSettingsAndComeBack(page, "Draft the launch note");

  expect(reads.n).toBe(loaded); // not reloaded
  await expect(box).toHaveValue("a half-written thought");
  const after = await scroller.evaluate((el) => el.scrollTop);
  expect(Math.abs(after - before)).toBeLessThan(4);
});

test("a turn still running when you leave is still running when you come back", async ({ page }) => {
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();
  const box = page.getByPlaceholder(/Ask anything/);
  await box.fill("stream the epic");
  await box.press("Enter");
  await expect(page.getByText("The epic scrolls ever onward").first()).toBeVisible({ timeout: 10_000 });

  await openSettingsAndComeBack(page, "Draft the launch note");

  // Nothing restarted: the streamed text is still there and the turn still reads as running.
  await expect(page.getByText("The epic scrolls ever onward").first()).toBeVisible();
  await expect(page.getByRole("button", { name: /Stop/ })).toBeVisible();
});

test("switching back to a conversation shows it at once, without waiting for the server", async ({ page }) => {
  await seedSessionMessages(page, "pinned-session-1", longHistory("Launch"));
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();
  await expect(page.getByText("Launch answer 30.").first()).toBeVisible();

  await page.getByText("Weekly plan 1").first().click();
  await expect(page.getByText("Launch answer 30.")).toHaveCount(0);

  // The server is slow the second time: the conversation must not wait for it.
  let slowServed = false;
  await page.route(/\/v1\/sessions\/pinned-session-1\/messages$/, async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 3000));
    slowServed = true;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ messages: longHistory("Launch"), revert: null }),
    });
  });
  await page.getByText("Draft the launch note").first().click();
  await expect(page.getByText("Launch answer 30.").first()).toBeVisible({ timeout: 1000 });
  expect(slowServed).toBe(false);
});
