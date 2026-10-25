// A live approval and the copy of it the server parks. Every approval the engine raises is saved
// on the server as well, which is how one survives a reload; the conversation polls for what is
// parked, and a copy still pending for a poll after the answer must not show as a second request.
// (No mode sends approvals out of the conversation: "unattended" went with the Inbox.)
import { expect } from "@playwright/test";
import { test } from "./fixtures";
import { composer, say } from "./steps";

const ASKED = "Reeve wants to run a command.";

test("a tool request surfaces the approval card in its conversation", async ({ page }) => {
  await page.goto("/");
  await say(page, "please run a tool");
  await expect(page.getByText(ASKED).first()).toBeVisible();
});

// Found by a tester on 2026-07-12: after "Allow once" the polled copy stayed pending for up to a
// poll, and the card answering in context showed the same request again. Here, once the live
// approval exists, the session's inbox read returns one pending approval until the answer goes;
// the mock's own items belong to other sessions. The real server resolves its copy with the
// answer, so only the client's polled copy is stale, which is the case pinned. (Parked before the
// first turn, a pending item docks in the composer's place, and there is nothing to type into.)
test("answering the live approval never re-flashes its parked copy", async ({ page }) => {
  let parked: "not yet" | "pending" | "resolved" = "not yet";
  let servedPending: () => void = () => {};
  const pendingServed = new Promise<void>((resolve) => (servedPending = resolve));
  await page.route(/\/v1\/inbox\?/, async (route) => {
    const sid = new URL(route.request().url()).searchParams.get("session_id");
    if (!sid || sid === "wp-3" || sid === "ops-1" || parked === "not yet") return route.fallback();
    if (parked === "pending") servedPending();
    const copy = {
      id: "mirror-1", session_id: sid, kind: "approval", title: "Run `run_shell`?", body: "requires approval",
      state: "pending", resolution: null, inbox: "default", created_at: "2026-07-12 10:00:00", resolved_at: null,
    };
    return route.fulfill({ json: { items: parked === "pending" ? [copy] : [] } });
  });

  await page.goto("/");
  await composer(page).fill("please run a tool");
  parked = "pending";
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(page.getByText(ASKED).first()).toBeVisible();
  // The client has to be holding the stale copy before the answer, or there is nothing to show
  // again. It polls every 4 seconds: wait for one pending copy to be served, and a frame for it.
  await pendingServed;
  await page.waitForTimeout(100);

  parked = "resolved";
  await page.getByRole("button", { name: "Allow once" }).last().click();
  // Before the fix the stale copy showed within a frame of the click and was gone a poll later,
  // so a plain toHaveCount(0) could pass between the two. The window after the click is watched.
  const shownAgain = await page.getByText("Run `run_shell`?").waitFor({ state: "visible", timeout: 700 }).then(() => true, () => false);
  expect(shownAgain).toBe(false);
  await expect(page.getByText("The command ran; 1 file found.")).toBeVisible();
});
