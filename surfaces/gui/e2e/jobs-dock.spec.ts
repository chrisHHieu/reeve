// The jobs segment of the activity row against the hermetic fixture, which does what the
// engine does: the job joins the roster in the MIDDLE of a turn and the roster is pushed on
// the session socket (`jobs` frames); the job's text is streamed as coalesced
// `job_output` frames, one of them skipping ahead.
// The segment shows a job while it runs and says what it is printing without being opened;
// with nothing running it is the quiet count of what finished. Its panel holds the finished
// list, the exit codes and Clear (a header chip held them until 2026-10-12).
import { expect } from "@playwright/test";
import { test } from "./fixtures";

test("a job started mid-turn shows at once, moving, and nothing polls for it", async ({ page }) => {
  // Every read of the list, counted: the client makes ONE, when its socket opens.
  let listReads = 0;
  page.on("request", (request) => {
    if (request.method() === "GET" && /\/v1\/sessions\/[^/]+\/jobs$/.test(new URL(request.url()).pathname)) {
      listReads += 1;
    }
  });
  await page.goto("/");
  await page.getByPlaceholder(/Ask anything/).fill("start a background job");
  await page.getByRole("button", { name: "Send" }).click();

  const dock = page.getByTestId("jobs-dock");
  await expect(dock).toBeVisible();
  await expect(page.getByTestId("jobs-dock-progress")).toContainText("1 running");
  // The closed header names the running command AND what it last printed — pushed with the
  // roster, colour codes stripped — so the reader does not have to open anything.
  await expect(page.getByTestId("jobs-dock-toggle")).toContainText("npm run build");
  await expect(page.getByTestId("jobs-dock-said")).toHaveText("✓ built in 4.31s");

  await page.getByTestId("jobs-dock-toggle").click();
  // The list opens under the row, in the segment's own panel.
  const panel = page.getByTestId("jobs-dock-body");
  const row = panel.getByTestId("job-row").first();
  await expect(row).toContainText("npm run build");
  await expect(row.getByTestId("job-last-line")).toHaveText("✓ built in 4.31s");
  // Running is the moving mark, never a solid dot.
  await expect(panel.getByTestId("job-running-dot").first()).toBeVisible();

  // The whole row is the door to the log: it observes the job and anchors on its tail, and
  // the stream adds to it after.
  await row.getByTestId("job-expand").click();
  const output = panel.getByTestId("job-output");
  // The log is the transcript's own terminal card, for the shell and folder the job ran in.
  await expect(output.locator("[data-terminal]")).toHaveAttribute("data-shell", "powershell");
  await expect(output.getByTestId("terminal-command")).toHaveText("npm run build");
  // The anchor (the tail route's backlog), which says it is a shortened log…
  await expect(output).toContainText("modules transformed");
  await expect(output.getByTestId("job-output-cut")).toBeVisible();
  // …then what the socket delivered beyond it.
  await expect(output).toContainText("vite building the client bundle");
  await expect(output).toContainText("built in 4.31s");
  // The second frame skipped ahead, and the log admits that hole instead of closing it.
  await expect(output).toContainText("… output dropped …");

  // First press arms; only the second one stops the job.
  const stop = panel.getByTestId("job-stop");
  await stop.click();
  await expect(stop).toHaveAttribute("data-kill-state", "armed");
  await expect(stop).toContainText("Confirm stop");
  await stop.click();

  // Stopped — the roster said so, nobody asked: the segment has nothing live to say, and
  // reads as the quiet count of what finished.
  await expect(dock).toHaveAttribute("data-state", "done");
  await expect(page.getByTestId("jobs-dock-progress")).toHaveText("2 finished");
  await expect(page.locator(".main-topbar")).not.toContainText(/background job/i);
  expect(listReads).toBe(1);
});

test("the panel lists the finished jobs behind their fold, and Clear forgets them", async ({ page }) => {
  await page.goto("/");
  await page.getByPlaceholder(/Ask anything/).fill("start a background job");
  await page.getByRole("button", { name: "Send" }).click();

  await page.getByTestId("jobs-dock-toggle").click();
  const panel = page.getByTestId("jobs-dock-body");
  // A log is read when its row is opened, never because the panel was.
  await expect(panel.getByTestId("job-output")).toHaveCount(0);
  await panel.getByTestId("job-expand").first().click();
  await expect(panel.getByTestId("job-output")).toContainText("built in 4.31s");

  // The finished fixture job, behind the fold while work is in flight: its verdict and the
  // last thing it printed, on the row.
  const fold = page.getByTestId("jobs-finished-toggle");
  await expect(fold).toHaveText("Finished 1");
  await expect(panel.getByTestId("job-row")).toHaveCount(1);
  await fold.click();
  const done = panel.getByTestId("job-row").nth(1);
  await expect(done).toHaveAttribute("data-tone", "ok");
  await expect(done).toContainText("exit 0");
  await expect(done.getByTestId("job-last-line")).toHaveText("Tests 1653 passed (1653)");

  await page.getByTestId("jobs-clear").click();
  await expect(panel.getByTestId("job-row")).toHaveCount(1);
  await expect(page.getByTestId("jobs-finished-toggle")).toHaveCount(0);
});

test("a stopped job reads as stopped, not as a failure", async ({ page }) => {
  await page.goto("/");
  await page.getByPlaceholder(/Ask anything/).fill("start a background job");
  await page.getByRole("button", { name: "Send" }).click();
  await page.getByTestId("jobs-dock-toggle").click();
  const panel = page.getByTestId("jobs-dock-body");
  const stop = panel.getByTestId("job-stop");
  await stop.click();
  await stop.click();
  // The pushed roster settles the row: the Stop control leaves with the running state, and
  // with nothing running the finished ones are shown.
  await expect(panel.getByTestId("job-stop")).toHaveCount(0);
  const stopped = panel.locator('[data-testid="job-row"][data-tone="stopped"]');
  await expect(stopped).toHaveCount(1);
  await expect(stopped.getByTestId("job-outcome")).toHaveText("stopped");
});
