import type { Page } from "@playwright/test";
import { test, expect } from "./fixtures";

// What ask_user can ask: options with descriptions, a Recommended mark and a monospace
// preview beside them; several questions stepped through; and a brief of many as one form. The
// inbox is answered here for each test (a later route wins), so the mock's own items, which
// inbox.spec.ts counts, stay as they are.

/** A question waiting in the ops session. */
const question = (id: string, fields: Record<string, unknown>) => ({
  id: `inb-question-${id}`, session_id: "ops-1", kind: "question", body: "", state: "pending", resolution: null as string | null,
  inbox: "default", created_at: "2026-07-29 08:00:00", resolved_at: null as string | null,
  session_title: "Investigate alerts", session_agent: "ops", session_workspace: "", session_exists: true,
  allow_text: true, multi: false, questions: [] as unknown[],
  ...fields,
});

/** A question asked as several: the first one also stands as the item's title and options, as
 *  the server writes it for a surface that knows only one. */
const grouped = (id: string, questions: Array<{ question: string; header: string; options: unknown[]; allow_text?: boolean; multi?: boolean }>) =>
  question(id, {
    title: questions[0].question, header: questions[0].header, options: questions[0].options,
    allow_text: questions[0].allow_text ?? true, multi: false, questions,
  });

/** Serve these items as the inbox, answering a resolve on this copy of them. */
async function seedInbox(page: Page, items: Record<string, unknown>[]) {
  const inbox = items.map((item) => ({ ...item }));
  await page.route(/\/v1\/inbox\/([^/]+)\/resolve$/, (route) => {
    const id = decodeURIComponent(new URL(route.request().url()).pathname.split("/").slice(-2)[0]);
    const item = inbox.find((row) => row.id === id);
    if (item) Object.assign(item, { state: "resolved", resolution: route.request().postDataJSON().resolution });
    return route.fulfill({ json: { ok: true } });
  });
  await page.route(/\/v1\/inbox(\?.*)?$/, (route) => route.fulfill({ json: { items: inbox.filter((row) => row.state === "pending") } }));
}

/** Open the app with `items` waiting, until `heading` shows. A question waits in its session's
 *  own dock, over the composer: there is no page of waiting questions any more. Returns that view. */
async function waitingWith(page: Page, items: Record<string, unknown>[], heading: string) {
  await seedInbox(page, items);
  await page.goto("/");
  const view = page.locator(".main").first();
  await expect(view.getByText(heading)).toBeVisible();
  return view;
}

/** The resolution the next resolve sends, once `answer` has been given. */
async function resolution(page: Page, answer: () => Promise<void>): Promise<string> {
  const sent = page.waitForRequest((r) => r.url().includes("/resolve") && r.method() === "POST");
  await answer();
  return (await sent).postDataJSON().resolution;
}

test("rich options render descriptions + Recommended; the preview pane follows hover", async ({ page }) => {
  const view = await waitingWith(page, [question("rich", {
    title: "How should I format the report?", header: "Format",
    options: [
      { label: "Markdown table", description: "Compact and renders in the app", recommended: true, preview: "| env | status |\n| --- | --- |\n| staging | ok |" },
      { label: "Plain text", description: "Safest for email forwarding", preview: "env: staging\nstatus: ok" },
    ],
  })], "How should I format the report?");

  await expect(view.getByText("Compact and renders in the app")).toBeVisible();
  await expect(view.getByText("Recommended")).toBeVisible();
  // The preview shows the first option that has one, then whichever the pointer is on.
  const pane = view.getByTestId("question-preview");
  await expect(pane).toContainText("| env | status |");
  await view.getByRole("button", { name: /Plain text/ }).hover();
  await expect(pane).toContainText("env: staging");

  // One choice of one question: a click answers it, with the option's label.
  expect(await resolution(page, () => view.getByRole("button", { name: /Markdown table/ }).click())).toBe("Markdown table");
  await expect(view.getByText("How should I format the report?")).not.toBeVisible();
});

