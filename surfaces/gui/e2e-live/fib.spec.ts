import { test, expect } from "@playwright/test";
import { readFileSync } from "fs";
import { scratchBaseIfReady, selectMode, sendTask, startSession, writtenFile } from "./helpers";

// The whole way through, for real: the app, the server and a model, asked in Full access to write
// a file, and the file read back from disk. It is what the hermetic specs mock (the model, the
// tools, the files, the stream). It spends tokens, so it runs by hand only (`npm run e2e:live`).

// A new name each run: a fib.md already right would be left alone, and nothing would be written.
const NAME = `fib-${Date.now()}.md`;
// Values unlikely to turn up in the prose, so the check holds whatever the layout.
const EXPECTED = ["144", "377", "987", "4181"];

test("live: agent writes fib.md to its scratch workspace, verified on disk", async ({ page }) => {
  const scratchBase = await scratchBaseIfReady();
  test.skip(!scratchBase, "the server is not up with a model ready: start reeve-server and set a model");

  await startSession(page);
  // No approval to wait on.
  await selectMode(page, "Full access");
  await sendTask(page, `Compute the first 20 Fibonacci numbers and write them to ${NAME} with a one-line explanation at the top.`);
  // The turn's receipt comes once the write has run.
  await expect(page.getByTestId("turn-changes")).toBeVisible({ timeout: 150_000 });

  const file = await writtenFile(scratchBase!, NAME);
  expect(file, `no ${NAME} under ${scratchBase} or the default folder`).toBeTruthy();
  const text = readFileSync(file!, "utf8");
  for (const value of EXPECTED) expect(text, `${NAME} should hold ${value}`).toContain(value);
});
