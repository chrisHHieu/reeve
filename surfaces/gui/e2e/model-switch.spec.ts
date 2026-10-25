// The model can be changed at any point of a session (2026-07-22; the lock that hid the picker
// after the first turn is gone), and the messages after it go to the new model.
import { expect } from "@playwright/test";
import { test } from "./fixtures";
import { openPinned, say } from "./steps";

// The model panel is 560px wide from the right; at the default 1280×720 the sidebar and panel
// leave it too little room and its list is cut off, so this runs in a desktop-sized window.
test.use({ viewport: { width: 1680, height: 900 } });

test("mid-session model switch shows the marker and later turns use the new model", async ({ page }) => {
  await openPinned(page);
  await say(page, "hello there", "enter");
  await expect(page.getByText("Echo: hello there", { exact: false }).first()).toBeVisible();

  const picker = page.getByTestId("model-picker-trigger");
  await expect(picker).toContainText("Claude Opus 4.8");
  await picker.click();
  // It opens on the session model's provider; "All" is the way to every provider's models.
  await page.getByTestId("provider-row-all").click();
  await page.getByTestId("model-row-gpt-5.5").click();
  // It stays open for the thinking level, and Escape closes it.
  await expect(page.getByTestId("model-picker-panel")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("model-picker-panel")).toHaveCount(0);

  // The picker names the new model, and the conversation gets no line about it (owner, 2026-10-13).
  await expect(picker).toContainText("GPT 5.5");
  await expect(page.getByText(/Model switched/)).toHaveCount(0);
  // The next message goes with it, as the echo says.
  await say(page, "after the switch", "enter");
  await expect(page.getByText("Echo: after the switch [model=gpt-5.5]", { exact: false }).first()).toBeVisible();
});
