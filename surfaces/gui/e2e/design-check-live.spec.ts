// A design write's check, live and reloaded. The check hands the model screenshots when
// it finds something to fix; those ride the event's `attachments`. A live row drew them as its body
// (a page-tall picture under "Edited", with no check line), and only a reload showed the row the way
// it is meant to be: the diff, and under it the check's line with its small picture and Open
// (owner report 2026-10-20). Invented page and findings.
import { expect } from "@playwright/test";
import { sendSessionEvent, test } from "./fixtures";

const SHOT =
  "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAgAAAAICAIAAABLbSncAAAAEklEQVR4nGNgGAWjYBSMAggAAAQQAAGFP6pyAAAAAElFTkSuQmCC";

test("a design write's row live is the one a reload draws: the check's line, not its screenshots", async ({ page }) => {
  await page.goto("/");
  await sendSessionEvent(page, "turn_start", {});
  const args = { path: "design/pricing.b.html", old_string: "One notebook", new_string: "One notebook, at any size" };
  await sendSessionEvent(page, "tool_proposed", { name: "edit_file", arguments: args, call_id: "d-1" });
  await sendSessionEvent(page, "tool_finished", {
    name: "edit_file",
    status: "ok",
    call_id: "d-1",
    result_preview: "Edited design/pricing.b.html",
    design_check: [
      {
        path: "design/pricing.b.html",
        summary: "Checked at 1440, 834 and 390 px: 1 to fix",
        issues: 1,
        lines: ["At 390 px, with longer text: text spills out of dt (past the edge: dt.label by 18 px)"],
        thumb: SHOT,
      },
    ],
    attachments: [{ type: "image_url", image_url: { url: SHOT } }],
  });

  const check = page.getByTestId("design-check").first();
  await expect(check).toBeVisible();
  await expect(check).toContainText("Checked at 1440, 834 and 390 px: 1 to fix");
  await expect(check.getByTestId("design-check-open")).toBeVisible();
  // The screenshots were the model's; the row's own body, opened, is not a picture.
  const line = page.locator(".tool-row-line").first();
  if ((await line.getAttribute("aria-expanded")) !== "true") await line.click();
  await expect(page.locator(".tool-row-body").first()).toBeVisible();
  await expect(page.getByTestId("tool-image")).toHaveCount(0);
  await page.screenshot({ path: test.info().outputPath("design-check-live.png") });
});

test("a page written then edited in one turn has one card, under the edit, and the write keeps its words", async ({ page }) => {
  // A card under the first write and chips under every edit showed the page twice, the newest
  // picture in the oldest place, and the chip's picture spread over the row above (owner, 2026-10-20).
  await page.goto("/");
  await sendSessionEvent(page, "turn_start", {});
  const check = (summary: string) => [{ path: "design/pricing.html", summary, issues: 1, lines: ["x"], thumb: SHOT }];
  await sendSessionEvent(page, "tool_proposed", { name: "write_file", arguments: { path: "design/pricing.html", content: "<h1>Plans</h1>" }, call_id: "w-1" });
  await sendSessionEvent(page, "tool_finished", { name: "write_file", status: "ok", call_id: "w-1", result_preview: "design/pricing.html", design_check: check("Checked at 1440, 834 and 390 px: 2 to fix") });
  await sendSessionEvent(page, "tool_proposed", { name: "replace_in_file", arguments: { path: "design/pricing.html", old: "Plans", new: "Pricing" }, call_id: "e-1" });
  await sendSessionEvent(page, "tool_finished", { name: "replace_in_file", status: "ok", call_id: "e-1", result_preview: "Updated design/pricing.html (1 replacement)", design_check: check("Checked at 1440, 834 and 390 px: 1 to fix") });
  const lines = page.getByTestId("design-check");
  await expect(lines).toHaveCount(2);
  await expect(lines.nth(0)).toContainText("2 to fix");
  await expect(lines.nth(0).locator("img")).toHaveCount(0);
  await expect(lines.nth(0).getByTestId("design-check-open")).toHaveCount(0);
  await expect(page.getByTestId("design-peek")).toHaveCount(1);
  await expect(lines.nth(1).getByTestId("design-peek")).toBeVisible();
  // The card sits under the edit's row, inside its own line.
  const card = await lines.nth(1).getByTestId("design-peek").boundingBox();
  const editRow = await page.locator(".tool-row-line").nth(1).boundingBox();
  expect(card && editRow).toBeTruthy();
  expect(card!.y).toBeGreaterThanOrEqual(editRow!.y + editRow!.height - 1);
  await page.screenshot({ path: test.info().outputPath("design-one-card.png") });
});
