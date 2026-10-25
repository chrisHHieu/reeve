// A background workflow, in a session opened after it ran (owner, 2026-10-13): the roster above
// the composer used to know a run only from live frames, so a reload dropped it; its agents were
// a level below and never listed; and its report read "Subagent finished". Now the stored call
// names its runner, the row lists the runner's agents, and the report says what it was.
import { expect } from "@playwright/test";
import { test, seedSessionMessages } from "./fixtures";

const TS = 1755600000;

test("a reopened background workflow is in the roster, with its agents, and reports as a workflow", async ({ page }) => {
  await page.route(/\/v1\/sessions\/runner-1\/children$/, (route) =>
    route.fulfill({
      json: {
        children: [
          { session_id: "agent-a", title: "shop/cart.py (@general subagent)", agent: "general", status: "idle" },
          { session_id: "agent-b", title: "shop/cache.py (@general subagent)", agent: "general", status: "idle" },
        ],
      },
    }),
  );
  await seedSessionMessages(page, "pinned-session-1", [
    { role: "user", content: "review the shop files", ts: TS },
    {
      role: "assistant",
      content: "",
      tool_calls: [
        {
          id: "w1",
          function: {
            name: "workflow",
            arguments: JSON.stringify({ meta: { name: "file-review", description: "Review each file in shop/" }, script: "…" }),
          },
        },
      ],
    },
    {
      role: "tool",
      tool_call_id: "w1",
      content: '<task id="runner-1" state="running">The workflow is running in the background.</task>',
      _presentation: { summary: "The file-review workflow is running in the background", kind: "execute", run_id: "runner-1" },
    },
    { role: "assistant", content: "Started the review.", ts: TS + 2 },
    {
      role: "user",
      content: '<task id="runner-1" state="completed">…</task>',
      source: { synthetic: "background_task", session_id: "runner-1", title: "file-review workflow", state: "completed", workflow: true, agents: 2 },
      ts: TS + 20,
    },
    { role: "assistant", content: "Both files reviewed.", ts: TS + 25 },
  ]);
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();

  // The report says what reported back.
  await expect(page.getByTestId("wake-marker")).toContainText("Workflow finished");
  await expect(page.getByTestId("wake-marker")).toContainText("2 agents");

  // The run is in the roster from the reload alone, and its agents are under it.
  await page.getByTestId("subagents-dock-toggle").click();
  const row = page.getByTestId("subagent-row");
  await expect(row).toContainText("Review each file in shop/");
  await page.getByTestId("subagent-row-toggle").click();
  const agents = page.getByTestId("workflow-agent");
  await expect(agents).toHaveCount(2);
  await expect(agents.first()).toContainText("shop/cart.py");
});
