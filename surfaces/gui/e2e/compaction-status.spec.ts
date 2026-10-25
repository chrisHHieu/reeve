// An automatic compaction is named on the running turn's own status line, not under it.
//
// Owner report 2026-10-01: the summarizer runs 35-100 s in the real session logs, and the screen showed
// "Working" with a second spinner and a small "Compacting context…" row stacked beneath it. One live line
// says what the turn is doing; compaction is an activity like any other, so it takes that line.
import { expect } from "@playwright/test";
import { sendSessionEvent, test } from "./fixtures";

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem("reeve-e2e-rail-default", "1");
    localStorage.setItem("reeve:rail-hidden:v1", "1");
  });
});

async function runningTurn(page: import("@playwright/test").Page) {
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();
  await sendSessionEvent(page, "turn_start", { input: "go" });
  await sendSessionEvent(page, "assistant_message", { text: "Reading the files now.", tool_calls: [{ id: "c1" }] });
  await sendSessionEvent(page, "tool_proposed", { name: "read_file", arguments: { path: "a.py" }, call_id: "c1" });
  await sendSessionEvent(page, "tool_finished", { name: "read_file", status: "ok", call_id: "c1", result_preview: "ok" });
}

test("compaction during a turn is named on the status line, with no second spinner", async ({ page }) => {
  await runningTurn(page);
  const transcript = page.locator(".transcript");
  const label = page.locator('[data-testid=turn-status][data-state=running]').getByTestId("turn-status-label");
  await expect(label).toHaveText("Working");

  await sendSessionEvent(page, "compacting", {});
  await expect(label).toHaveText(/^Compacting the conversation/);
  await expect(page.getByTestId("turn-status-clock")).toBeVisible(); // the turn's clock keeps counting beside it
  await expect(page.locator(".waiting-row")).toHaveCount(0);

  await sendSessionEvent(page, "compacted", {
    text: "Context compacted — earlier turns were summarized",
    reason: "summarized",
    saved_tokens: 12500,
    now_tokens: 30000,
    summary: "Objective: keep the launch note moving.",
  });
  await expect(transcript).toContainText("Compacted");
  await expect(transcript).not.toContainText("Compacting the conversation");
  await expect(label).toHaveText("Working"); // the turn goes on
});

// A `/compact` between turns (owner, 2026-10-13). It used to show a spinner of its own with
// no size and no clock, the composer looked ready, and a message sent meanwhile was refused
// by the server ("already running a turn") and lost. Now it is the line a turn has, the top
// bar says it, and a message typed meanwhile waits in the queue and goes when it ends.
async function idleSession(page: import("@playwright/test").Page) {
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();
  await sendSessionEvent(page, "turn_start", { input: "go" });
  await sendSessionEvent(page, "assistant_message", { text: "Done." });
  await sendSessionEvent(page, "turn_done", {});
}

test("a /compact between turns is a working line, and a message sent meanwhile waits for it", async ({ page }) => {
  await idleSession(page);
  await sendSessionEvent(page, "compacting", { manual: true });
  const line = page.getByTestId("compacting-line");
  await expect(line).toContainText("Compacting the conversation");
  await expect(page.getByTestId("topbar-state")).toHaveAttribute("data-state", "compacting");

  const box = page.getByRole("textbox", { name: "Message" });
  await expect(box).toHaveAttribute("placeholder", "Enter queues it until compacting is done");
  await box.fill("now add the csv flag");
  await box.press("Enter");
  await expect(page.getByTestId("queued-prompt")).toContainText("now add the csv flag");
  await expect(page.getByTestId("queued-prompt-when")).toHaveText("when compacting is done");

  // Another frame of the session is not the end of it.
  await sendSessionEvent(page, "jobs", { jobs: [] });
  await expect(line).toBeVisible();
  await expect(page.getByTestId("queued-prompt")).toHaveCount(1);

  await sendSessionEvent(page, "compacted", {
    reason: "summarized",
    saved_tokens: 152000,
    now_tokens: 30000,
    seconds: 14.2,
    summary: "Objective: the export flags.",
  });
  await expect(line).toHaveCount(0);
  await expect(page.getByTestId("compaction-divider").last()).toContainText("182k → 30k tokens · 14s");
  // The queued message went the moment it ended.
  await expect(page.getByTestId("queued-prompt")).toHaveCount(0);
  await expect(page.locator(".transcript")).toContainText("now add the csv flag");
});

