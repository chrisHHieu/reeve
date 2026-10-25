// A conversation keeps receiving while the reader is in another one.
//
// The app used to hold one transcript and one socket, closing both on every switch. The server
// does not stop a turn when its socket closes, so a running session left in the background went
// on working while the browser stopped listening: returning showed a frozen snapshot, and
// whatever was streamed or asked in between was gone. Each conversation now owns its state and
// its socket (src/live/liveSession.ts). These specs are the owner's scenario, end to end:
//
//   open A → the agent runs → open B → come back to A
//
// and the app must show A as it is NOW — no reload, no blank state, nothing stale.
import { expect, type Page } from "@playwright/test";
import { dropSessionSocket, dropSessionSocketOf, seedSessionMessages, sessionFramesSent, sendSessionEventTo, sessionSocketsOpened, test } from "./fixtures";

const box = (page: Page) => page.getByPlaceholder(/Ask anything/);
const title = (page: Page) => page.locator(".main-title-text");

async function startEpicInLaunchNote(page: Page) {
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();
  await box(page).fill("stream the epic");
  await box(page).press("Enter");
  await expect(page.getByText("The epic scrolls ever onward").first()).toBeVisible({ timeout: 10_000 });
}

test("a turn finishes in the background and the conversation is complete when you return", async ({ page }) => {
  // From the second read on, the server's copy of the transcript is something else entirely.
  // If returning to the conversation re-read it, this line would replace what the events built.
  let reads = 0;
  await page.route(/\/v1\/sessions\/pinned-session-1\/messages$/, async (route) => {
    reads += 1;
    const messages = reads === 1 ? [] : [{ role: "user", content: "SERVER COPY — must never be shown" }];
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ messages }) });
  });
  await startEpicInLaunchNote(page);

  await page.getByText("Weekly plan 1").first().click();
  // The other conversation is its own transcript, not a continuation of this one.
  await expect(page.getByText("The epic scrolls ever onward")).toHaveCount(0);

  // The mock streams ~40 ticks (~5s). Stay away long enough for the whole turn to finish.
  await page.waitForTimeout(6500);

  await page.getByText("Draft the launch note").first().click();
  // No empty state, no skeleton: the finished answer is already there, built from the events
  // that arrived while the reader was elsewhere.
  await expect(page.getByText("The epic concludes.").first()).toBeVisible({ timeout: 1500 });
  await expect(page.getByRole("button", { name: /Stop/ })).toHaveCount(0);
  await expect(page.getByText("SERVER COPY")).toHaveCount(0);
});

test("coming back mid-turn picks up the same stream, still running", async ({ page }) => {
  await startEpicInLaunchNote(page);
  await page.getByText("Weekly plan 1").first().click();
  await page.waitForTimeout(1200);
  await page.getByText("Draft the launch note").first().click();

  // The turn is still going (Stop is offered) and what streamed meanwhile is on screen.
  await expect(page.getByRole("button", { name: /Stop/ })).toBeVisible();
  await expect(page.getByText("The epic scrolls ever onward").first()).toBeVisible();
  // ...and it carries on to its end without a refresh.
  await expect(page.getByText("The epic concludes.").first()).toBeVisible({ timeout: 10_000 });
  await expect(page.getByRole("button", { name: /Stop/ })).toHaveCount(0);
});

test("an approval the agent asks for while you are elsewhere is waiting when you return", async ({ page }) => {
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();
  await box(page).fill("please run a tool");
  await box(page).press("Enter");
  await expect(page.getByRole("button", { name: "Allow once" }).last()).toBeVisible();

  await page.getByText("Weekly plan 1").first().click();
  await expect(page.getByRole("button", { name: "Allow once" })).toHaveCount(0);

  await page.getByText("Draft the launch note").first().click();
  const allow = page.getByRole("button", { name: "Allow once" }).last();
  await expect(allow).toBeVisible();
  await allow.click();
  // The decision reaches the agent that has been waiting the whole time.
  await expect(page.getByText(/The command ran/)).toBeVisible();
});

test("a conversation that finished its turn elsewhere is not re-read when you return", async ({ page }) => {
  await seedSessionMessages(page, "pinned-session-1", [
    { role: "user", content: "Earlier question" },
    { role: "assistant", content: "Earlier answer" },
  ]);
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();
  await expect(page.getByText("Earlier answer")).toBeVisible();

  // The server's copy changes behind our back. Only a re-read could show it.
  await seedSessionMessages(page, "pinned-session-1", [{ role: "user", content: "SERVER COPY" }]);
  await page.getByText("Weekly plan 1").first().click();
  await page.getByText("Draft the launch note").first().click();

  await expect(page.getByText("Earlier answer")).toBeVisible();
  await expect(page.getByText("SERVER COPY")).toHaveCount(0);
});

