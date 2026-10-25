// The permission dock, end to end (P1 / ledger 5.7): a child's parked ask surfaces above
// the composer of the session you are viewing, a `task` spawn ask says "Launch
// sub-agents" and its description, replying hits `/v1/permissions/{id}/reply`, and a
// `permission_replied` frame clears the dock. An ask for a session you are NOT looking at
// arrives as a dismissible toast with a way over.
import { expect } from "@playwright/test";
import { sendAppEvent, test } from "./fixtures";

// The pinned conversation is what the app resumes at boot; the delegated-run fixture
// already announces `explore-2` as its child.
async function openPinned(page: import("@playwright/test").Page) {
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();
  await expect(page.getByPlaceholder(/Ask anything/)).toBeVisible();
}

test("a child's permission ask docks above the composer and once replies", async ({
  page,
}) => {
  await openPinned(page);
  const box = page.getByPlaceholder(/Ask anything/);
  await box.fill("delegate the research");
  await box.press("Enter");
  // The child exists on the app-wide stream (the fixture announces `explore-2`), so the
  // ask below resolves to the viewed session's subtree. The child CARD cannot anchor this
  // wait: a settled delegation folds into its card header, collapsed with the turn.
  await expect(page.getByText("The explorer reported back — see the run.").first()).toBeVisible({
    timeout: 10_000,
  });

  await sendAppEvent(page, {
    type: "permission_asked",
    data: {
      request_id: "per_dock_1",
      session_id: "explore-2",
      parent_session: "pinned-session-1",
      permission: "run_shell",
      patterns: ["rm -rf build"],
      metadata: {},
      always: ["rm -rf build"],
      tool: { call_id: "sub-1" },
    },
  });

  const dock = page.getByTestId("permission-dock");
  await expect(dock).toBeVisible();
  await expect(dock).toContainText("Asked by");
  await expect(page.getByTestId("permission-title")).toHaveText("run_shell");
  await expect(page.getByTestId("permission-detail")).toHaveText("rm -rf build");

  await page.getByTestId("permission-once").click();
  await expect(dock).toHaveCount(0);
});

test("a task spawn ask shows the sub-agents hint and a reply frame clears the dock", async ({
  page,
}) => {
  await openPinned(page);
  await sendAppEvent(page, {
    type: "permission_asked",
    data: {
      request_id: "per_dock_2",
      session_id: "pinned-session-1",
      parent_session: null,
      permission: "task",
      patterns: ["general"],
      metadata: { description: "delegate the lookup", subagent_type: "general" },
      always: ["*"],
      tool: { call_id: "call_task" },
    },
  });

  const dock = page.getByTestId("permission-dock");
  await expect(dock).toBeVisible();
  await expect(page.getByTestId("permission-title")).toHaveText("Launch sub-agents");
  await expect(page.getByTestId("permission-detail")).toHaveText(
    "delegate the lookup",
  );

  // A reply from anywhere else (another window, an API client) clears it by frame.
  await sendAppEvent(page, {
    type: "permission_replied",
    data: { request_id: "per_dock_2", session_id: "pinned-session-1", reply: "once" },
  });
  await expect(dock).toHaveCount(0);
});

test("an ask for another session toasts with Go to session and opens it", async ({
  page,
}) => {
  await openPinned(page);
  await sendAppEvent(page, {
    type: "permission_asked",
    data: {
      request_id: "per_dock_3",
      session_id: "wp-1",
      parent_session: null,
      permission: "write_file",
      patterns: ["src/fetch_data.py"],
      metadata: {},
      always: ["src/fetch_data.py"],
      tool: null,
    },
  });

  // Not the viewed session's subtree: a toast, not the dock.
  const toast = page.getByTestId("toast");
  await expect(toast).toContainText("Permission required");
  await expect(toast).toContainText("write_file");
  await expect(page.getByTestId("permission-dock")).toHaveCount(0);

  await page.getByRole("button", { name: "Go to session" }).click();
  // The dock now belongs to the opened session.
  await expect(page.getByTestId("permission-dock")).toBeVisible();
  await expect(page.getByTestId("permission-detail")).toHaveText(
    "src/fetch_data.py",
  );
});