test("a /compact that leaves no marker says why where its line stood", async ({ page }) => {
  await idleSession(page);
  await sendSessionEvent(page, "compacting", { manual: true });
  await sendSessionEvent(page, "compact_failed", { error: "the summarizer could not condense this history", nothing: false });
  const note = page.getByTestId("compact-note");
  await expect(note).toHaveAttribute("data-kind", "failed");
  await expect(note).toContainText("Could not compact");
  await expect(note).toContainText("· the summarizer could not condense this history");
  await expect(note.getByTestId("compact-again")).toBeVisible();
  await expect(page.getByTestId("compacting-line")).toHaveCount(0);

  await sendSessionEvent(page, "compacting", { manual: true });
  await expect(note).toHaveCount(0);
  await sendSessionEvent(page, "compact_failed", { error: "nothing to compact", nothing: true });
  await expect(page.getByTestId("compact-note")).toHaveAttribute("data-kind", "nothing");
  await expect(page.getByTestId("compact-note")).toContainText("Nothing to compact yet");
});

// Owner, 2026-10-13: opening what a compaction kept had no motion. The summary was mounted and
// unmounted on the click. Heights are sampled a frame at a time: a region that moves is seen
// at heights between shut and open, both ways; one that jumps is only ever at the two ends.
test("what a compaction kept opens and closes as a motion", async ({ page }) => {
  await runningTurn(page);
  await sendSessionEvent(page, "compacted", {
    text: "Context compacted — earlier turns were summarized",
    reason: "summarized",
    saved_tokens: 12500,
    now_tokens: 30000,
    summary: Array.from({ length: 12 }, (_, line) => `- kept fact ${line + 1}`).join("\n"),
  });
  const marker = page.getByTestId("compaction-divider");
  // It arrives closed; the first click opens it.
  await expect(marker.getByTestId("compaction-summary")).toHaveCount(0);
  await marker.getByTestId("compaction-toggle").click();
  await expect(marker.getByTestId("compaction-summary")).toContainText("kept fact 12");
  await page.waitForTimeout(400);
  const full = await marker.locator(".compaction-fold").evaluate((el) => el.getBoundingClientRect().height);
  expect(full).toBeGreaterThan(100);

  // Click, then read the region's height on each of the next frames until it is gone or settled.
  const heightsAfterClick = () =>
    marker.evaluate(
      (el) =>
        new Promise<number[]>((resolve) => {
          const seen: number[] = [];
          (el.querySelector("[data-testid=compaction-toggle]") as HTMLElement).click();
          const started = performance.now();
          const frame = () => {
            const fold = el.querySelector(".compaction-fold");
            seen.push(fold ? Math.round(fold.getBoundingClientRect().height) : 0);
            if (performance.now() - started > 700) resolve(seen);
            else requestAnimationFrame(frame);
          };
          requestAnimationFrame(frame);
        }),
    );
  const between = (heights: number[]) => heights.filter((height) => height > 4 && height < full - 4);

  const closing = await heightsAfterClick();
  expect(between(closing).length, `closing heights: ${closing.join(",")}`).toBeGreaterThan(3);
  expect(closing[closing.length - 1]).toBe(0);
  await expect(marker.getByTestId("compaction-summary")).toHaveCount(0);

  const opening = await heightsAfterClick();
  expect(between(opening).length, `opening heights: ${opening.join(",")}`).toBeGreaterThan(3);
  expect(Math.abs(opening[opening.length - 1] - full)).toBeLessThan(2);
});

test("a quiet compaction (prune, nothing to announce) clears the status when the turn goes on", async ({ page }) => {
  await runningTurn(page);
  const label = page.locator('[data-testid=turn-status][data-state=running]').getByTestId("turn-status-label");
  await sendSessionEvent(page, "compacting", {});
  await expect(label).toHaveText(/^Compacting the conversation/);
  // The prune tier emits `compacted` with reason "pruned" and no divider (owner call 2026-09-23).
  await sendSessionEvent(page, "compacted", { reason: "pruned", now_tokens: 30000 });
  await expect(label).toHaveText("Working");
  await expect(page.getByTestId("compaction-divider")).toHaveCount(0); // quiet: no marker
});
