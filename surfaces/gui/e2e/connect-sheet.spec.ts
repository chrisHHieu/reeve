// The connect sheet (2026-10-10): it covers the whole window wherever it is opened from, keeps the
// keyboard inside, and shows each moment of a connect: waiting on the browser (with a way back to
// the sign-in page and a way out), a failure next to the button, and a success that says so
// before it closes.
import { expect } from "@playwright/test";
import { test, openConnectors as goToConnectors } from "./fixtures";

async function openMondaySheet(page) {
  await page.goto("/");
  await goToConnectors(page);
  const connect = page.getByTestId("connector-monday").getByRole("button", { name: "Connect" });
  await connect.click();
  const sheet = page.getByTestId("add-connection-modal");
  await expect(sheet).toBeVisible();
  return { sheet, connect };
}

test("the sheet covers the window, not the Settings column it was opened from", async ({ page }) => {
  // Settings' content column animates in with a transform; a fixed overlay drawn inside it was
  // clipped to the column (owner screenshot). The sheet is drawn on body now.
  const { sheet } = await openMondaySheet(page);
  const viewport = page.viewportSize()!;
  const scrim = await sheet.locator(".ui-dialog-scrim").boundingBox();
  expect(scrim).toEqual({ x: 0, y: 0, width: viewport.width, height: viewport.height });
  await expect(page.getByRole("dialog", { name: "Connect monday.com" })).toBeVisible();
});

test("keyboard: focus starts on Connect, stays inside, and Escape returns it to the trigger", async ({ page }) => {
  const { sheet, connect } = await openMondaySheet(page);
  await expect(sheet.getByTestId("modal-mcp-one-click")).toBeFocused();
  for (let i = 0; i < 6; i++) {
    await page.keyboard.press("Tab");
    expect(await page.evaluate(() => !!document.activeElement?.closest("[role=dialog]"))).toBe(true);
  }
  await page.keyboard.press("Escape");
  await expect(sheet).toHaveCount(0);
  // The sheet closed, Settings did not.
  await expect(page.getByTestId("settings-shell")).toBeVisible();
  await expect(connect).toBeFocused();
});

test("waiting offers the sign-in page again and a way out; success says so, then closes", async ({ page }) => {
  const { sheet } = await openMondaySheet(page);
  await sheet.getByTestId("modal-mcp-one-click").click();
  const waiting = sheet.getByTestId("modal-connect-waiting");
  await expect(waiting).toContainText("Check your browser…");
  await expect(sheet.getByTestId("modal-connect-reopen")).toBeFocused();
  await expect(sheet.getByTestId("modal-connect-cancel")).toBeVisible();

  // The mock sign-in completes at once: the poll sees it and the sheet shows the done moment.
  await expect(sheet.getByTestId("modal-connected")).toBeVisible({ timeout: 10_000 });
  await expect(page.getByRole("dialog", { name: "Connected to monday.com" })).toBeVisible();
  await expect(sheet).toHaveCount(0, { timeout: 5_000 });
  await expect(page.getByTestId("connector-monday")).toContainText("Connected");
});

test("Cancel stops waiting and gives the button back", async ({ page }) => {
  // A sign-in that never finishes: the connect starts, the poll never sees a connection.
  await page.route("**/v1/connectors/monday/mcp-connect", (route) =>
    route.fulfill({ json: { ok: true, started: true } }),
  );
  const { sheet } = await openMondaySheet(page);
  await sheet.getByTestId("modal-mcp-one-click").click();
  await expect(sheet.getByTestId("modal-connect-waiting")).toBeVisible();
  await sheet.getByTestId("modal-connect-cancel").click();
  await expect(sheet.getByTestId("modal-mcp-one-click")).toBeFocused();
  await expect(sheet.getByTestId("modal-mcp-one-click")).toHaveText("Connect monday.com");
});

test("a failed start shows the reason where the button is, and Try again", async ({ page }) => {
  await page.route("**/v1/connectors/monday/mcp-connect", (route) =>
    route.fulfill({ json: { ok: false, error: "monday.com refused the sign-in request." } }),
  );
  const { sheet } = await openMondaySheet(page);
  await sheet.getByTestId("modal-mcp-one-click").click();
  const error = sheet.getByTestId("modal-connect-error");
  await expect(error).toBeVisible();
  await expect(error).toContainText("Couldn’t connect monday.com");
  await expect(error).toContainText("monday.com refused the sign-in request.");
  await expect(sheet.getByTestId("modal-mcp-one-click")).toHaveText("Try again");
  await expect(sheet.getByTestId("modal-mcp-one-click")).toBeFocused();
});
