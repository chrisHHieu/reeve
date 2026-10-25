// Owner review (2026-09-28): a decision takes the composer's place and nothing else's — it
// sits in the composer's column at the composer's width (the plan dock used to span the
// window once the side panels were closed), the To-dos strip steps aside while it waits,
// and the conversation says which turn you are reading once your message has scrolled away.
import { expect, type Page } from "@playwright/test";
import { test, seedSessionMessages } from "./fixtures";

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem("reeve-e2e-rail-default", "1");
    localStorage.setItem("reeve:rail-hidden:v1", "1");
  });
});

async function open(page: Page) {
  await page.setViewportSize({ width: 1400, height: 800 });
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();
}

async function ask(page: Page, text: string) {
  const box = page.getByPlaceholder(/Ask anything/);
  await box.fill(text);
  await box.press("Enter");
}

const width = (page: Page, selector: string) =>
  page.locator(selector).first().evaluate((el) => el.getBoundingClientRect().width);

test("a question replaces the composer at its width, and the plan stays in the task's head", async ({ page }) => {
  await open(page);
  const composerWidth = await width(page, ".composer");
  await ask(page, "ask me which first");
  await expect(page.getByText("Where do we start?").first()).toBeVisible();
  // The plan is the task's head, not a strip over the input: nothing makes it step aside.
  await expect(page.locator('[data-placement="head"]').getByTestId("todo-dock")).toBeVisible();
  // Polled: the card arrives with a short scale-in, and a width read mid-way is not its width.
  await expect
    .poll(async () => Math.abs((await width(page, '[data-testid="question-card"]')) - composerWidth))
    .toBeLessThan(2);
  // One Send in the whole card.
  await expect(page.getByTestId("decision-wrap").getByRole("button", { name: /^Send/ })).toHaveCount(1);

  await page.getByText("#2 Report by tag").click();
  await expect(page.getByText("Starting with: #2 Report by tag").first()).toBeVisible();
  await expect(page.getByTestId("todo-dock")).toBeVisible();
});

test("the plan dock stays in the conversation's column with the side panels closed", async ({ page }) => {
  await open(page);
  await page.getByRole("button", { name: "Collapse sidebar" }).click();
  await ask(page, "propose a plan");
  const dock = page.getByTestId("plan-dock");
  await expect(dock).toBeVisible();
  await page.waitForTimeout(500); // the sidebar's collapse animation has settled
  const box = (await dock.boundingBox())!;
  // What is asked, the plan's title and steps, one row that answers: a card, not a page.
  expect(box.height).toBeLessThan(300);
  await expect(dock).toContainText("Goal contributions back the figure");

  await page.keyboard.press("1");
  await expect(page.getByText("Plan approved — implementing.").first()).toBeVisible();
  // The composer that comes back has the dock's width: the dock sat in its column.
  expect(Math.abs((await width(page, ".composer")) - box.width)).toBeLessThan(2);
});

for (const size of [
  { width: 1400, height: 800 },
  { width: 1024, height: 700 },
  { width: 480, height: 800 },
]) {
  test(`a plan named by a long first step keeps its question whole at ${size.width}px`, async ({ page }) => {
    // The owner's screenshot (2026-10-15): the context would not wrap or shrink, so it squeezed
    // "Approve this plan?" to one word a line and ran past the card's edge.
    await open(page);
    await page.setViewportSize(size);
    await ask(page, "propose an unheaded plan");
    const dock = page.getByTestId("plan-dock");
    await expect(dock).toBeVisible();
    const title = dock.locator(".dec-t");
    await expect(title).toHaveText("Approve this plan?");
    const line = await title.evaluate((el) => parseFloat(getComputedStyle(el).lineHeight));
    expect((await title.boundingBox())!.height).toBeLessThan(line * 1.5);
    const card = (await dock.boundingBox())!;
    const context = (await dock.locator(".dec-c").boundingBox())!;
    expect(context.x + context.width).toBeLessThanOrEqual(card.x + card.width + 0.5);
    // The step count is never the part cut.
    await expect(dock.locator(".dec-c")).toContainText("4 steps");
    await page.screenshot({ path: test.info().outputPath(`plan-dock-${size.width}.png`) });
  });
}

test("the turn you are reading is pinned once your message has scrolled away", async ({ page }) => {
  const messages: Record<string, unknown>[] = [];
  for (let i = 1; i <= 5; i++) {
    messages.push({ role: "user", content: `Question ${i} about the goals`, ts: 1755600000 + i * 100 });
    messages.push({
      role: "assistant",
      content: `Answer ${i}.\n\n` + "A line of detail about the work. ".repeat(60),
      ts: 1755600000 + i * 100 + 50,
    });
  }
  await seedSessionMessages(page, "pinned-session-1", messages);
  await open(page);
  await expect(page.getByText("Answer 5.").first()).toBeVisible();
  const scroller = page.locator(".main-scroll");
  const pin = page.getByTestId("turn-pin");

  // At the live bottom nothing is pinned, though turn 5's message is above the view:
  // the turn being read there is the latest one, and a pin would only be motion.
  await expect
    .poll(() => scroller.evaluate((el) => el.scrollHeight - el.scrollTop - el.clientHeight))
    .toBeLessThan(48);
  await expect(pin).not.toHaveAttribute("data-shown", "");

  await scroller.evaluate((el) => (el.scrollTop = 0));
  await expect(pin).not.toHaveAttribute("data-shown", "");

  // Into the middle of turn 3's answer: turn 3's message is what is pinned.
  await scroller.evaluate((el) => {
    const third = el.querySelectorAll<HTMLElement>("[data-user-turn]")[2];
    el.scrollTop = third.offsetTop + 200;
  });
  await expect(pin).toHaveAttribute("data-shown", "");
  await expect(pin).toContainText("Question 3 about the goals");

  // Pressing the message goes back to it.
  await page.getByTestId("turn-pin-go").click();
  await expect
    .poll(() =>
      scroller.evaluate((el) => {
        const third = el.querySelectorAll<HTMLElement>("[data-user-turn]")[2];
        const top = third.getBoundingClientRect().top - el.getBoundingClientRect().top;
        return top >= 0 && top < el.clientHeight;
      }),
    )
    .toBe(true);
});
