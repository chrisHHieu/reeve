// The workspace panel's two file surfaces answer different questions (2026-09-29):
// Review — what changed, and do I keep it — diff first, the shape Codex, Claude Code Desktop
// and Conductor share (a scope menu, every changed file stacked with its diff, a file tree,
// Commit); Files — what is in the folders right now. They link both ways.
import { expect } from "@playwright/test";
import { test } from "./fixtures";

// The panel pinned at its narrow width (512px); the work pane's own default is wider (layout
// step 3). Review is one column at any width.
test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("reeve:rail-width:v2", "512"));
});

/** Opens a file's card in Review's column, unless it is open already. */
async function openCard(page: import("@playwright/test").Page, path: string) {
  const card = page.getByTestId(`rvw-card-${path}`);
  if (!/\bopen\b/.test((await card.getAttribute("class")) ?? "")) await page.getByTestId(`rvw-toggle-${path}`).click();
}

test("Review is diff first: scopes, viewed, stage, revert, commit", async ({ page }) => {
  await page.goto("/");
  await page.getByPlaceholder(/Ask anything/).fill("edit the upload client");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByText("Added retry and a test.")).toBeVisible();
  // The panel stays on its own start page: a turn that changed files does not open Review.

  // Start: each tab names the question it answers.
  const row = (door: string) => page.locator(".guide-row", { has: page.getByTestId(`rail-door-${door}`) });
  await expect(row("review")).toContainText("What changed, and do I keep it?");
  await expect(row("files")).toContainText("What is in my folders right now?");
  // The Review row says how much changed before it is opened.
  await expect(page.getByTestId("rail-door-review-fact")).toContainText("3 files");
  await page.getByTestId("rail-guide").screenshot({ path: test.info().outputPath("start.png") });

  await page.getByTestId("rail-door-review").click();
  const review = page.getByTestId("review-pane");
  await expect(page.getByTestId("rvw-scope")).toContainText("Uncommitted");
  await expect(page.getByTestId("rvw-count")).toHaveText("3 files");
  // One column (2026-10-14): the files Reeve changed for a request sit under the person's words,
  // the rest apart; git's untracked file reads U. The tab wears the count.
  const ask = page.getByTestId("rvw-ask-1");
  await expect(ask).toContainText("edit the upload client");
  await expect(ask.getByTestId("rvw-card-test_upload.py")).toContainText("U");
  await expect(page.getByTestId("rvw-outside").getByTestId("rvw-card-settings.toml")).toBeVisible();
  await expect(page.getByTestId("rail-tab-count-review")).toHaveText("3");
  await page.getByTestId("rail-tabs").screenshot({ path: test.info().outputPath("rail-strip.png") });
  await review.screenshot({ path: test.info().outputPath("review-narrow.png") });

  // The first card is open without a click.
  const notes = page.getByTestId("rvw-card-notes.md");
  await expect(notes.getByTestId("diffview")).toContainText("retry uploads up to three times");
  await review.screenshot({ path: test.info().outputPath("review-file.png") });

  // Stage it from its header (Stage and Revert show on the header the pointer is on).
  await notes.locator(".rvw-card-head").hover();
  await page.getByTestId("rvw-stage-notes.md").click();
  await expect(notes).toContainText("Staged");

  // Viewed: the card folds and the next one not viewed opens.
  await page.getByTestId("rvw-viewed-notes.md").check();
  await expect(notes).not.toHaveClass(/\bopen\b/);
  await expect(page.getByTestId("rvw-card-test_upload.py")).toHaveClass(/\bopen\b/);
  await expect(page.getByTestId("rvw-progress")).toContainText("Viewed 1 of 3");
  // A diff says how many lines between its hunks it leaves out.
  await page.getByTestId("rvw-toggle-settings.toml").click();
  const settings = page.getByTestId("rvw-card-settings.toml");
  await expect(settings.getByTestId("diffview")).toContainText("35 unmodified lines");

  // Full screen: the same column, wider.
  await page.getByTestId("rail-fullscreen").click();
  await expect(page.getByTestId("rvw-cards")).toBeVisible();
  await review.screenshot({ path: test.info().outputPath("review-wide.png") });
  await page.getByTestId("rail-fullscreen").click();

  // Revert asks first, then removes exactly that file.
  await page.getByTestId("rvw-card-test_upload.py").locator(".rvw-card-head").hover();
  await page.getByTestId("rvw-revert-test_upload.py").click();
  await expect(page.getByTestId("scm-discard-dialog")).toContainText("cannot be undone");
  await page.getByTestId("scm-discard-confirm").click();
  await expect(page.getByTestId("rvw-card-test_upload.py")).toHaveCount(0);
  await expect(page.getByTestId("rvw-count")).toHaveText("2 files");

  // Commit: the box is at the foot, and the line under the bar says what is unpushed.
  await expect(page.getByTestId("rvw-sync")).toContainText("1 commit not pushed");
  await expect(page.getByTestId("rvw-commit")).toContainText("Write a message first");
  await page.getByTestId("rvw-commit-message").fill("Retry uploads");
  await review.screenshot({ path: test.info().outputPath("review-commit.png") });
  await page.getByTestId("rvw-commit-message").press("Control+Enter");
  await expect(page.getByTestId("rvw-toast")).toContainText("Committed d41f7c2 to main");
  await expect(page.getByTestId("rvw-clean")).toContainText("No uncommitted changes");
  await review.screenshot({ path: test.info().outputPath("review-after-commit.png") });

  // Reeve's own work is one scope away, by turn, in the user's words.
  await page.getByTestId("rvw-scope").click();
  await review.screenshot({ path: test.info().outputPath("review-scopes.png") });
  await page.getByTestId("rvw-scope-session").click();
  await expect(page.getByTestId("recorded-turn-1")).toContainText("edit the upload client");
});

