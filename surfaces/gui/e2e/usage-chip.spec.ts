// What a session has spent. Once a turn reports usage, a quiet chip in the composer's
// lower row fills with the context window; it opens a breakdown by model. The fake agent puts the
// same usage on every echo (input 1k, output 200, cache reads 8k, cache writes 800: 10k a turn),
// and the mock's settings give the default model a 200k window.
import { expect, type Page } from "@playwright/test";
import { test } from "./fixtures";
import { composer, openPinned, say } from "./steps";

const chip = (page: Page) => page.getByTestId("usage-chip");
const popover = (page: Page) => page.getByTestId("usage-popover");

/** Send `text` with Enter and wait for its echo. */
async function turn(page: Page, text: string) {
  await say(page, text, "enter");
  await expect(page.getByText(`Echo: ${text}`, { exact: false }).first()).toBeVisible({ timeout: 10_000 });
}

/** The pinned session after one turn. */
async function afterOneTurn(page: Page) {
  await openPinned(page);
  await turn(page, "hello");
}

test("the usage popover answers the meter and offers no compaction action", async ({ page }) => {
  await afterOneTurn(page);
  await chip(page).click();
  await expect(popover(page)).toContainText("10k of 200k · 5%");
  // `/compact` in the slash menu is the one way to compact (owner, 2026-09-16); the popover
  // once had a second, with a busy state and a result of its own.
  await expect(popover(page).getByTestId("compact-now")).toHaveCount(0);
  await expect(popover(page).getByTestId("compact-result")).toHaveCount(0);
});

test("/compact runs from the slash menu and the draft is not sent as a message", async ({ page }) => {
  // The turn first: the chip is there only once usage is reported, and the meter is read below.
  await afterOneTurn(page);
  await composer(page).fill("/comp");
  const [request] = await Promise.all([
    page.waitForRequest((r) => /\/v1\/sessions\/[^/]+\/compact$/.test(new URL(r.url()).pathname) && r.method() === "POST"),
    page.getByTestId("slash-command-compact").click(),
  ]);
  expect(request.method()).toBe("POST");
  // It shows in the transcript, as the divider the server's marker makes, and nowhere near the
  // composer; a command puts nothing in the box.
  const divider = page.getByTestId("compaction-divider");
  await expect(divider).toContainText("Compacted");
  await expect(divider).toContainText("42.5k → 30k tokens");
  await expect(page.getByTestId("command-notice")).toHaveCount(0);
  await expect(composer(page)).toHaveValue("");
  // The meter takes the marker's `now_tokens` at once. Before, it showed the old fill until
  // another turn came.
  await chip(page).click();
  await expect(popover(page)).toContainText("30k of 200k · 15%");
});

test("usage chip appears after a turn and opens the breakdown popover", async ({ page }) => {
  await openPinned(page);
  // Nothing spent yet: no chip at all.
  await expect(chip(page)).toHaveCount(0);
  await turn(page, "hello");
  // The fill bar is the default (owner, 2026-09-25), with the session's total in its title.
  await expect(chip(page)).toHaveAttribute("title", "Context window 5% full · 10k tokens this session");

  // The window holds what was sent and what came back, since the answer is sent again with the
  // next request: 9.8k in and 200 out, 10k of 200k.
  await chip(page).click();
  const pop = popover(page);
  await expect(pop).toBeVisible();
  for (const said of ["Context window", "10k of 200k · 5%", "This session", "Claude Opus 4.8 · Anthropic"]) await expect(pop).toContainText(said);
  // A model's lines open from its row. With a cache split, the input lines add up to Total
  // input: 1k new, 8k read, 800 written.
  await pop.getByRole("button", { name: /Claude Opus 4.8/ }).click();
  for (const said of ["Uncached input", "Cache reads", "Cache writes", "Total input", "9.8k", "10k tokens"]) await expect(pop).toContainText(said);

  // A click beside it closes it; a second turn doubles the total.
  await page.mouse.click(10, 10);
  await expect(pop).toHaveCount(0);
  await turn(page, "again");
  await expect(chip(page)).toHaveAttribute("title", /· 20k tokens this session$/);
});

test("usage resets on a new session", async ({ page }) => {
  await openPinned(page);
  await say(page, "hello", "enter");
  await expect(chip(page)).toBeVisible({ timeout: 10_000 });
  // A new session starts with nothing spent.
  await page.getByRole("button", { name: /New session/ }).first().click();
  await expect(chip(page)).toHaveCount(0);
});

// The bar is the default; Settings has no switch for it, but the server's setting is
// still read, and with it off the chip says the session's total.
test("the context bar is the default; a server that turns it off gets the session total", async ({ page }) => {
  const chipAfterOneTurn = async () => {
    await openPinned(page);
    await say(page, "hello", "enter");
    await expect(chip(page)).toBeVisible({ timeout: 10_000 });
    return chip(page);
  };
  const served = page.waitForResponse((r) => /\/v1\/settings$/.test(new URL(r.url()).pathname));
  const bar = await chipAfterOneTurn();
  await expect(bar).not.toContainText("10k");
  await expect(bar).toHaveAttribute("title", /Context window 5% full/);

  // The settings the mock served, with the bar off.
  const settings = await (await served).json();
  await page.route(/\/v1\/settings$/, (route) => route.fulfill({ json: { ...settings, context_bar: false } }));
  const total = await chipAfterOneTurn();
  await expect(total).toContainText("10k");
  await expect(total).toHaveAttribute("title", "Token usage this session: 10k");
});

test("each turn's footer says what it spent, beside the model that answered", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await openPinned(page);
  await turn(page, "hello");
  await turn(page, "again");
  // A turn's own spend, not the running total: 10k each.
  const spent = page.getByTestId("turn-tokens");
  await expect(spent).toHaveCount(2);
  await expect(spent.last()).toHaveText("10k tokens");
  await spent.last().hover();
  await expect(page.getByRole("tooltip")).toContainText("Cache read 8k");
  await page.getByTestId("bubble-meta").last().screenshot({ path: test.info().outputPath("turn-footer.png") });
});
