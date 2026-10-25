// The start (the boot screen redesign, 2026-10-23): the app's own frame from the first frame, and
// in the conversation's column what Reeve is doing, in words. A message sent while it starts is
// held and sent once the conversation is open; a start that fails says why. Also the cold-boot fix
// of 2026-07-23: the model picker recovers when the mount-time settings fetch loses the race
// against the sidecar boot (previously "Loading models…" stuck until the user visited Settings).
import { expect } from "@playwright/test";
import { test } from "./fixtures";

/** A route held until the test lets it go. */
function held() {
  let release!: () => void;
  const gate = new Promise<void>((resolve) => (release = resolve));
  return { gate, release };
}

test("while Reeve starts, its own frame is there and the column says what it is doing", async ({ page }) => {
  const health = held();
  await page.route("**/v1/health", async (route) => {
    await health.gate;
    await route.fallback();
  });
  await page.goto("/");
  const pane = page.getByTestId("boot-pane");
  await expect(pane).toBeVisible();
  await expect(pane.getByText("Starting Reeve")).toBeVisible();
  await expect(page.getByTestId("boot-step-now")).toHaveText("Starting the engine");
  // The real mark (the logo's svg), not the ✦ glyph that read as another product's logo.
  await expect(pane.locator(".boot-mark svg")).toBeVisible();
  await expect(pane).not.toContainText("✦");
  // The frame the app will have: the rail (drawn, inert), the bar, the composer.
  await expect(page.getByTestId("sidebar-starting")).toBeVisible();
  await expect(page.getByTestId("topbar-title-pending")).toBeVisible();
  await expect(page.getByRole("textbox", { name: "Message" })).toBeVisible();

  health.release();
  await expect(page.getByTestId("boot-pane")).toHaveCount(0);
  await expect(page.getByTestId("sidebar-starting")).toHaveCount(0);
  await expect(page.getByTestId("topbar-title")).toHaveText("Draft the launch note");
});

test("once the engine answers, the bar names the conversation being opened", async ({ page }) => {
  const transcript = held();
  await page.route("**/v1/sessions/pinned-session-1/messages*", async (route) => {
    await transcript.gate;
    await route.fallback();
  });
  await page.goto("/");
  await expect(page.getByTestId("boot-step-now")).toHaveText("Opening “Draft the launch note”");
  await expect(page.getByTestId("boot-pane")).toContainText("Engine started");
  await expect(page.getByTestId("topbar-title")).toHaveText("Draft the launch note");
  transcript.release();
  await expect(page.getByTestId("boot-pane")).toHaveCount(0);
});

test("a message sent while Reeve starts is held, then sent in the conversation that opens", async ({ page }) => {
  const health = held();
  await page.route("**/v1/health", async (route) => {
    await health.gate;
    await route.fallback();
  });
  await page.goto("/");
  const box = page.getByRole("textbox", { name: "Message" });
  await expect(box).toHaveAttribute("placeholder", "Enter queues it until Reeve is ready");
  await box.fill("written before it was ready");
  await box.press("Enter");
  await expect(page.getByTestId("queued-prompt")).toContainText("written before it was ready");
  await expect(page.getByTestId("queued-prompts")).toContainText("when Reeve is ready");

  health.release();
  // Sent once the conversation is open and connected, in that conversation, and answered.
  await expect(page.getByTestId("topbar-title")).toHaveText("Draft the launch note");
  await expect(page.getByText(/Echo: written before it was ready/)).toBeVisible();
  await expect(page.getByTestId("queued-prompt")).toHaveCount(0);
});

test("a start whose engine never answers says so, and Try again starts over", async ({ page }) => {
  await page.clock.install();
  let up = false;
  await page.route("**/v1/health", async (route) => {
    if (!up) {
      await route.abort();
      return;
    }
    await route.fallback();
  });
  await page.goto("/");
  await expect(page.getByTestId("boot-pane")).toBeVisible();
  await page.clock.runFor(21_000);
  const card = page.getByTestId("boot-failure");
  await expect(card).toBeVisible();
  await expect(card).toContainText("Reeve’s engine didn’t start");
  await expect(card).toContainText("It didn’t answer within");
  await expect(page.getByTestId("boot-reason")).toHaveText("The engine did not answer.");
  // A browser has no log folder to open; the desktop app does.
  await expect(page.getByTestId("boot-logs")).toHaveCount(0);

  up = true;
  await page.getByTestId("boot-retry").click();
  await page.clock.runFor(2_000);
  await expect(page.getByTestId("boot-failure")).toHaveCount(0);
  await expect(page.getByTestId("topbar-title")).toHaveText("Draft the launch note");
});

// A cold start as it happens: nothing answers until the sidecar is up, health included, and then
// everything does. The settings read when the app mounts lose that race and fail quietly; the
// read after health has answered fills the picker, with no visit to Settings needed.
test("model picker recovers when settings fetches die during sidecar boot", async ({ page }) => {
  let sidecar: "starting" | "up" = "starting";
  await page.route("**/v1/health", async (route) => {
    await page.waitForTimeout(700);
    sidecar = "up";
    return route.fallback();
  });
  await page.route("**/v1/settings", (route) =>
    route.request().method() === "GET" && sidecar === "starting" ? route.abort() : route.fallback(),
  );
  await page.goto("/");
  await expect(page.getByTestId("model-picker-trigger")).toContainText("Claude Opus 4.8", { timeout: 10_000 });
  await expect(page.getByTestId("models-loading")).toHaveCount(0);
});