test("Review compares the index, the edits, the branch and one commit; hunks and bulk actions", async ({ page }) => {
  await page.goto("/");
  await page.getByPlaceholder(/Ask anything/).fill("edit the upload client");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByText("Added retry and a test.")).toBeVisible();
  // The panel does not open Review by itself (owner, 2026-10-16): the person opens it.
  await page.getByTestId("rail-door-review").click();
  await expect(page.getByTestId("review-pane")).toBeVisible();
  const review = page.getByTestId("review-pane");
  await page.getByTestId("rail-fullscreen").click();

  // Staged: the index, with Unstage on each file and on each hunk.
  await page.getByTestId("rvw-scope").click();
  await expect(page.getByTestId("rvw-scope-branch")).toContainText("Against main");
  await page.getByTestId("rvw-scope-staged").click();
  await expect(page.getByTestId("rvw-scope")).toContainText("Staged");
  await expect(page.getByTestId("rvw-count")).toHaveText("1 file");
  const settings = page.getByTestId("rvw-card-settings.toml");
  await settings.locator(".rvw-card-head").hover();
  await expect(settings.getByTestId("rvw-unstage-settings.toml")).toBeVisible();
  await expect(settings.getByTestId("rvw-hunk-unstage-settings.toml-1")).toBeVisible();
  await review.screenshot({ path: test.info().outputPath("review-staged.png") });

  // Unstage the second hunk: the server is told which hunk, by index and by its header.
  const [unstage] = await Promise.all([
    page.waitForRequest((r) => r.url().endsWith("/scm/hunk") && r.method() === "POST"),
    settings.getByTestId("rvw-hunk-unstage-settings.toml-1").click(),
  ]);
  expect(unstage.postDataJSON()).toEqual({ path: "settings.toml", index: 1, header: "@@ -40,2 +40,3 @@", action: "unstage" });

  // Unstaged: the edits, with Stage hunk / Revert hunk; Revert asks first.
  await page.getByTestId("rvw-scope").click();
  await page.getByTestId("rvw-scope-unstaged").click();
  // settings.toml (unstaged again above) has two hunks, so each hunk has its own buttons;
  // a one-hunk file has none, the file's own Stage / Revert being the same action.
  await openCard(page, "notes.md");
  await expect(page.getByTestId("rvw-card-notes.md").getByTestId("diffview")).toBeVisible();
  await expect(page.getByTestId("rvw-card-notes.md").getByTestId("rvw-hunk-stage-notes.md-0")).toHaveCount(0);
  await openCard(page, "settings.toml");
  const unstagedSettings = page.getByTestId("rvw-card-settings.toml");
  await expect(unstagedSettings.getByTestId("rvw-hunk-stage-settings.toml-0")).toBeVisible();
  await review.screenshot({ path: test.info().outputPath("review-unstaged.png") });
  await unstagedSettings.getByTestId("rvw-hunk-revert-settings.toml-0").click();
  await expect(page.getByTestId("scm-discard-dialog")).toContainText("cannot be undone");
  await page.getByRole("button", { name: "Cancel" }).click();

  // Stage everything from the options menu.
  await page.getByTestId("rvw-options").click();
  await expect(page.getByTestId("rvw-revert-all")).toBeVisible();
  const [stageAll] = await Promise.all([
    page.waitForRequest((r) => r.url().endsWith("/scm/stage") && r.method() === "POST"),
    page.getByTestId("rvw-stage-all").click(),
  ]);
  expect(stageAll.postDataJSON().paths.length).toBeGreaterThan(1);

  // The branch against main is looked at, not changed.
  await page.getByTestId("rvw-scope").click();
  await page.getByTestId("rvw-scope-branch").click();
  await expect(page.getByTestId("rvw-scope-note")).toContainText("main");
  await openCard(page, "upload_client.py");
  await expect(page.getByTestId("rvw-card-upload_client.py")).toBeVisible();
  await expect(page.getByTestId("rvw-stage-upload_client.py")).toHaveCount(0);
  await expect(page.getByTestId("rvw-commit")).toHaveCount(0);

  // A commit, picked from the history: newest first, the tip and the remote marked.
  await page.getByTestId("rvw-scope").click();
  await page.getByTestId("rvw-scope-commits").click();
  await expect(page.getByTestId("rvw-history")).toContainText("Add the upload client");
  await expect(page.getByTestId("rvw-history")).toContainText("origin/main");
  await page.getByTestId("rvw-history").screenshot({ path: test.info().outputPath("review-history.png") });
  await page.getByTestId("rvw-commit-c2e1a9f").click();
  await expect(page.getByTestId("rvw-scope")).toContainText("Commit c2e1a9f");
  await expect(page.getByTestId("rvw-scope-note")).toContainText("Add the upload client");
  await expect(page.getByTestId("rvw-count")).toHaveText("2 files");
  await review.screenshot({ path: test.info().outputPath("review-commit-scope.png") });
});