test("switching conversations never opens a second socket for one that is already open", async ({ page }) => {
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();
  await expect(box(page)).toBeVisible();

  for (let i = 0; i < 3; i++) {
    await page.getByText("Weekly plan 1").first().click();
    await expect(title(page)).toContainText("Weekly plan 1");
    await page.getByText("Draft the launch note").first().click();
    await expect(title(page)).toContainText("Draft the launch note");
  }
  expect(sessionSocketsOpened(page, "pinned-session-1")).toBe(1);
  expect(sessionSocketsOpened(page, "wp-1")).toBe(1);
});

test("a dropped connection comes back by itself, and the conversation keeps working", async ({ page }) => {
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();
  await box(page).fill("before the drop");
  await box(page).press("Enter");
  await expect(page.getByText(/Echo: before the drop/)).toBeVisible();
  expect(sessionSocketsOpened(page, "pinned-session-1")).toBe(1);

  await dropSessionSocket(page);

  // No refresh, no navigation: the socket reopens and the next message goes through.
  await expect.poll(() => sessionSocketsOpened(page, "pinned-session-1"), { timeout: 8_000 }).toBe(2);
  await box(page).fill("after the drop");
  await box(page).press("Enter");
  await expect(page.getByText(/Echo: after the drop/)).toBeVisible();
});

test("a message sent while the connection is down is delivered when it returns", async ({ page }) => {
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();
  await expect(box(page)).toBeVisible();
  await dropSessionSocket(page);

  // Typed in the gap before the reconnect: held, then sent — not shown and silently lost.
  await expect.poll(() => sessionSocketsOpened(page, "pinned-session-1"), { timeout: 8_000 }).toBe(2);
  await box(page).fill("typed after the gap");
  await box(page).press("Enter");
  await expect(page.getByText(/Echo: typed after the gap/)).toBeVisible({ timeout: 8_000 });
});

test("a conversation that needs you while you are elsewhere says so, and takes you there", async ({ page }) => {
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();
  await expect(box(page)).toBeVisible();
  await page.getByText("Weekly plan 1").first().click();
  await expect(title(page)).toContainText("Weekly plan 1");

  // The launch-note agent, in the background, asks for approval.
  await sendSessionEventTo(page, "pinned-session-1", "turn_start", { input: "run it" });
  await sendSessionEventTo(page, "pinned-session-1", "tool_proposed", { name: "run_shell", call_id: "bg-1", arguments: { command: "ls" } });
  await sendSessionEventTo(page, "pinned-session-1", "permission_required", {
    name: "run_shell",
    arguments: { command: "ls" },
    reason: "Reeve wants to run a command.",
  });

  const toast = page.getByTestId("toast").filter({ hasText: "Draft the launch note is waiting for you" });
  await expect(toast).toBeVisible();
  // ...the sidebar says the same, in the row itself, before the list is polled
  await expect(page.getByTestId("needs-you").getByTitle("Draft the launch note")).toBeVisible();

  await toast.getByRole("button", { name: "Open" }).click();
  await expect(title(page)).toContainText("Draft the launch note");
  await expect(page.getByRole("button", { name: "Allow once" }).last()).toBeVisible();
});

test("a conversation that finishes while you are elsewhere says so", async ({ page }) => {
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();
  await page.getByText("Weekly plan 1").first().click();
  await expect(title(page)).toContainText("Weekly plan 1");

  await sendSessionEventTo(page, "pinned-session-1", "turn_start", { input: "go" });
  await sendSessionEventTo(page, "pinned-session-1", "assistant_message", { text: "All done here." });
  await sendSessionEventTo(page, "pinned-session-1", "turn_done", {});

  await expect(page.getByTestId("toast").filter({ hasText: "Draft the launch note finished" })).toBeVisible();
  // reading it is not required: the answer is waiting where the reader left it
  await page.getByText("Draft the launch note").first().click();
  await expect(page.getByText("All done here.")).toBeVisible();
});

test("the conversation you are looking at never toasts about itself", async ({ page }) => {
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();
  await box(page).fill("hello");
  await box(page).press("Enter");
  await expect(page.getByText(/Echo: hello/)).toBeVisible();
  await expect(page.getByTestId("toast")).toHaveCount(0);
});

test("a server that keeps refusing a conversation is not hammered, and the reader can try again", async ({ page }) => {
  // The server accepts, sends its error and closes before it ever says `ready` (e.g. no valid
  // workspace: `_refuse` in session_socket.py). The app tries a few times, then says so instead
  // of reconnecting for ever and writing an error line each time. The close waits a beat so the
  // page has seen the socket open: one that never opened reached no server, and is retried.
  let attempts = 0;
  await page.routeWebSocket(/\/ws\/session\/pinned-session-1/, (ws) => {
    attempts += 1;
    ws.send(JSON.stringify({ type: "error", data: { error: "no valid workspace — choose a project folder first" } }));
    setTimeout(() => ws.close(), 50);
  });
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();

  const note = page.getByTestId("connection-note");
  await expect(note).toContainText("could not open this conversation", { timeout: 15_000 });
  await expect(page.getByTestId("connection-retry")).toBeVisible();
  const settled = attempts;
  expect(settled).toBe(3);
  await page.waitForTimeout(3000);
  expect(attempts).toBe(settled);

  // Try again asks once more.
  await page.getByTestId("connection-retry").click();
  await expect.poll(() => attempts).toBe(settled + 1);
});

