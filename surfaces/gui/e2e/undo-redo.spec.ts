// Message-level undo/redo: `/undo` stages a revert — the transcript shows a
// boundary where the model's view stops, the undone message comes back into the draft,
// and nothing is deleted; `/redo` clears the marker.
import { expect } from "@playwright/test";
import { seedSessionMessages, test } from "./fixtures";

const MESSAGES = [
  { role: "user", content: "first question", ts: 1 },
  { role: "assistant", content: "first answer", ts: 2 },
  { role: "user", content: "second question", ts: 3 },
  { role: "assistant", content: "second answer", ts: 4 },
];

test("undo stages a boundary and pulls the message back; redo clears it", async ({ page }) => {
  // The seeded transcript's marker, mutable so the spec's own routes can stage/clear it
  // and the app's reload reads the current value.
  const state: { revert: unknown } = { revert: null };
  await seedSessionMessages(page, "pinned-session-1", MESSAGES, state);
  await page.route(/\/v1\/sessions\/pinned-session-1\/revert$/, async (route) => {
    state.revert = { boundary: 2, staged_at: 4, ts: 1 };
    await route.fulfill({
      json: {
        ok: true,
        revert: state.revert,
        message: { index: 2, text: "second question", attachments: [] },
      },
    });
  });
  await page.route(/\/v1\/sessions\/pinned-session-1\/unrevert$/, async (route) => {
    state.revert = null;
    await route.fulfill({ json: { ok: true, revert: null, cleared: true } });
  });

  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();
  await expect(page.getByText("second question").first()).toBeVisible({ timeout: 10_000 });

  const box = page.getByPlaceholder(/Ask anything/);
  await box.fill("/undo");
  await page.getByTestId("slash-command-undo").click();

  // The boundary line lands where the model's view now stops, and the undone message is
  // back in the composer as the draft.
  const divider = page.getByTestId("revert-divider");
  await expect(divider).toBeVisible({ timeout: 10_000 });
  await expect(divider).toContainText("Reverted — the next 2 messages are hidden");
  await expect(box).toHaveValue("second question");
  // Additive: the hidden span is still on screen in the transcript.
  await expect(page.getByText("second question").first()).toBeVisible();

  // Redo clears the marker and the boundary disappears.
  await box.fill("/redo");
  await page.getByTestId("slash-command-redo").click();
  await expect(page.getByTestId("revert-divider")).toHaveCount(0, { timeout: 10_000 });
  await expect(box).toHaveValue("");
});
