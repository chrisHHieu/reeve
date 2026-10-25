// The new-session setup row (the welcome controls): the folder and machine
// chips under the composer, the folder chip's panel (search over recents, the current
// folder checked, "Open folder…", a typed path, and our "Just chat" — a temporary folder),
// and the composer state after each pick.
import { expect, type Page } from "@playwright/test";
import { test } from "./fixtures";

const RECENTS = {
  workspaces: [
    { path: "/Users/test/projects/exports", name: "exports", exists: true },
    { path: "/Users/test/projects/recipes", name: "recipes", exists: true },
  ],
};

async function newSession(page: Page) {
  await page.route("**/v1/workspaces/recent", (route) => route.fulfill({ json: RECENTS }));
  await page.goto("/");
  await page.getByTestId("rail-new-session").click();
  await expect(page.getByTestId("setup-row")).toBeVisible();
}

test("the folder panel searches recents, checks the current folder, and binds a pick", async ({
  page,
}) => {
  await newSession(page);

  // The panel is the dialog the chip advertises; the search row filters the catalog.
  await page.getByTestId("folder-chip").click();
  await expect(page.getByRole("dialog", { name: "Choose a folder" })).toBeVisible();
  await expect(page.getByTestId("folder-option")).toHaveCount(2);

  await page.getByTestId("folder-search").fill("rec");
  await expect(page.getByTestId("folder-option")).toHaveCount(1);
  await expect(page.getByTestId("folder-option")).toContainText("recipes");

  await page.getByTestId("folder-option").click();
  await expect(page.getByTestId("folder-chip")).toContainText("recipes");

  // Reopening checks the folder the draft is bound to.
  await page.getByTestId("folder-chip").click();
  await expect(page.getByRole("radio", { name: /recipes/ })).toHaveAttribute(
    "aria-checked",
    "true",
  );
  await expect(page.getByRole("radio", { name: /exports/ })).toHaveAttribute(
    "aria-checked",
    "false",
  );
});

test("a typed path opens from the search row", async ({ page }) => {
  await newSession(page);

  await page.getByTestId("folder-chip").click();
  await page.getByTestId("folder-search").fill("/tmp/typed-project");
  await page.getByTestId("folder-typed").click();

  await expect(page.getByTestId("folder-chip")).toContainText("typed-project");
  await expect(page.getByTestId("folder-panel")).toHaveCount(0);
});

test("Just chat picks a temporary folder and the send goes out with no folder dialog", async ({
  page,
}) => {
  await newSession(page);

  await page.getByTestId("folder-chip").click();
  await page.getByTestId("folder-temp").click();

  await expect(page.getByTestId("folder-chip")).toContainText("Temporary folder");
  await expect(page.getByTestId("folder-panel")).toHaveCount(0);

  // No dead end: the draft can send immediately, and no send-time dialog appears.
  await page.getByPlaceholder(/Ask anything/).fill("work somewhere temporary");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByText(/Echo: work somewhere temporary/)).toBeVisible();
  await expect(page.getByTestId("setup-row")).toHaveCount(0);
});

// The environment pick is live. A saved host is selectable, the
// workspace path on it is asked for next, and the bind is the server PATCH that the app
// then reflects as `user@host:path` — no fake local folder, no disabled row.
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

