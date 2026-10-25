// The fetch card (FetchCard.tsx): the page as its head, what the call got as its body, how much
// of the page that was as its foot. Cards in the shape `fetch_present._fetch_card` sends.

import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { FetchCard } from "./FetchCard";
import type { WebFetchCard } from "./types";

afterEach(cleanup);

const URL_ = "https://docs.widgets.example/3/library/widget-tasks.html";

function card(extra: Partial<WebFetchCard> = {}): WebFetchCard {
  return { card: "web", kind: "fetch", url: URL_, ...extra };
}

describe("FetchCard head", () => {
  it("names the page by its title, which opens it, over the address as a trail", () => {
    render(<FetchCard card={card({ title: "Widget tasks — Widgets docs" })} />);
    const title = screen.getByText("Widget tasks — Widgets docs");
    expect(title.closest("a")?.getAttribute("href")).toBe(URL_);
    expect(screen.getByTestId("web-fetch-meta").textContent).toBe("docs.widgets.example3 › library › widget-tasks.html");
  });

  it("falls back to the host, and never links an address that is not the web", () => {
    render(<FetchCard card={card({ url: "javascript:alert(1)" })} />);
    expect(document.querySelector("a[href^='javascript']")).toBeNull();
  });

  it("colours a status by what it means and invents none it was not given", () => {
    const { rerender } = render(<FetchCard card={card()} />);
    expect(screen.queryByTestId("web-fetch-status")).toBeNull();
    for (const [code, tone] of [[200, "ok"], [304, "neutral"], [404, "danger"], [503, "danger"]] as const) {
      rerender(<FetchCard card={card({ status_code: code })} />);
      const status = screen.getByTestId("web-fetch-status");
      expect([status.textContent, status.getAttribute("data-tone")]).toEqual([String(code), tone]);
    }
  });
});

