// What the agent says to a subagent after its brief (`message_task`), replayed.
// In the parent: the call is a row of the turn naming whom it messaged. In the subagent's
// session: a message read inside its running turn is a note labelled as the agent's (not the
// person's "steered"), and one that opened a turn of its own is the agent speaking, as its brief
// did, never a "reported back" line.
import { expect } from "@playwright/test";
import { test, seedSessionMessages } from "./fixtures";

const TS = 1755600000;
const FROM = { synthetic: "parent_message", session_id: "p1" };

test("a message to a subagent replays as the agent's, on both sides", async ({ page }) => {
  await seedSessionMessages(page, "pinned-session-1", [
    { role: "user", content: "survey the repo, then the tests", ts: TS },
    {
      role: "assistant",
      content: "",
      tool_calls: [
        {
          id: "m1",
          function: {
            name: "message_task",
            arguments: JSON.stringify({ to: "survey", message: "include the cache module" }),
          },
        },
      ],
    },
    {
      role: "tool",
      tool_call_id: "m1",
      content: '<task id="abc123" state="running">\n<summary>Message delivered</summary>\n</task>',
      _presentation: { summary: "Sent to survey · it reads it next", kind: "search" },
    },
    { role: "assistant", content: "I told the survey to cover the cache too.", ts: TS + 5 },
    // The same transcript stands in for the subagent's side: the brief, a message read
    // mid-turn, and one that opened a turn of its own.
    { role: "user", content: "survey it", ts: TS + 10 },
    { role: "assistant", content: "reading the tree" },
    { role: "user", content: "include the cache module", _steer: true, source: FROM, ts: TS + 12 },
    { role: "assistant", content: "cache read too", ts: TS + 20 },
    { role: "user", content: "now the tests", source: FROM, ts: TS + 30 },
    { role: "assistant", content: "tests read", ts: TS + 40 },
  ]);
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();

  // The parent's row names whom it messaged and what happened.
  await expect(page.getByText("Sent to survey · it reads it next")).toBeVisible();

  // Read inside the running turn: labelled as the agent's.
  const note = page.getByTestId("turn-steer");
  await expect(note).toHaveAttribute("data-from", "agent");
  await expect(note).toContainText("main agent");
  await expect(note).toContainText("include the cache module");
  await expect(note).not.toContainText("steered");

  // Opened a turn of its own: the words as a bubble, and no "reported back" line.
  await expect(page.getByText("now the tests", { exact: true })).toBeVisible();
  await expect(page.getByText(/reported back/)).toHaveCount(0);
});

test("a call that returned its own refusal replays as failed, the others as done", async ({ page }) => {
  // A message to a workflow run is refused by the tool itself: the dispatch was fine, the call
  // did not do what was asked, and the stored result says so (`_failed`, the log's `is_error`).
  const send = (id: string, to: string) => ({
    role: "assistant",
    content: "",
    tool_calls: [{ id, function: { name: "message_task", arguments: JSON.stringify({ to, message: "hi" }) } }],
  });
  await seedSessionMessages(page, "pinned-session-1", [
    { role: "user", content: "message the run and the survey", ts: TS },
    send("r1", "abc123"),
    {
      role: "tool",
      tool_call_id: "r1",
      content: "Error: abc123 is a workflow run: it runs its script and reads no messages",
      _failed: true,
      _presentation: { summary: "Message to abc123 failed", kind: "search" },
    },
    send("r2", "survey"),
    {
      role: "tool",
      tool_call_id: "r2",
      content: '<task id="def456" state="running">Delivered</task>',
      _presentation: { summary: "Sent to survey · it reads it next", kind: "search" },
    },
    { role: "assistant", content: "The run takes no messages; the survey got it.", ts: TS + 5 },
  ]);
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();

  const refused = page.locator(".tool-row", { hasText: "Message to abc123 failed" });
  const sent = page.locator(".tool-row", { hasText: "Sent to survey" });
  await expect(refused).toHaveAttribute("data-state", "error");
  await expect(sent).not.toHaveAttribute("data-state", "error");
});
