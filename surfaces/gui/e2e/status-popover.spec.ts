// The status popover in the session topbar: the trigger wears a pip only for a problem, the
// head is the verdict, what works is groups that open onto the fixture's services, and its
// links land on the surfaces that own the fixes.
//
// Fixture defaults: MCP is empty and Slack/GitHub are connected and live.
import { expect } from "@playwright/test";
import { test } from "./fixtures";

test("status popover: verdict, groups, rows, links", async ({ page }) => {
  await page.goto("/");

  const trigger = page.getByTestId("status-trigger");
  await expect(trigger).toBeVisible();
  // Nothing is wrong in the fixture: connectors are live, MCP and LSP are simply empty. So
  // the button wears no pip: the pip is for a problem.
  await expect(trigger).toHaveAttribute("aria-label", "Status");
  await expect(page.getByTestId("status-trigger-dot")).toHaveCount(0);

  await trigger.click();
  await expect(page.getByTestId("status-popover")).toBeVisible();
  await expect(page.getByTestId("status-head")).toHaveText("Everything is working");
  // Nothing needs a person, so there is no such list, and no tabs to look behind.
  await expect(page.getByTestId("status-problems")).toHaveCount(0);
  await expect(page.getByRole("tab")).toHaveCount(0);

  // Connectors: one line that names them, and a row each once it is opened.
  const connectors = page.getByTestId("status-group-connectors");
  await expect(connectors).toContainText("Slack");
  await expect(page.getByTestId("status-row-slack")).toHaveCount(0);
  await page.getByTestId("status-group-toggle-connectors").click();
  await expect(page.getByTestId("status-row-slack")).toBeVisible();
  await expect(page.getByTestId("status-row-github")).toBeVisible();
  // A working thing does not say that it works.
  await expect(page.getByTestId("status-row-slack")).not.toContainText("Live");

  // MCP: none in the fixture, so there is no group for it.
  await expect(page.getByTestId("status-group-mcp")).toHaveCount(0);

  // Code intelligence: the pool starts empty (servers start on first use).
  await expect(page.getByTestId("status-lsp-idle")).toContainText("python");

  // Skills: the fixture's rows with their scopes.
  await page.getByTestId("status-group-toggle-skills").click();
  await expect(page.getByTestId("status-row-weekly-report")).toContainText("weekly-report");
  await expect(page.getByTestId("status-row-weekly-report")).toContainText("global");
  await expect(page.getByTestId("status-row-html-to-markdown")).toContainText("html-to-markdown");

  // Closing a group puts its rows away.
  await page.getByTestId("status-group-toggle-connectors").click();
  await expect(page.getByTestId("status-row-slack")).toHaveCount(0);

  // Escape closes.
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("status-popover")).toHaveCount(0);

  // Reopen, and let the Skills link land in Settings ▸ Skills.
  await page.getByTestId("status-trigger").click();
  await page.getByTestId("status-group-toggle-skills").click();
  await page.getByTestId("status-skills-more").getByRole("button", { name: "Open Skills" }).click();
  await expect(page.getByRole("button", { name: "Skills", exact: true })).toBeVisible();
});

test("a press anywhere outside closes it: the conversation, the panel, a page framed in the panel", async ({ page }) => {
  // The backdrop lived in the session header, whose backdrop-filter made it the box the `fixed`
  // cover was laid out in: only a press on the header closed the popover (owner, 2026-10-20).
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();
  const trigger = page.getByTestId("status-trigger");
  const popover = page.getByTestId("status-popover");

  await trigger.click();
  await expect(popover).toBeVisible();
  // A press inside it is not a press away.
  await popover.getByTestId("status-head").click();
  await expect(popover).toBeVisible();
  // The conversation, well below the header.
  await page.mouse.click(300, 700);
  await expect(popover).toHaveCount(0);

  // The panel beside it.
  await trigger.click();
  await expect(popover).toBeVisible();
  await page.mouse.click(1200, 600);
  await expect(popover).toHaveCount(0);

  // A page the panel frames: its presses never reach this document; the window losing focus does.
  await page.getByTestId("rail-new").click();
  await page.getByTestId("rail-new-browser").click();
  const origin = new URL(page.url()).origin;
  await page.getByTestId("browser-address").fill(origin.replace(/^http:\/\//, ""));
  await page.getByTestId("browser-address").press("Enter");
  const frame = page.getByTestId("browser-frame");
  await expect(frame).toHaveAttribute("src", origin);
  await trigger.click();
  await expect(popover).toBeVisible();
  const box = (await frame.boundingBox())!;
  await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
  await expect(popover).toHaveCount(0);
});