describe("FetchCard body", () => {
  it("renders a Markdown preview as the page's structure", () => {
    render(
      <FetchCard
        card={card({ mode: "page", excerpt_kind: "markdown", excerpt: "Intro.\n\n## Running a widget\n\n- one\n- two" })}
      />,
    );
    const body = screen.getByTestId("web-fetch-excerpt");
    expect(body.querySelector("h2")?.textContent).toBe("Running a widget");
    expect(body.querySelectorAll("li")).toHaveLength(2);
  });

  it("shows text and code as they are, and an older card's raw excerpt as text", () => {
    const { rerender } = render(<FetchCard card={card({ excerpt: '{\n  "a": 1\n}', excerpt_kind: "code" })} />);
    expect(screen.getByTestId("web-fetch-excerpt").querySelector("pre.web-fetch-code")?.textContent).toBe('{\n  "a": 1\n}');
    rerender(<FetchCard card={card({ excerpt: "## not a heading here" })} />);
    const body = screen.getByTestId("web-fetch-excerpt");
    expect(body.querySelector("h2")).toBeNull();
    expect(body.querySelector("pre.web-fetch-text")?.textContent).toBe("## not a heading here");
  });

  it("says when the page held no text", () => {
    render(<FetchCard card={card({ mode: "page" })} />);
    expect(screen.getByText("The page held no text.")).toBeTruthy();
  });

  it("shows an answer with its question and each quote marked found or not", () => {
    render(
      <FetchCard
        card={card({
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
            quotes_total: 11,
            quotes_found: 7,
            parts: 3,
          },
        })}
      />,
    );
    const answer = screen.getByTestId("web-fetch-answer");
    expect(answer.textContent).toContain("What cancels the group?");
    expect(answer.querySelector("strong")?.textContent).toBe("first failure");
    // The counts are the server's, over all eleven quotes, not the two the card carries.
    expect(screen.getByTestId("web-fetch-quotes").textContent).toContain("7 of 11 found");
    const rows = screen.getByTestId("web-fetch-quotes").querySelectorAll("li");
    expect([...rows].map((row) => row.hasAttribute("data-found"))).toEqual([true, false]);
    expect(rows[1].textContent).toContain("not in the page");
    expect(screen.getByTestId("web-fetch-foot").textContent).toContain(
      "Answered by reader-model · from 54k characters · read in 3 parts",
    );
  });

  it("says when an answer gave nothing to check", () => {
    render(<FetchCard card={card({ mode: "answer", answer: { text: "Yes.", quotes: [] } })} />);
    expect(screen.getByText("The answer gave no quotes to check")).toBeTruthy();
  });

  it("lists each place a phrase occurs, at its position, with the phrase marked", () => {
    render(
      <FetchCard
        card={card({
          mode: "find",
          found: { phrase: "task group", count: 18, matches: [{ at: 2700, text: "A Task  Group runs tasks." }] },
        })}
      />,
    );
    const find = screen.getByTestId("web-fetch-find");
    expect(find.textContent).toContain("18 matches for “task group” · first 1 shown");
    expect(find.querySelector("mark")?.textContent).toBe("Task  Group");
    expect(find.querySelector(".web-fetch-at")?.textContent).toBe("2.7k");
  });

  it("says when a phrase is not in the page", () => {
    render(<FetchCard card={card({ mode: "find", read: { page_chars: 6900 }, found: { phrase: "zebra", count: 0, matches: [] } })} />);
    expect(screen.getByTestId("web-fetch-find").textContent).toBe("No match for “zebra” in 6.9k characters");
  });

  it("names where a redirect goes, linking it only when it is a web address", () => {
    const { rerender } = render(
      <FetchCard card={card({ mode: "redirect", redirect: { host: "docs.example", url: "https://docs.example/start" } })} />,
    );
    expect(screen.getByText("docs.example").closest("a")?.getAttribute("href")).toBe("https://docs.example/start");
    rerender(<FetchCard card={card({ mode: "redirect", redirect: { host: "x.example" } })} />);
    expect(screen.getByText("x.example").closest("a")).toBeNull();
    rerender(<FetchCard card={card({ mode: "redirect", redirect: { host: "y.example", url: "javascript:alert(1)" } })} />);
    expect(screen.getByText("y.example").closest("a")).toBeNull();
  });
});

describe("FetchCard foot", () => {
  it("says how much of the page was read, with a bar only when it knows the share", () => {
    const { rerender } = render(
      <FetchCard card={card({ mode: "page", excerpt: "x", read: { start: 0, end: 20000, page_chars: 54000 } })} />,
    );
    const foot = screen.getByTestId("web-fetch-foot");
    expect(foot.textContent).toContain("Characters 0–20k of 54k");
    const share = foot.querySelector<HTMLElement>(".web-fetch-bar > span")!;
    expect([share.style.left, share.style.width]).toEqual(["0%", `${(20000 / 54000) * 100}%`]);
    rerender(<FetchCard card={card({ excerpt: "x", chars: 12400, truncated: true })} />);
    expect(screen.getByTestId("web-fetch-foot").textContent).toBe("12k characters · part of the page");
    expect(screen.getByTestId("web-fetch-foot").querySelector(".web-fetch-bar")).toBeNull();
  });

  it("names how the page was read, warnings in their tone", () => {
    render(
      <FetchCard
        card={card({ mode: "page", excerpt: "x", about: { left_out_chars: 1900, plain_http: true, cached_seconds: 125 } })}
      />,
    );
    const foot = screen.getByTestId("web-fetch-foot");
    expect(foot.textContent).toContain("Main content: 1.9k characters of menus left out");
    expect(foot.textContent).toContain("This session's copy, fetched 2 min ago");
    expect(foot.querySelector("[data-tone='warn']")?.textContent).toContain("plain http");
  });

  it("has no foot when there is nothing to say", () => {
    render(<FetchCard card={card({ mode: "pdf" })} />);
    expect(screen.queryByTestId("web-fetch-foot")).toBeNull();
    expect(screen.getByText("A PDF, attached for the model to read.")).toBeTruthy();
  });
});
