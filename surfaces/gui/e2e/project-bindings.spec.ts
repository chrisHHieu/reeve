import type { Page } from "@playwright/test";
import { test, expect } from "./fixtures";
import { openPinned } from "./steps";

// The session's part of the composer's attach menu: which project's memory the session
// is bound to. Two labelled sections; the memory's menu, its rows named by the rules (the folder
// it comes from, the most recent named ones, which one is bound); swapping the binding; naming
// the current one. Only project memory is bound here; the global memory never shows.

/** The pinned session's attach menu, at its project memory. */
async function projectMemory(page: Page, opened = false) {
  if (!opened) await openPinned(page);
  await page.getByRole("button", { name: "Attach" }).click();
  await page.getByRole("button", { name: "Project memory" }).click();
  return page.getByTestId("project-menu-memory");
}

test("attach menu: two sections, memory submenu with derived + named rows", async ({ page }) => {
  await openPinned(page);
  await page.getByRole("button", { name: "Attach" }).click();
  // The headings matched whole: a part of them is also found in other text on the page (the
  // Review panel's section titles).
  await expect(page.getByText("Add to this message", { exact: true })).toBeVisible();
  await expect(page.getByText("For this whole session", { exact: true })).toBeVisible();
  await expect(page.getByTestId("plus-files")).toBeVisible();

  await page.getByRole("button", { name: "Project memory" }).click();
  const menu = page.getByTestId("project-menu-memory");
  // The folder's own memory, its path cut to the last three parts and marked.
  await expect(menu.getByText("…/ro4d/demo-universe/notes")).toBeVisible();
  await expect(menu.getByText("this folder")).toBeVisible();
  // The named ones, the newest first, with no filter under six, and the two actions.
  for (const shown of ["reeve", "personal-ops", "Name current memory…", "View & edit…"]) await expect(menu.getByText(shown)).toBeVisible();
  await expect(menu.getByPlaceholder("Filter…")).toHaveCount(0);
});

test("binding swap round-trips and closes the menu", async ({ page }) => {
  await (await projectMemory(page)).getByText("reeve").click();
  await expect(page.getByTestId("project-menu-memory")).toHaveCount(0);
  // Opened again, it says which is bound.
  await expect((await projectMemory(page, true)).getByText("bound")).toBeVisible();
});

test("naming the current project adds it to the named list", async ({ page }) => {
  await (await projectMemory(page)).getByText("Name current memory…").click();
  const name = page.getByPlaceholder("Name this memory…");
  await name.fill("my-notes");
  await name.press("Enter");
  await expect(page.getByTestId("project-menu-memory").getByText("my-notes")).toBeVisible();
});
