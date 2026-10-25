import { test, expect } from "@playwright/test";
import { scratchBaseIfReady, selectMode, sendTask, startSession } from "./helpers";

// Work survives a reload, for real: once a turn is done, the session is still in the sidebar and
// opens again with its transcript and its receipt. It is opened by name rather than trusted to be
// the one restored, since several sessions can share a second of `updated_at`. Spends tokens; by
// hand only (`npm run e2e:live`).

test("live: a session's transcript and artifact survive a page reload", async ({ page }) => {
  const scratchBase = await scratchBaseIfReady();
  test.skip(!scratchBase, "the server is not up with a model ready: start reeve-server and set a model");
  const token = `PERSIST-${Date.now()}`;
  // The file's name comes early in the prompt, so it is in the session's title however that is
  // cut, and is something to click in the sidebar.
  const name = `note-${Date.now()}.txt`;

  await startSession(page);
  await selectMode(page, "Full access");
  await sendTask(page, `Write a file named ${name} containing exactly: ${token}`);
  await expect(page.getByTestId("turn-changes")).toBeVisible({ timeout: 150_000 });
  await expect(page.getByText(token).first()).toBeVisible();

  await page.reload();
  await page.getByText(name).first().click({ timeout: 60_000 });
  // The transcript read back from disk, and the receipt with it.
  await expect(page.getByText(token).first()).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("turn-changes")).toBeVisible({ timeout: 30_000 });
});