test("grouped questions step through the header chips and resolve as one answer map", async ({ page }) => {
  const view = await waitingWith(page, [grouped("grouped", [
    { question: "Chart style?", header: "Chart style", options: ["Bar", "Line"], allow_text: false, multi: false },
    { question: "Which distribution?", header: "Distribution", options: ["Stacked", "Grouped"], allow_text: true, multi: false },
  ])], "Chart style?");
  const stepper = view.getByTestId("question-stepper");
  const ownWords = view.getByPlaceholder("Something else? Type it here…");

  // The first of two, with no box for one's own words (it allows none), though the choice can
  // still be left to the agent.
  await expect(stepper).toContainText("Chart style");
  await expect(stepper).toContainText("1 of 2");
  await expect(ownWords).not.toBeVisible();
  await expect(view.getByTestId("question-leave")).toHaveText("Let the agent choose");
  await view.getByTestId("question-card").screenshot({ path: test.info().outputPath("question-leave.png") });

  // An answer moves on to the second, which takes one's own words.
  await view.getByRole("button", { name: "Bar", exact: true }).click();
  await expect(stepper).toContainText("2 of 2");
  await expect(view.getByText("Which distribution?")).toBeVisible();
  await expect(ownWords).toBeVisible();
  // Back, where the first can be answered again, and forward.
  await view.getByRole("button", { name: "Previous question" }).click();
  await expect(stepper).toContainText("1 of 2");
  await view.getByRole("button", { name: "Bar", exact: true }).click();
  await expect(stepper).toContainText("2 of 2");

  // The last answer sends them all, as JSON keyed by header, and the dock empties.
  expect(await resolution(page, () => view.getByRole("button", { name: "Stacked", exact: true }).click()))
    .toBe(JSON.stringify({ "Chart style": "Bar", Distribution: "Stacked" }));
  await expect(view.getByTestId("question-stepper")).toHaveCount(0);
});

test("a brief of eight questions is one form: answer what matters, the rest goes to the agent", async ({ page }) => {
  const choices = (...labels: Array<string | { label: string; recommended?: boolean; description?: string }>) =>
    labels.map((l) => (typeof l === "string" ? { label: l } : l));
  const view = await waitingWith(page, [grouped("brief", [
    { question: "Who is the page for?", header: "Audience", options: choices({ label: "Small teams", recommended: true, description: "3–20 people sharing notes" }, "Solo", "Companies") },
    { question: "What should a visitor do after reading?", header: "Action", options: choices({ label: "Start free", recommended: true }, "Talk to sales", "Compare plans") },
    { question: "Which content already exists?", header: "Content", options: [], allow_text: true },
    { question: "A page to follow?", header: "Reference", options: [], allow_text: true },
    { question: "How should it feel?", header: "Direction", options: choices({ label: "Calm editorial", recommended: true }, "Bold product", "Playful") },
    { question: "How dense?", header: "Density", options: choices("Roomy", "Compact") },
    { question: "Light or dark?", header: "Theme", options: choices({ label: "Light", recommended: true }, "Dark", "Both") },
    { question: "Which states to draw?", header: "States", multi: true, options: choices({ label: "Empty", recommended: true }, "Loading", { label: "Error", recommended: true }) },
  ])], "8 questions before I start");
  const card = view.getByTestId("question-card");

  await expect(card.getByTestId("question-form-item")).toHaveCount(8);
  await expect(card).toContainText("3 left to the agent");
  // A recommended choice starts picked, with its reason shown.
  await expect(card.getByRole("button", { name: /Small teams/ })).toHaveAttribute("aria-pressed", "true");
  await expect(card).toContainText("3–20 people sharing notes");

  // The form stays inside the window at every size, measured once its glide to the new size ends.
  for (const size of [{ width: 1440, height: 900 }, { width: 1024, height: 700 }, { width: 760, height: 900 }]) {
    await page.setViewportSize(size);
    await card.evaluate((el) => Promise.all(el.getAnimations({ subtree: true }).map((a) => a.finished)));
    await page.waitForTimeout(400);
    const box = (await card.boundingBox())!;
    expect(box.x).toBeGreaterThanOrEqual(0);
    expect(box.x + box.width).toBeLessThanOrEqual(size.width);
    expect(box.y + box.height).toBeLessThanOrEqual(size.height);
    await card.screenshot({ path: test.info().outputPath(`question-form-${size.width}.png`) });
  }
  await page.setViewportSize({ width: 1440, height: 900 });

  await card.getByRole("button", { name: "Talk to sales" }).click();
  await card.getByLabel("Your own answer to: Which content already exists?").fill("Free, Team $8, Business $16");
  // A question with choices has its own-words box only after "Other…".
  await expect(card.getByLabel("Your own answer to: Who is the page for?")).toHaveCount(0);
  // Unpicking the recommended feel leaves it to the agent.
  await card.getByRole("button", { name: "Calm editorial" }).click();
  const answers = JSON.parse(await resolution(page, () => card.getByTestId("question-send").click()));
  const left = "Up to you: choose what fits best, and say in your answer what you chose.";
  expect(answers).toEqual({
    Audience: "Small teams", Action: "Talk to sales", Content: "Free, Team $8, Business $16",
    Reference: left, Direction: left, Density: left, Theme: "Light", States: "Empty, Error",
  });
});
