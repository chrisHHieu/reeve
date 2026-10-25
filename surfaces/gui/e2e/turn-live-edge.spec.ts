// The running turn's live edge: the status line stands on its own under the steps, with no
// rail segment running into it (owner, 2026-09-30, Claude Code's "✻ Working…"; it used to
// be the rail's last station, 2026-09-28), and a reader who never scrolled always sees the
// status line whole above the composer — never half of it under the composer's edge.
import { expect } from "@playwright/test";
import { test, seedSessionMessages } from "./fixtures";

async function start(page: import("@playwright/test").Page, text: string, ready = "family-group") {
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();
  const box = page.getByPlaceholder(/Ask anything/);
  await box.fill(text);
  await box.press("Enter");
  await expect(page.getByTestId(ready).first()).toBeVisible({ timeout: 10_000 });
}

/** How far the running status line's bottom sits below the scroll box's bottom (≤ 0: whole). */
const statusOverhang = `(() => {
  const scroller = document.querySelector(".main-scroll");
  const status = [...document.querySelectorAll('[data-testid="turn-status"][data-state="running"]')].pop();
  if (!scroller || !status) return null;
  return status.getBoundingClientRect().bottom - scroller.getBoundingClientRect().bottom;
})()`;

test("the working line stands apart: the rail ends at the steps and does not run into it", async ({ page }) => {
  await start(page, "run the long job");
  // Rows slide in (a ~200ms transform); the geometry below is the landed one.
  await page.waitForFunction(() =>
    document.getAnimations().every((a) => a.effect?.getTiming().iterations === Infinity || a.playState !== "running"),
  );
  await page.screenshot({ path: "test-results/live-edge-rail.png" });
  const gap = await page.evaluate(() => {
    const group = [...document.querySelectorAll('.stepgroup[data-state="running"]')].pop()!;
    const rail = group.querySelector(".turn-rail")!;
    const status = group.querySelector(".turn-status")!;
    const railLine = getComputedStyle(rail, "::before");
    const statusLine = getComputedStyle(status, "::before");
    const dots = group.querySelector(".turn-dots")!.getBoundingClientRect();
    const icon = [...group.querySelectorAll(".turn-rail .tool-row-leading")].pop()!.getBoundingClientRect();
    return {
      railDrawn: railLine.content !== "none" && railLine.backgroundColor !== "rgba(0, 0, 0, 0)",
      // No segment on the status line itself any more.
      statusDrawn: statusLine.content !== "none" && statusLine.content !== "normal",
      // A visible gap between the rail's end and the status line.
      seam: status.getBoundingClientRect().top - rail.getBoundingClientRect().bottom,
      // The working mark still sits in the icon column, lined up with the steps' icons.
      columnDrift: Math.abs((dots.left + dots.right) / 2 - (icon.left + icon.right) / 2),
      // The line stops at the last step's icon: no stub hanging under it (owner, 2026-09-30).
      stub: rail.getBoundingClientRect().bottom - parseFloat(railLine.bottom) - (icon.top + icon.bottom) / 2,
    };
  });
  expect(gap.railDrawn).toBe(true);
  expect(gap.statusDrawn).toBe(false);
  expect(gap.seam).toBeGreaterThanOrEqual(0);
  expect(gap.columnDrift).toBeLessThanOrEqual(2);
  expect(Math.abs(gap.stub)).toBeLessThanOrEqual(1);
});

test("a reader who never scrolled sees the whole status line when a row opens or the dock appears", async ({ page }) => {
  await start(page, "run the build job", "turn-step");
  const overhangs: number[] = [];
  for (let i = 0; i < 16; i++) {
    await page.waitForTimeout(200);
    // Measured after two frames, so a size change has had its frame to be followed.
    const value = await page.evaluate(
      (expr) =>
        new Promise<number | null>((resolve) =>
          requestAnimationFrame(() => requestAnimationFrame(() => resolve(eval(expr)))),
        ),
      statusOverhang,
    );
    if (value !== null) overhangs.push(value);
  }
  await page.screenshot({ path: "test-results/live-edge-follow.png" });
  expect(overhangs.length).toBeGreaterThan(10);
  expect(overhangs.filter((v) => v > 1).length).toBeLessThanOrEqual(2);
  await expect(page.getByTestId("jump-to-latest")).toHaveCount(0);
});

test("a session opened from another one still ends its rail at the last step", async ({ page }) => {
  // Owner, 2026-09-30: a stub of rail hung under "Thought · 26s" in a session reached from
  // another one. The end is measured after layout; on that path it was measured while the
  // rows were not laid out yet and never again.
  await seedSessionMessages(page, "pinned-session-1", [
    { role: "user", content: "Audit the release branch" },
    {
      role: "assistant",
      content: "",
      reasoning: "Compare the log against the changelog.",
      tool_calls: [{ id: "t1", function: { name: "run_shell", arguments: JSON.stringify({ command: "git log -5" }) } }],
    },
    { role: "tool", tool_call_id: "t1", content: "abc123 release: cut 0.1.7" },
    { role: "assistant", content: "", reasoning: "Both entries line up." },
    { role: "assistant", content: "The branch is clean." },
  ]);
  await page.goto("/");
  await page.getByText("Weekly plan 1").first().click();
  await page.getByText("Draft the launch note").first().click();
  await expect(page.getByText("The branch is clean.")).toBeVisible();
  await page.waitForTimeout(600);
  const stubs = await page.evaluate(() =>
    [...document.querySelectorAll(".turn-rail")].map((rail) => {
      const box = rail.getBoundingClientRect();
      const stations = [...rail.querySelectorAll(".tool-row-leading, .think-row-leading, .live-call-ico")].filter(
        (s) => s.getBoundingClientRect().height > 0,
      );
      const last = stations[stations.length - 1]?.getBoundingClientRect();
      const lineBottom = box.bottom - parseFloat(getComputedStyle(rail, "::before").bottom);
      return last ? lineBottom - (last.top + last.height / 2) : 0;
    }),
  );
  expect(stubs.length).toBeGreaterThan(0);
  for (const stub of stubs) expect(Math.abs(stub)).toBeLessThanOrEqual(1);
});
