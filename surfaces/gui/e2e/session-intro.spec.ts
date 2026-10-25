// The new-session page (2026-10-12): its heading, the composer right under it, and under
// that two short lists: the sessions to pick up and the things to start, as rows.
// Start-screen template tasks: concrete cards, no icon tiles, no "Set me up" list.
// Sub-lines are outcome-voiced; connection state lives in the dots + the trailing action.
// A task that needs apps is offered only when they are connected (it used to pitch
// GitHub and Slack to everyone with "Configure ›"); a click prefills the composer with the stem.
// The HubSpot row left when the catalog was narrowed.
import { expect } from "@playwright/test";
import { test } from "./fixtures";

const liveSources = (page, names: string[]) =>
  page.route("**/v1/sessions/*/connections*", (route) =>
    route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        connected: names.map((connector) => ({ connector, enabled: true, detail: "" })),
        recommended: [],
        attention: 0,
      }),
    }),
  );

test("a task that needs apps the person has not connected is not offered", async ({ page }) => {
  await liveSources(page, ["slack"]); // github missing → the report row is not offered
  await page.goto("/");
  await expect(page.getByTestId("start-title")).toBeVisible();

  await expect(page.getByTestId("intro-tasks").locator(".task-row")).toHaveCount(1);
  await expect(page.getByTestId("intro-task-folder")).toBeVisible();
  await expect(page.getByTestId("intro-task-github-slack")).toHaveCount(0);
  await expect(page.getByText("Configure ›")).toHaveCount(0);
  await expect(page.getByText("Set me up (optional)")).toHaveCount(0);
});

test("ready rows reveal → on hover and prefill the composer", async ({ page }) => {
  await liveSources(page, ["github", "slack"]);
  await page.goto("/");

  const gh = page.getByTestId("intro-task-github-slack");
  await expect(gh).toHaveAttribute("title", "Repo activity, summarized and posted every Friday");
  await expect(gh.locator(".task-row-act")).toHaveText("→");
  // The action is hover-revealed on ready rows (hidden at rest).
  await expect(gh.locator(".task-row-act")).toHaveCSS("opacity", "0");
  await gh.hover();
  await expect(gh.locator(".task-row-act")).toHaveCSS("opacity", "1");
  await gh.click();
  await expect(page.getByPlaceholder(/Ask anything/)).toHaveValue(/weekly progress report/);
});

// The session has only its own folder so far, so the folder task opens a form to add one, and
// once a folder is added it puts its stem in the composer.
test("folder task opens the inline add-folder form; adding a folder prefills the composer", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("intro-task-folder").click();
  const folder = page.getByPlaceholder("Choose or paste a folder path…");
  await expect(folder).toBeVisible();
  await folder.fill("/Users/me/Reports");
  await page.getByRole("button", { name: "Add", exact: true }).click();
  await expect(page.getByPlaceholder(/Ask anything/)).toHaveValue(/Analyze the files in this folder/);
});

test("a new session is a start page: the heading, the composer under it, the lists under that", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/");
  const heading = page.getByRole("heading", { name: "What can I take off your plate?" });
  await expect(heading).toBeVisible();
  // The lines of help that sat under the old heading are gone: the placeholder says them.
  await expect(page.getByTestId("home-agent-tip")).toHaveCount(0);
  await expect(page.getByPlaceholder("Ask anything, / for commands, @ for files")).toBeVisible();
  // The fixtures open the side panel, which folds the queue at this width: measure once the
  // column has finished sliding.
  await expect.poll(async () => Math.round((await page.locator(".sidebar").boundingBox())!.width)).toBe(56);
  await page.waitForTimeout(450);

  const boxes = async () => {
    const h = (await heading.boundingBox())!;
    const c = (await page.locator(".composer").boundingBox())!;
    const t = (await page.getByTestId("intro-tasks").boundingBox())!;
    return { h, c, t };
  };
  const { h, c, t } = await boxes();
  // In reading order, close together, and nowhere near the foot of the window.
  expect(c.y).toBeGreaterThan(h.y + h.height);
  expect(c.y - (h.y + h.height)).toBeLessThan(60);
  expect(t.y).toBeGreaterThan(c.y + c.height);
  expect(c.y + c.height).toBeLessThan(900 * 0.6);
  // The heading is centred over the composer.
  expect(Math.abs(h.x + h.width / 2 - (c.x + c.width / 2))).toBeLessThan(2);
  // The card's corners are the large ones.
  await expect(page.locator(".composer")).toHaveCSS("border-top-left-radius", "24px");

  // The first message turns the page into a conversation: the composer goes to the foot,
  // and the heading and the tasks are gone.
  await page.getByPlaceholder(/Ask anything/).fill("hello");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByText("Echo: hello", { exact: false }).first()).toBeVisible();
  await expect(heading).toHaveCount(0);
  await expect(page.getByTestId("intro-tasks")).toHaveCount(0);
  const after = (await page.locator(".composer").boundingBox())!;
  expect(after.y + after.height).toBeGreaterThan(900 * 0.85);
});

test("the start page offers what to pick up, waiting first, and a row opens it", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "New session" }).first().click();
  const pick = page.getByTestId("pick-up");
  await expect(pick).toBeVisible();
  const rows = pick.getByTestId("pick-up-row");
  await expect(rows).toHaveCount(3);
  // "Weekly plan 3" waits on an approval: it comes first, and says so.
  await expect(rows.first()).toContainText("Weekly plan 3");
  await expect(rows.first()).toContainText("Approval");
  const name = (await rows.nth(1).locator(".start-row-text").textContent()) || "";
  await rows.nth(1).click();
  await expect(page.getByTestId("topbar-title")).toHaveText(name);
});
