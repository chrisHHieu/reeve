// Where a conversation works (owner, 2026-10-13). Before the first message the strip under the
// composer holds the pickers; with the first message the strip goes and a crumb before the name
// in the top bar says it: the folder and its branch, or the remote machine by its name. A
// remote machine was said twice (a chip beside the name, and the strip), and the strip stayed
// at the foot of every conversation for something nobody chooses any more.
import { expect, type Page } from "@playwright/test";
import { dropSessionSocket, test } from "./fixtures";

const box = (page: Page) => page.getByPlaceholder(/Ask anything/);

test("the strip goes with the first message and the crumb says where, with the branch to switch", async ({ page }) => {
  await page.goto("/");
  await box(page).waitFor();
  await expect(page.getByTestId("composer-session")).toBeVisible();
  await expect(page.getByTestId("where-crumb")).toHaveCount(0);

  await box(page).fill("edit the upload client");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(page.getByText("Added retry and a test.")).toBeVisible();

  await expect(page.getByTestId("composer-session")).toHaveCount(0);
  const crumb = page.getByTestId("where-crumb");
  // It arrived with this conversation's first message: with a motion.
  await expect(crumb).toHaveAttribute("data-arrive", "true");
  await expect(page.getByTestId("where-crumb-branch")).toHaveText("main");
  // Before the name.
  const order = await page.evaluate(() => {
    const c = document.querySelector('[data-testid="where-crumb"]')!.getBoundingClientRect();
    const t = document.querySelector('[data-testid="topbar-title"]')!.getBoundingClientRect();
    return c.right <= t.left;
  });
  expect(order).toBe(true);

  await crumb.click();
  const pop = page.getByTestId("where-pop");
  await expect(pop).toBeVisible();
  // It hangs under the bar, as the bar's other menus do (read once it has come in).
  const under = () =>
    page.evaluate(() => {
      const p = document.querySelector('[data-testid="where-pop"]')!.getBoundingClientRect();
      const bar = document.querySelector(".main-topbar")!.getBoundingClientRect();
      return p.top >= bar.bottom - 0.5;
    });
  await expect.poll(under).toBe(true);
  await expect(page.getByTestId("where-branch")).toContainText("main");
  await expect(page.getByTestId("where-machine")).toHaveText("This computer");

  // The folder in the panel's Files tab.
  await page.getByTestId("where-show-files").click();
  await expect(pop).toHaveCount(0);
  await expect(page.locator('.rail-chip[data-kind="files"]')).toHaveAttribute("aria-selected", "true");

  // The branch, switched from the crumb's panel.
  await crumb.click();
  await page.getByTestId("where-switch-branch").click();
  await page.getByTestId("branch-feature/statements").click();
  await expect(page.getByTestId("where-crumb-branch")).toHaveText("feature/statements");
});

test("a conversation opened with its history shows the crumb at once, and no strip", async ({ page }) => {
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();
  await box(page).fill("hello");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(page.getByText(/Echo: hello/).first()).toBeVisible();
  await page.getByText("Weekly plan 1").first().click();
  await page.getByText("Draft the launch note").first().click();
  const crumb = page.getByTestId("where-crumb");
  await expect(crumb).toBeVisible();
  await expect(crumb).not.toHaveAttribute("data-arrive", /.*/);
  await expect(page.getByTestId("composer-session")).toHaveCount(0);
});

const REMOTE_HOST = {
  id: "h1",
  name: "Build server",
  host: "dev.example.com",
  user: "ubuntu",
  port: 2222,
  identity_file: null,
  created_at: "2026-09-18T00:00:00Z",
  updated_at: "2026-09-18T00:00:00Z",
};

async function bindRemote(page: Page) {
  await page.route("**/v1/remote-environments", (route) => route.fulfill({ json: { environments: [REMOTE_HOST] } }));
  await page.route(/\/v1\/sessions\/[^/?]+$/, (route) => {
    if (route.request().method() !== "PATCH") return route.fallback();
    const body = route.request().postDataJSON();
    return route.fulfill({
      json: {
        ok: true,
        session_id: route.request().url().split("/").pop(),
        environment_id: body.environment_id,
        workspace: body.workspace,
        environment: { id: "h1", name: "Build server", user: "ubuntu", host: "dev.example.com", port: 2222 },
      },
    });
  });
  await page.goto("/");
  await box(page).waitFor();
  await page.getByTestId("environment-chip").click();
  await page.getByTestId("environment-remote").click();
  await page.getByTestId("environment-path").fill("/srv/app");
  await page.getByTestId("environment-path-bind").click();
  await expect(page.getByTestId("environment-chip")).toContainText("ubuntu@dev.example.com");
  await box(page).fill("what runs on this host");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(page.getByText(/Echo: what runs on this host/).first()).toBeVisible();
}

test("a remote machine is named once, in the crumb; its address and folder are one press away", async ({ page }) => {
  await bindRemote(page);
  await expect(page.getByTestId("topbar-environment")).toHaveCount(0);
  await expect(page.getByTestId("composer-session")).toHaveCount(0);
  const crumb = page.getByTestId("where-crumb");
  await expect(crumb).toHaveText("Build server");
  await crumb.click();
  await expect(page.getByTestId("where-machine")).toContainText("ubuntu@dev.example.com:2222");
  await expect(page.getByTestId("where-machine")).toContainText("Connected");
  await expect(page.getByTestId("where-folder")).toContainText("/srv/app");
  await expect(page.getByTestId("where-branch")).toHaveCount(0);
});

test("a remote machine that refuses to start is the named cause, and can be asked again", async ({ page }) => {
  await bindRemote(page);
  // The conversation the server keeps: a reconnect reads it again.
  await page.route(/\/v1\/sessions\/[^/]+\/messages$/, (route) =>
    route.fulfill({
      json: {
        messages: [
          { role: "user", content: "what runs on this host", ts: 1755600000 },
          { role: "assistant", content: "Echo: what runs on this host", ts: 1755600010 },
        ],
        revert: null,
      },
    }),
  );
  // From now on the server builds no engine for it: accept, say why, close (`_refuse`).
  let attempts = 0;
  await page.routeWebSocket(/\/ws\/session\//, (ws) => {
    attempts += 1;
    ws.send(
      JSON.stringify({
        type: "error",
        data: { error: "ssh: connect to host dev.example.com port 2222: Connection timed out", kind: "timeout" },
      }),
    );
    setTimeout(() => ws.close(), 50);
  });
  await dropSessionSocket(page);

  const note = page.getByTestId("connection-note");
  await expect(note).toContainText("Reeve could not start on Build server.", { timeout: 15_000 });
  const crumb = page.getByTestId("where-crumb");
  await expect(crumb.locator(".where-crumb-dot")).toHaveCount(1);
  await crumb.click();
  await expect(page.getByTestId("where-machine")).toContainText("Not started");
  await expect(page.getByTestId("where-problem")).toContainText("Connection timed out");
  const before = attempts;
  await page.getByTestId("where-retry").click();
  await expect.poll(() => attempts).toBe(before + 1);
});
