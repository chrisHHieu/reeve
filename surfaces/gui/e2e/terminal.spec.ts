// `/terminal`: the composer's slash row opens the
// session's user-shell panel, typed input streams back, and close hides it. The panel
// attaches to the user's own shell — never the agent's terminal tool one.
import { expect } from "@playwright/test";
import { test } from "./fixtures";

test("/terminal opens the shell tab and streams typed input", async ({ page }) => {
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();
  const box = page.getByPlaceholder(/Ask anything/);

  await box.fill("/terminal");
  await page.getByTestId("slash-command-terminal").click();

  // The tab hosts a real emulator (xterm.js) over the pty channel. The shell marks its
  // prompt, so an idle shell reads Ready, and the tab is named after its folder.
  const host = page.getByTestId("terminal-host");
  await expect(page.getByTestId("terminal-panel")).toBeVisible();
  await expect(page.getByTestId("terminal-state")).toHaveText("Ready");
  await expect(page.getByTestId("rail-tab-terminal-1")).toContainText("launch-note");
  await expect(page.getByTestId("terminal-interrupt")).toHaveCount(0);

  await host.click();
  await page.keyboard.type("echo hi");
  await page.keyboard.press("Enter");
  await expect(host).toContainText("echo: echo hi");
  await expect(page.getByTestId("terminal-state")).toHaveText("Ready");

  // Clear drops the view.
  await page.getByTestId("terminal-clear").click();
  await expect(host).not.toContainText("echo: echo hi");
});

test("a running command names the tab, offers Stop; closing asks first", async ({ page }) => {
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();
  await page.getByPlaceholder(/Ask anything/).fill("/terminal");
  await page.getByTestId("slash-command-terminal").click();
  const host = page.getByTestId("terminal-host");
  await expect(page.getByTestId("terminal-state")).toHaveText("Ready");

  await host.click();
  await page.keyboard.type("serve");
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("terminal-state")).toContainText("Running serve");
  await expect(page.getByTestId("rail-tab-terminal-1")).toContainText("serve");
  await page.locator(".right-rail").screenshot({ path: test.info().outputPath("terminal-running.png") });

  // Stop sends Ctrl-C; the shell says the command ended.
  await page.getByTestId("terminal-interrupt").click();
  await expect(host).toContainText("^C");
  await expect(page.getByTestId("terminal-state")).toHaveText("Ready");

  // Closing a tab whose command runs asks first, then stops the shell.
  await host.click();
  await page.keyboard.type("serve");
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("terminal-state")).toContainText("Running serve");
  await page.getByTestId("rail-tab-close-terminal-1").click();
  await expect(page.getByTestId("term-stop-dialog")).toContainText("Stop serve and close?");
  const [closed] = await Promise.all([
    page.waitForRequest((r) => r.url().includes("/terminal?terminal=t1") && r.method() === "DELETE"),
    page.getByTestId("term-stop-confirm").click(),
  ]);
  expect(closed).toBeTruthy();
  await expect(page.getByTestId("terminal-panel")).toHaveCount(0);
  await expect(page.getByTestId("rail-guide")).toBeVisible();
});

test("a failed command hands its output to the chat box, never sending it", async ({ page }) => {
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();
  const box = page.getByPlaceholder(/Ask anything/);
  await box.fill("/terminal");
  await page.getByTestId("slash-command-terminal").click();
  const host = page.getByTestId("terminal-host");
  await expect(page.getByTestId("terminal-state")).toHaveText("Ready");

  await host.click();
  await page.keyboard.type("fail");
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("terminal-failed")).toContainText("Exit 1");
  await page.locator(".right-rail").screenshot({ path: test.info().outputPath("terminal-failed.png") });
  await page.getByTestId("terminal-ask-failed").click();
  await expect(box).toHaveValue(/This command failed in my terminal \(exit 1\)/);
  await expect(box).toHaveValue(/the build failed/);
  await expect(page.getByTestId("terminal-ask-failed")).toHaveText("Added to your message ✓");
});

test("the guide's shell menu launches the chosen interpreter and remembers it", async ({ page }) => {
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();

  // The Start guide is the resting surface; the terminal door's chevron opens the menu
  // (the shell catalogue comes from the backend).
  await page.getByTestId("rail-door-terminal-menu").click();
  await page.getByTestId("rail-shell-cmd").click();

  await expect(page.getByTestId("terminal-panel")).toBeVisible();
  await expect(page.getByTestId("rail-tab-terminal-1")).toBeVisible();
  // The pick is remembered per device (the door's chip and the next launch read it).
  expect(await page.evaluate(() => localStorage.getItem("reeve:rail-shell:v1"))).toBe("cmd");
});