test("Review code, Write it for me, commit and push", async ({ page }) => {
  await page.goto("/");
  await page.getByPlaceholder(/Ask anything/).fill("edit the upload client");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByText("Added retry and a test.")).toBeVisible();
  // The panel does not open Review by itself (owner, 2026-10-16): the person opens it.
  await page.getByTestId("rail-door-review").click();
  await expect(page.getByTestId("review-pane")).toBeVisible();
  const review = page.getByTestId("review-pane");

  // Reeve's review lands under the line it is about; Fix this is the person's comment.
  await page.getByTestId("rvw-review-code").click();
  await expect(page.getByTestId("rvw-toast")).toContainText("Reeve raised 1 thing");
  await expect(page.getByTestId("rvw-notes-notes.md")).toHaveText("1 note");
  await openCard(page, "notes.md");
  const notes = page.getByTestId("rvw-card-notes.md");
  await expect(notes).toContainText("back off");
  await review.screenshot({ path: test.info().outputPath("review-findings.png") });
  await notes.getByRole("button", { name: "Fix this" }).click();
  await expect(notes.getByTestId("diff-comment")).toContainText("Fix this (Reeve's review)");
  // The composer carries it to the next message.
  await expect(page.getByText(/Fix this \(Reeve's review\)/).first()).toBeVisible();

  // Write it for me drafts into the box at the foot; the person commits.
  await page.getByTestId("rvw-commit-write").click();
  await expect(page.getByTestId("rvw-commit-message")).toHaveValue(/Retry uploads up to three times/);
  await review.screenshot({ path: test.info().outputPath("review-commit-drafted.png") });
  await page.getByTestId("rvw-commit-go").click();
  await expect(page.getByTestId("rvw-toast")).toContainText("Committed d41f7c2 to main");

  // Commits waiting: the line under the bar says how many and offers the push.
  await expect(page.getByTestId("rvw-sync")).toContainText("2 commits not pushed");
  await page.getByTestId("rvw-push").click();
  await expect(page.getByTestId("rvw-toast")).toContainText("Pushed main to origin");
  await expect(page.getByTestId("rvw-push")).toHaveCount(0);
});

test("Files links a file Reeve changed back to Review", async ({ page }) => {
  await page.goto("/");
  await page.getByPlaceholder(/Ask anything/).fill("edit the upload client");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByText("Added retry and a test.")).toBeVisible();

  await page.getByTestId("rail-door-files").click();
  await page.getByTestId("files-root-row").first().click();
  await page.locator(".tree-label", { hasText: "notes.md" }).click();
  await expect(page.getByTestId("fm-changed-banner")).toContainText("Changed in turn 1");
  await page.locator(".right-rail").screenshot({ path: test.info().outputPath("files-banner.png") });

  await page.getByTestId("fm-see-changes").click();
  await expect(page.getByTestId("review-pane")).toBeVisible();
  await expect(page.getByTestId("rvw-scope")).toContainText("This session");
  await expect(page.getByTestId("recorded-diff-/Users/test/Reeve/launch-note/notes.md")).toBeVisible();
});

// The turn's receipt is a line under the answer (owner, 2026-10-13): what changed, whether it
// was checked, and a row per file that opens its change in Review, the panel opening for it.
// Shown, the panel never writes the person's stored choice (rail-hidden stays "1").
test("the receipt says what changed; a file's row opens its change in Review", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("reeve:rail-hidden:v1", "1"));
  await page.goto("/");
  await page.getByPlaceholder(/Ask anything/).fill("edit the upload client");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByText("Added retry and a test.")).toBeVisible();

  const receipt = page.getByTestId("turn-changes");
  // Two of the person's files; the agent's own note is not counted.
  await expect(receipt.getByTestId("changes-count")).toHaveText("2 files changed");
  // Only the files and their churn: the check's last line is not on this line (owner, 2026-10-15).
  await expect(receipt).not.toContainText("Checked");
  await receipt.getByTestId("changes-toggle").click();
  await expect(receipt.getByTestId("receipt-notes")).toContainText("1 of its own notes");
  const created = receipt.getByTestId("receipt-file").filter({ hasText: "test_upload.py" });
  await expect(created).toContainText("new");

  // The panel stays put away; a file's row opens it on that file.
  await expect(page.getByTestId("review-pane")).toHaveCount(0);
  await receipt.getByTestId("receipt-file").filter({ hasText: "notes.md" }).locator(".rc-file-open").click();
  await expect(page.getByTestId("review-pane")).toBeVisible();
  await expect(page.getByTestId("rvw-scope")).toContainText("This session");
});

test("a file name in Review opens that file in Files", async ({ page }) => {
  await page.goto("/");
  await page.getByPlaceholder(/Ask anything/).fill("edit the upload client");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByText("Added retry and a test.")).toBeVisible();

  // The panel does not open Review by itself (owner, 2026-10-16): the person opens it.
  await page.getByTestId("rail-door-review").click();
  await expect(page.getByTestId("review-pane")).toBeVisible();
  await openCard(page, "notes.md");
  await page.getByTestId("rvw-open-notes.md").click();
  await expect(page.getByTestId("file-manager")).toBeVisible();
  await expect(page.getByTestId("rail-tab-files")).toBeVisible();
});
