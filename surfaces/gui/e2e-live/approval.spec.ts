import { test, expect, type Page } from "@playwright/test";
import { readFileSync } from "fs";
import { scratchBaseIfReady, selectMode, sendTask, startSession, writtenFile } from "./helpers";

// The approval, for real: in Workspace write a write waits on its card in the transcript, and runs
// once allowed. The spec picks the mode itself, since the server's default is the person's own
// setting. Spends tokens; by hand only (`npm run e2e:live`).

// A write's card says "Allow"; a command's, "Allow once".
const allowButton = (page: Page) => page.getByRole("button", { name: /^Allow( once)?$/ }).first();

test("live: a write blocks on an approval card, then completes once approved", async ({ page }) => {
  const scratchBase = await scratchBaseIfReady();
  test.skip(!scratchBase, "the server is not up with a model ready: start reeve-server and set a model");
  // A new name each run, so "not there before it is allowed" cannot see an earlier run's file.
  const name = `hello-${Date.now()}.txt`;

  await startSession(page);
  await selectMode(page, "Workspace write");
  await sendTask(page, `Create a file named ${name} containing exactly the text: hello world`);
  await expect(allowButton(page)).toBeVisible({ timeout: 120_000 });
  expect(await writtenFile(scratchBase!, name), "the file is written before it was allowed").toBeNull();

  // Allow it, and whatever else the turn asks before it ends: the model often checks the file with
  // a command, which waits too in this mode.
  await allowButton(page).click();
  const receipt = page.getByTestId("turn-changes");
  for (let asked = 0; asked < 6 && !(await receipt.isVisible()); asked++) {
    const next = allowButton(page);
    await Promise.race([
      next.waitFor({ state: "visible", timeout: 60_000 }).then(() => next.click()),
      receipt.waitFor({ state: "visible", timeout: 60_000 }),
    ]).catch(() => {});
  }

  await expect(receipt).toBeVisible({ timeout: 120_000 });
  const file = await writtenFile(scratchBase!, name);
  expect(file, `no ${name} under ${scratchBase}`).toBeTruthy();
  expect(readFileSync(file!, "utf8").toLowerCase()).toContain("hello world");
});
