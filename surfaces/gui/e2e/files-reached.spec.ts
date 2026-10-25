// The projects a session opened on its own are a permission it holds, listed in the
// composer's mode menu, not in Files: in the tree they piled up, one per project the agent looked
// at, each "Read only" (owner, 2026-10-18). Files is the folders the person chose; a folder they
// marked read-only still says so. From the menu a project is taken back, or added to Files, which
// makes it the person's folder.
import { expect, type Page } from "@playwright/test";
import { test } from "./fixtures";

const SCRATCH = { path: "/Users/test/Reeve/launch-note", writable: true, label: "scratch", primary: true, exists: true };
const BRAND = { path: "/Users/test/Shared/brand", writable: false, label: "brand", primary: false, exists: true };
const WEALTHLOG = { path: "/Users/test/Project/wealthlog", writable: false, label: "wealthlog", primary: false, exists: true, reached: true };
const NATIVELY = { path: "/Users/test/Project/natively", writable: true, label: "natively", primary: false, exists: true, reached: true };

/** A roots endpoint with the server's rules: a person's add owns the folder, a remove drops it. */
async function serveRoots(page: Page) {
  let roots: any[] = [SCRATCH, BRAND, WEALTHLOG, NATIVELY].map((r) => ({ ...r }));
  await page.route("**/v1/sessions/*/roots**", async (route) => {
    const request = route.request();
    if (request.method() === "POST") {
      const body = request.postDataJSON();
      roots = roots.map((r) => (r.path === body.path ? { ...r, writable: !!body.writable, reached: undefined } : r));
    } else if (request.method() === "DELETE") {
      const path = new URL(request.url()).searchParams.get("path");
      roots = roots.filter((r) => r.path !== path);
    }
    await route.fulfill({ json: request.method() === "GET" ? { roots } : { ok: true, roots } });
  });
}

async function start(page: Page) {
  await serveRoots(page);
  await page.goto("/");
  await page.getByPlaceholder(/Ask anything/).fill("hello");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByText(/Echo: hello/)).toBeVisible();
}

test("Files lists the folders the person chose; the projects the agent opened are in the mode menu", async ({ page }) => {
  await start(page);
  await page.getByTestId("rail-door-files").click();
  const rows = page.getByTestId("files-root-row");
  await expect(rows).toHaveText([/^scratch/, /^brand/]);
  await expect(rows.nth(1).getByTestId("files-root-access")).toHaveText("Read only");

  await page.getByRole("button", { name: "Mode" }).click();
  const menu = page.getByTestId("mode-menu");
  await expect(menu.getByTestId("reached-projects")).toHaveText("Opened by the agent");
  await expect(menu.getByTestId("reached-project")).toHaveText([/wealthlog\s*read$/, /natively\s*read & write$/]);
});

test("a project is taken back from the menu, or added to Files", async ({ page }) => {
  await start(page);
  await page.getByTestId("rail-door-files").click();
  await page.getByRole("button", { name: "Mode" }).click();

  await page.getByRole("button", { name: "Take back natively" }).click();
  await expect(page.getByTestId("reached-project")).toHaveText([/wealthlog/]);

  await page.getByRole("button", { name: "Add wealthlog to Files" }).click();
  await expect(page.getByTestId("reached-project")).toHaveCount(0);
  await expect(page.getByTestId("reached-projects")).toHaveCount(0);
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("files-root-row")).toHaveText([/^scratch/, /^brand/, /^wealthlog/]);
});
