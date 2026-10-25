// A reopened session, drawn from its saved transcript (GET /v1/sessions/{id}/messages, through
// itemsFromMessages) with no turn played live. The fake agent's echo cannot reach these: calls
// replayed with their results, what the privacy filters hid, reasoning, saved notices, and
// messages that came in through a connector.
import { expect, type Page } from "@playwright/test";
import { test, seedSessionMessages } from "./fixtures";

// A fixed time: nothing replayed may depend on the clock.
const TS = 1755600000;

/** Open the pinned session with `messages` as its saved transcript. */
async function reopen(page: Page, messages: Record<string, unknown>[]) {
  await seedSessionMessages(page, "pinned-session-1", messages);
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();
}

const call = (id: string, name: string, args: Record<string, unknown>) => ({ id, function: { name, arguments: JSON.stringify(args) } });

test("a reopened session replays rich history: tools, filters, reasoning, notices", async ({ page }) => {
  await reopen(page, [
    { role: "user", content: "Audit the release branch", ts: TS },
    { role: "assistant", content: "", tool_calls: [call("t1", "run_shell", { command: "git log --oneline -5" }), call("t2", "read_file", { path: "CHANGELOG.md" })] },
    { role: "tool", tool_call_id: "t1", content: "abc123 release: cut 0.1.7" },
    {
      role: "tool", tool_call_id: "t2", content: "     1	## 0.1.7", _display: { hidden_by_filters: 3 },
      // The tool's own account of its result, its card with it. A reopened session draws the row
      // the live one drew from this; from the model's text alone every read is a bare line.
      _presentation: {
        summary: "Read CHANGELOG.md", kind: "read",
        card: { card: "read", path: "CHANGELOG.md", offset: 1, total_lines: 40, lines: [{ number: 1, text: "## 0.1.7" }] },
      },
    },
    {
      role: "assistant", content: "The branch is clean — **two checks** passed.",
      reasoning: "Compared the log against the changelog; both entries line up.", ts: TS + 40,
    },
    { role: "notice", kind: "compacted", text: "Context compacted" },
    { role: "assistant", content: "Anything else before I file the summary?" },
  ]);

  await expect(page.getByText("Audit the release branch")).toBeVisible();
  await expect(page.locator(".md strong", { hasText: "two checks" })).toBeVisible();
  await expect(page.getByTestId("compaction-divider")).toContainText("Compacted");

  // The turn's status line, under its steps, gives the time and what the filters hid. The time
  // runs to the answer's stamp, which the Thought row inside the turn carries: 40 seconds.
  const turn = page.locator(".stepgroup").first();
  await expect(turn).toContainText("Worked for 40s");
  await expect(page.getByTestId("stepgroup-hidden")).toContainText("3 hidden");
  // A row a call, nothing grouped.
  await expect(page.getByTestId("turn-step")).toHaveCount(2);

  // The read's card is back after the reload, from the saved account, not worked out again from
  // the text. Its title is what is clicked: the middle of a file's row can be its link, which
  // opens the file instead of the row.
  await turn.locator("[data-disclosure-row]").nth(1).locator(".tool-row-title").click();
  await expect(page.getByTestId("read-body")).toContainText("## 0.1.7");
  await expect(page.getByTestId("read-window")).toContainText("1 / 40");

  // Reasoning comes back as the same folded Thought row, settled.
  await expect(page.getByTestId("think-row").first()).toHaveAttribute("data-state", "ok");
});

test("a connector-sourced message replays as its structured card", async ({ page }) => {
  const said = "Ship it when the checks are green";
  await reopen(page, [
    {
      role: "user", content: `[slack] Priya: ${said}`,
      source: { connector: "slack", kind: "channel", channel_id: "C0REL", channel_name: "#release", sender_id: "U1", sender_name: "Priya", ts: TS, text: said },
    },
    { role: "assistant", content: "Will do — watching the checks now." },
  ]);

  const card = page.locator(".connector-card[data-brand='slack']");
  await expect(card).toBeVisible();
  await expect(card).toContainText("Priya");
  await expect(card).toContainText(said);
  // Drawn once, as the card: the framed text the model read is not a bubble of its own as well.
  await expect(page.getByText("[slack] Priya:")).toHaveCount(0);
});

test("a replayed error notice at the tail offers Retry", async ({ page }) => {
  await reopen(page, [
    { role: "user", content: "run the report", ts: TS },
    { role: "notice", kind: "error", text: "provider unavailable" },
  ]);

  await expect(page.getByText("Error: provider unavailable")).toBeVisible();
  await expect(page.getByTestId("notice-retry")).toBeVisible();
});

// A server that would not start is one quiet line naming it (owner, 2026-08-21: never a wall of
// stderr in the transcript). The error itself waits behind Details, and Open Connectors is where
// it is fixed.
test("a dead MCP server replays as one quiet line with Details and Open Connectors", async ({ page }) => {
  await reopen(page, [
    { role: "user", content: "hi", ts: TS },
    { role: "assistant", content: "Hello!" },
    {
      role: "notice", kind: "mcp_error", server: "sales-db",
      text: "MCP server “sales-db” failed to start: unhandled errors in a TaskGroup — aws configure export-credentials --profile aicreator exited 255",
    },
  ]);

  const line = page.getByTestId("mcp-notice");
  await expect(line).toContainText("sales-db");
  await expect(line).toContainText("didn’t start");
  await expect(page.getByTestId("mcp-notice-detail")).toHaveCount(0);
  await page.getByTestId("mcp-notice-details").click();
  await expect(page.getByTestId("mcp-notice-detail")).toContainText("TaskGroup");

  await page.getByTestId("mcp-notice-connectors").click();
  await expect(page.getByText("Connectors", { exact: true }).first()).toBeVisible();
});

// Sessions saved before the notice named its server kept the whole text and a written pointer
// to Settings. The name is read back out of the text when it is drawn, so old transcripts get
// the quiet line too, with the pointer left out: the button is the way there now.
test("a LEGACY mcp_error notice (pre-server-field) also collapses to the quiet line", async ({ page }) => {
  await reopen(page, [
    { role: "user", content: "hi", ts: TS },
    {
      role: "notice", kind: "mcp_error",
      text: "MCP server “sales-db” failed to start: unhandled errors in a TaskGroup <function f at 0x102ab40f0> — see Settings ▸ Connectors",
    },
  ]);

  await expect(page.getByTestId("mcp-notice")).toContainText("sales-db");
  await page.getByTestId("mcp-notice-details").click();
  const detail = page.getByTestId("mcp-notice-detail");
  await expect(detail).toContainText("TaskGroup");
  await expect(detail).not.toContainText("see Settings");
});
