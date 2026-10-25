// The task head's activity row steps down when a segment's words do not fit, not only when the
// row as a whole overflows (owner's screenshot, 2026-10-20: "3 of 3 done" ran under "29 files").
// The to-dos segment may shrink to nothing, so the row never overflowed while its words spilled
// out; a wider face (a theme's font, a larger size) is what makes it happen, stood in for here by
// a larger size on the row's own text.
import type { Page } from "@playwright/test";
import { expect } from "@playwright/test";
import { seedSessionMessages, sendSessionEventTo, test } from "./fixtures";

const SESSION = "pinned-session-1";

async function openBusySession(page: Page) {
  const stats = Array.from({ length: 29 }, (_, i) => ({ path: `src/file${i}.ts`, added: 83, removed: 11 }));
  await seedSessionMessages(page, SESSION, [
    { role: "user", content: "Do the work" },
    { role: "assistant", content: "Done." },
    { role: "notice", kind: "changes", changes: { overwritten: stats.map((s) => s.path), stats } },
  ]);
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();
  const todos = ["Read", "Write", "Check"].map((content) => ({ content, status: "completed" }));
  await sendSessionEventTo(page, SESSION, "tool_proposed", { name: "todo_write", arguments: { todos }, call_id: "t1" });
  await sendSessionEventTo(page, SESSION, "tool_finished", { name: "todo_write", status: "ok", call_id: "t1", result_preview: "ok" });
  await sendSessionEventTo(page, SESSION, "assistant_message", {
    text: "ok",
    usage: { model: "anthropic:claude-opus-4-8", input: 1_000_000, output: 420_000, cache_read: 52_000_000, cache_write: 0 },
  });
  await sendSessionEventTo(page, SESSION, "turn_done", {});
  await expect(page.getByTestId("task-tokens")).toBeVisible();
}

/** The to-dos segment's words stay inside it, and it ends before the facts begin. */
async function expectNoSpill(page: Page) {
  const button = page.getByTestId("todo-dock-toggle");
  expect(await button.evaluate((el) => el.scrollWidth <= el.clientWidth + 1)).toBe(true);
  const segment = (await page.getByTestId("todo-dock").boundingBox())!;
  const files = (await page.getByTestId("task-files").boundingBox())!;
  expect(segment.x + segment.width).toBeLessThanOrEqual(files.x + 0.5);
}

test("with room, the row says its to-dos in words", async ({ page }) => {
  await openBusySession(page);
  const row = page.getByTestId("activity-row");
  await expect(row).toHaveAttribute("data-fit", "wide");
  await expect(page.getByTestId("todo-dock-progress")).toBeVisible();
  await expectNoSpill(page);
});

test("a wider face steps the row down instead of running its words under the facts", async ({ page }) => {
  await openBusySession(page);
  await page.addStyleTag({ content: ".act-btn, .head-fact { font-size: 17px !important; }" });
  const row = page.getByTestId("activity-row");
  await expect(row).not.toHaveAttribute("data-fit", "wide");
  await expectNoSpill(page);
  await row.screenshot({ path: test.info().outputPath("activity-row-wider-face.png") });
});