test("while the engine is restarting the app keeps asking, and is connected when it is back", async ({ page }) => {
  // No server answers for a while: the socket never opens. That was counted as a refusal, and
  // ~3.5s into a sidecar restart the app gave up for good and said "Not connected" until
  // reloaded (owner-hit, 2026-10-13). Four unanswered tries take ~7.5s of waiting.
  let attempts = 0;
  await page.routeWebSocket(/\/ws\/session\/pinned-session-1/, (ws) => {
    attempts += 1;
    if (attempts <= 4) {
      ws.close();
      return;
    }
    ws.send(JSON.stringify({ type: "ready", data: { mode: "workspace-write" } }));
  });
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();

  const note = page.getByTestId("connection-note");
  await expect(note).toContainText("Reconnecting");
  await expect.poll(() => attempts, { timeout: 15_000 }).toBe(5);
  await expect(note).toHaveCount(0);
  await expect(page.getByTestId("connection-retry")).toHaveCount(0);
});

test("a conversation that dropped while you were away is brought up to date when you open it", async ({ page }) => {
  await seedSessionMessages(page, "pinned-session-1", [
    { role: "user", content: "Earlier question" },
    { role: "assistant", content: "Earlier answer" },
  ]);
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();
  await expect(page.getByText("Earlier answer")).toBeVisible();
  await page.getByText("Weekly plan 1").first().click();
  await expect(title(page)).toContainText("Weekly plan 1");

  // While away: the connection drops, and the turn the agent finished in the gap is on the
  // server but was never heard.
  await seedSessionMessages(page, "pinned-session-1", [
    { role: "user", content: "Earlier question" },
    { role: "assistant", content: "Earlier answer" },
    { role: "user", content: "Asked from the TUI" },
    { role: "assistant", content: "Missed while away" },
  ]);
  await dropSessionSocketOf(page, "pinned-session-1");
  await expect.poll(() => sessionSocketsOpened(page, "pinned-session-1"), { timeout: 8_000 }).toBe(2);

  await page.getByText("Draft the launch note").first().click();
  await expect(page.getByText("Missed while away")).toBeVisible({ timeout: 5_000 });
});

test("a half-written message is still there after a look at another conversation", async ({ page }) => {
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();
  await box(page).fill("the migration plan, step 1:");

  await page.getByText("Weekly plan 1").first().click();
  await expect(title(page)).toContainText("Weekly plan 1");
  await expect(box(page)).toHaveValue(""); // not carried into the other conversation
  await box(page).fill("something else");

  await page.getByText("Draft the launch note").first().click();
  await expect(box(page)).toHaveValue("the migration plan, step 1:");
  await page.getByText("Weekly plan 1").first().click();
  await expect(box(page)).toHaveValue("something else");
});

test("a permission picked before a drop is sent again, so the picker never claims what the engine lacks", async ({ page }) => {
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();
  const chip = page.getByRole("button", { name: "Mode", exact: true });
  await chip.click();
  await page.getByTestId("mode-menu").getByText("Full access").click();
  await expect(chip).toHaveAttribute("data-permission", "full");
  await expect.poll(() => sessionFramesSent(page, "set_mode").length).toBe(1);

  // The connection drops and comes back; the fresh `ready` still reports workspace-write (the
  // engine never got the first frame, or restarted). The chip must not keep saying Full access
  // while nothing backs it, so the pick goes out again.
  await dropSessionSocket(page);
  await expect.poll(() => sessionSocketsOpened(page, "pinned-session-1"), { timeout: 8_000 }).toBe(2);
  await expect.poll(() => sessionFramesSent(page, "set_mode").length, { timeout: 5_000 }).toBe(2);
  expect(sessionFramesSent(page, "set_mode").map((f) => f.mode)).toEqual([
    "danger-full-access",
    "danger-full-access",
  ]);
  await expect(chip).toHaveAttribute("data-permission", "full");
});

test("a permission picked while the connection is down is delivered when it returns", async ({ page }) => {
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();
  await expect(box(page)).toBeVisible();
  await dropSessionSocket(page);

  const chip = page.getByRole("button", { name: "Mode", exact: true });
  await chip.click();
  await page.getByTestId("mode-menu").getByText("Read only").click();
  await expect.poll(() => sessionSocketsOpened(page, "pinned-session-1"), { timeout: 8_000 }).toBe(2);
  await expect.poll(() => sessionFramesSent(page, "set_mode").map((f) => f.mode), { timeout: 5_000 }).toContain("read-only");
});
