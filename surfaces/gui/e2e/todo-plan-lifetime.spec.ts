// The standing plan (src/todoPlan.ts): the To-dos dock shows the latest list the model
// wrote, until it writes another. The engine keeps that list across turns (and re-injects
// it after compaction), so a new turn that has not written its own plan yet is still
// working from it. Clearing the dock at `turn_start` hid it — and reload, which folds the
// log with no turn boundary, showed it again. The dock is open while a turn is live and a
// closed chip between turns (the task's head, SessionDock.tsx), so the checks below are made in both states.
import { expect } from "@playwright/test";
import { test } from "./fixtures";

test("the plan stands into the next turn until the model writes another", async ({ page }) => {
  await page.goto("/");
  const box = page.getByPlaceholder(/Ask anything/);
  const send = async (text: string) => {
    await box.fill(text);
    await page.getByRole("button", { name: "Send", exact: true }).click();
  };
  const holdOnApproval = async () => {
    await send("please run a tool");
    await expect(page.getByText("Reeve wants to run a command.").first()).toBeVisible();
  };
  const release = async () => {
    await page.getByRole("button", { name: "Allow once" }).last().click();
    await expect(page.getByText("The command ran; 1 file found.").last()).toBeVisible();
  };

  await send("make a checklist");
  await expect(page.getByText("Both steps done.")).toBeVisible();
  // The turn is over: the plan stays as a closed chip that says it finished.
  await expect(page.getByTestId("todo-dock")).toHaveAttribute("data-state", "settled");
  await expect(page.getByTestId("todo-dock-progress")).toHaveText("2 of 2 done");

  // Turn 2 writes nothing: the plan turn 1 wrote is still the standing one, in the task's
  // head the whole time (2026-10-13: it no longer shares the space above the input with the
  // approval, so it does not step aside for it).
  await holdOnApproval();
  await expect(page.getByTestId("todo-dock-progress")).toHaveText("2 of 2 done");
  await release();
  await expect(page.getByTestId("todo-dock-progress")).toHaveText("2 of 2 done");

  // A new write replaces it.
  await send("plan the release note");
  await expect(page.getByText("Drafting it now.")).toBeVisible();
  await holdOnApproval();
  const dock = page.getByTestId("todo-dock");
  await expect(dock).toBeVisible(); // in the head while the approval waits
  await release();
  await expect(page.getByTestId("todo-dock-progress")).toHaveText("0 of 2");
  await expect(page.getByTestId("todo-dock-progress")).toHaveAttribute("title", /1 in progress.*1 pending/);
  await expect(dock).toContainText("Draft the release note");
  await expect(dock).not.toContainText("Patch the flaky test");
});