test("picking a saved SSH host binds the draft and the readout says user@host:path", async ({
  page,
}) => {
  await page.route("**/v1/remote-environments", (route) =>
    route.fulfill({ json: { environments: [REMOTE_HOST] } }),
  );
  // The workspace step's browser lists the host's folders; $HOME is the default.
  await page.route("**/v1/remote-environments/*/directories*", (route) =>
    route.fulfill({
      json: {
        ok: true,
        path: "/home/ubuntu",
        entries: [{ name: "project", path: "/home/ubuntu/project" }],
      },
    }),
  );
  const patches: any[] = [];
  await page.route(/\/v1\/sessions\/[^/?]+$/, (route) => {
    if (route.request().method() !== "PATCH") return route.fallback();
    const body = route.request().postDataJSON();
    patches.push(body);
    return route.fulfill({
      json: {
        ok: true,
        session_id: route.request().url().split("/").pop(),
        environment_id: body.environment_id,
        workspace: body.workspace ?? "/Users/test/Reeve",
        environment: body.environment_id
          ? {
              id: REMOTE_HOST.id,
              name: REMOTE_HOST.name,
              user: REMOTE_HOST.user,
              host: REMOTE_HOST.host,
              port: REMOTE_HOST.port,
            }
          : null,
      },
    });
  });
  await newSession(page);

  // The host list is LIVE now (phase 1 listed these rows disabled).
  await page.getByTestId("environment-chip").click();
  await expect(page.getByTestId("environment-remote")).toHaveCount(1);
  await expect(page.getByTestId("environment-remote")).toContainText(
    "ubuntu@dev.example.com:2222",
  );

  // Picking a host asks for the workspace on it; a relative path is refused inline.
  await page.getByTestId("environment-remote").click();
  await expect(page.getByTestId("environment-path-step")).toBeVisible();
  // The host's $HOME arrives first. Typed before it lands, the fill could be cleared and then
  // the answer written in under it, binding "/home/ubuntusrv/app" (5 runs in 10).
  await expect(page.getByTestId("environment-path")).toHaveValue("/home/ubuntu");
  await page.getByTestId("environment-path").fill("srv/app");
  await page.getByTestId("environment-path-bind").click();
  await expect(page.getByTestId("environment-error")).toContainText("absolute");

  await page.getByTestId("environment-path").fill("/srv/app");
  await page.getByTestId("environment-path-bind").click();

  // Bound: the chip reads the destination, the topbar carries the full readout, and the
  // server got the PATCH the manager's binding route expects.
  await expect(page.getByTestId("environment-chip")).toContainText("ubuntu@dev.example.com");
  // Before the first message the strip's pickers say it; the top bar does not say it twice.
  await expect(page.getByTestId("where-crumb")).toHaveCount(0);
  expect(patches[0]).toMatchObject({ environment_id: "h1", workspace: "/srv/app" });

  // LOCAL clears the binding through the same route.
  await page.getByTestId("environment-chip").click();
  await page.getByTestId("environment-local").click();
  await expect(page.getByTestId("environment-chip")).toContainText("Local");
  expect(patches[1]).toMatchObject({ environment_id: null });
});

// Owner-hit 2026-10-14: picking a permission on the start page turned it into an empty
// conversation ("Read only is on."), since the mode marker made the transcript non-empty.
test("a permission picked on the start page leaves the start page in place", async ({ page }) => {
  await newSession(page);
  const heading = page.getByRole("heading", { name: "What can I take off your plate?" });
  await expect(heading).toBeVisible();

  await page.getByRole("button", { name: "Mode", exact: true }).click();
  await page.getByTestId("mode-menu").getByText("Read only").click();

  await expect(page.getByText("Read only is on.")).toHaveCount(0);
  await expect(heading).toBeVisible();
  await expect(page.getByTestId("setup-row")).toBeVisible();
});

// Owner-hit 2026-10-14: "Just chat", then a model picked, and the chip went from "Temporary
// folder" to the scratch folder's id with a HEAD branch: the model move re-applied the facts
// recorded at connect, when the draft had no temporary folder yet.
test("a model picked after Just chat keeps the temporary folder", async ({ page }) => {
  await newSession(page);
  await page.getByTestId("folder-chip").click();
  await page.getByTestId("folder-temp").click();
  await expect(page.getByTestId("folder-chip")).toContainText("Temporary folder");

  await page.getByTestId("model-picker-trigger").click();
  await page.getByTestId("provider-row-all").click();
  await page.getByTestId("model-row-gpt-5.5").click();
  await expect(page.getByTestId("model-picker-trigger")).toContainText("GPT 5.5");
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("folder-chip")).toContainText("Temporary folder");

  await page.getByRole("button", { name: "Mode", exact: true }).click();
  await page.getByTestId("mode-menu").getByText("Full access").click();

  await expect(page.getByTestId("folder-chip")).toContainText("Temporary folder");
});
