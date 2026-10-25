// The fetch card in a conversation: a finished web_fetch row says what came of it, and its card
// shows the page and what the call got (FetchCard.tsx). Invented pages, in the shape
// `fetch_present._fetch_card` sends.
import { expect } from "@playwright/test";
import { sendSessionEvent, test } from "./fixtures";

async function fetched(page: import("@playwright/test").Page, callId: string, args: object, card: object) {
  await sendSessionEvent(page, "tool_proposed", { name: "web_fetch", arguments: args, call_id: callId });
  await sendSessionEvent(page, "tool_finished", {
    name: "web_fetch",
    status: "ok",
    call_id: callId,
    result_preview: "",
    presentation: { summary: "Read a page", kind: "fetch", card },
  });
}

test("a fetch row says what came of it, and its card shows the answer and its checked quotes", async ({ page }) => {
  await page.goto("/");
  await sendSessionEvent(page, "turn_start", {});
  await fetched(
    page,
    "f-answer",
    { url: "https://docs.widgets.example/tasks", prompt: "What cancels the group?" },
    {
      card: "web",
      kind: "fetch",
      url: "https://docs.widgets.example/tasks",
      title: "Widget tasks — Widgets docs",
      status_code: 200,
      mode: "answer",
      read: { page_chars: 54000 },
      answer: {
        question: "What cancels the group?",
        text: "The **first failure** cancels the rest.",
        by: "reader-model",
        quotes: [
          { quote: "the remaining tasks are cancelled", found: true },
          { quote: "always re-raised immediately", found: false },
        ],
        quotes_total: 2,
        quotes_found: 1,
      },
    },
  );
  await sendSessionEvent(page, "turn_done", {});

  const row = page.getByText("answered · 1 of 2 quotes found").first();
  await expect(row).toBeVisible();
  const card = page.locator("[data-web='fetch'][data-mode='answer']").first();
  if (!(await card.isVisible())) await row.click();
  await expect(card.getByText("Widget tasks — Widgets docs")).toBeVisible();
  await expect(card.getByTestId("web-fetch-quotes")).toContainText("1 of 2 found");
  await expect(card.locator("li:not([data-found])")).toContainText("not in the page");
});

test("a page that was not there reads as a refusal, in the row and on the card", async ({ page }) => {
  await page.goto("/");
  await sendSessionEvent(page, "turn_start", {});
  await fetched(
    page,
    "f-404",
    { url: "https://docs.widgets.example/gone" },
    {
      card: "web",
      kind: "fetch",
      url: "https://docs.widgets.example/gone",
      title: "Page not found",
      status_code: 404,
      mode: "page",
      excerpt: "Nothing is at this address.",
      excerpt_kind: "markdown",
      read: { start: 0, end: 27, page_chars: 27 },
    },
  );
  await sendSessionEvent(page, "turn_done", {});

  const row = page.getByText("HTTP 404").first();
  await expect(row).toBeVisible();
  const card = page.locator("[data-web='fetch']").first();
  if (!(await card.isVisible())) await row.click();
  await expect(card.getByTestId("web-fetch-status")).toHaveAttribute("data-tone", "danger");
  await expect(card.getByTestId("web-fetch-excerpt")).toContainText("Nothing is at this address.");
});
