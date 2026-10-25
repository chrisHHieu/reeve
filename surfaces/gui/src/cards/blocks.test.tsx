import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { DiffBlock } from "./DiffBlock";
import { TerminalBlock } from "./TerminalBlock";
import { ReadBlock } from "./ReadBlock";
import { SearchBlock } from "./SearchBlock";
import { WebBlock } from "./WebBlock";
import { CodeBlock } from "./CodeBlock";
import type { FileDiff, SearchResultCard } from "./types";

afterEach(cleanup);

// A hunk with context on both sides, the shape the engine sends: the sides are
// CONTEXT-WINDOWED, so the counted lines appear in both. The `added`/`removed` the engine
// supplies are the true churn and must be what the footer shows.
const HUNK: FileDiff = {
  path: "harness/engine.py",
  old_text: "ctx a\nctx b\nold line\nctx c",
  new_text: "ctx a\nctx b\nnew line\nctx c",
  added: 1,
  removed: 1,
};

describe("DiffBlock", () => {
  it("draws a unified diff and leaves the file's identity to the row", () => {
    render(<DiffBlock diffs={[HUNK]} />);
    // The card is the diff: no banner path, no status pill, no footer — the row header above
    // it already says `Edit · engine.py  harness  +1 −1`.
    expect(screen.queryByText("engine.py")).toBeNull();
    expect(screen.queryByText("edited")).toBeNull();
    expect(screen.queryByText(/└/)).toBeNull();
    const body = screen.getByTestId("diff-body").textContent ?? "";
    expect(body).toContain("ctx a");
    expect(body).toContain("old line");
    expect(body).toContain("new line");
  });

  it("offers copy, and a way to open a single-file diff", () => {
    const opened: string[] = [];
    render(
      <DiffBlock
        diffs={[{ path: "src/a.ts", old_text: "one", new_text: "two", added: 1, removed: 1 }]}
        onOpenFile={(path) => opened.push(path)}
      />,
    );
    // The tools strip carries the actions the row cannot: copy the patch, open the file.
    expect(screen.getByTestId("diff-tools")).toBeTruthy();
    expect(screen.getByTestId("copy-button")).toBeTruthy();
    fireEvent.click(screen.getByTestId("diff-open-file"));
    expect(opened).toEqual(["src/a.ts"]);
  });

  it("opens a second hunk in the same file with a gap, not a repeated path", () => {
    render(<DiffBlock diffs={[HUNK, { path: HUNK.path, old_text: "x", new_text: "y" }]} />);
    // One file means no per-file header (the header exists only to tell files apart).
    expect(document.querySelectorAll(".rv-diff-filehead")).toHaveLength(0);
    expect(screen.getByText("⋯")).toBeDefined();
  });

  it("names each file when one card carries several", () => {
    render(
      <DiffBlock
        diffs={[
          { path: "src/a.ts", old_text: "one", new_text: "two" },
          { path: "src/b.ts", old_text: null, new_text: "new\n" },
        ]}
      />,
    );
    // Without the banner this header is the only place a multi-file patch is legible.
    const heads = [...document.querySelectorAll(".rv-diff-filehead")].map((el) => ({
      name: el.firstChild?.textContent,
      dir: el.querySelector("span")?.textContent,
    }));
    expect(heads).toEqual([
      { name: "a.ts", dir: "src" },
      { name: "b.ts", dir: "src" },
    ]);
  });

  it("treats a trailing newline as a terminator, not an extra blank line", () => {
    render(<DiffBlock diffs={[{ path: "a.txt", old_text: null, new_text: "one\n" }]} />);
    const body = screen.getByTestId("diff-body").textContent ?? "";
    expect(body).toContain("one");
    expect(screen.queryByText("\u00a0")).toBeNull();
  });

  it("renders the whole body in a scrolling card, keeping both ends", () => {
    const many = Array.from({ length: 40 }, (_, i) => `line ${i}`).join("\n");
    render(<DiffBlock diffs={[{ path: "a.txt", old_text: null, new_text: many }]} />);
    // No "… more lines" control: a tall body scrolls inside the card instead.
    expect(screen.queryByTestId("diff-more")).toBeNull();
    const body = screen.getByTestId("diff-body");
    // Both ends are present: the head says what it is, the tail says how it finished.
    expect(body.textContent).toContain("line 0");
    expect(body.textContent).toContain("line 20");
    expect(body.textContent).toContain("line 39");
  });

  it("renders nothing for no hunks", () => {
    const { container } = render(<DiffBlock diffs={[]} />);
    expect(container.firstChild).toBeNull();
  });

  it("keeps the open affordance off a multi-file diff", () => {
    render(
      <DiffBlock
        diffs={[
          { path: "a.ts", old_text: "one", new_text: "two" },
          { path: "b.ts", old_text: "one", new_text: "two" },
        ]}
        onOpenFile={() => {}}
      />,
    );
    // "Open" cannot answer which of the two files it means, so it is not offered at all.
    expect(screen.queryByTestId("diff-open-file")).toBeNull();
    // Copy still is: the patch as a whole is one thing.
    expect(screen.getByTestId("copy-button")).toBeTruthy();
  });
});

describe("TerminalBlock", () => {
  it("shows the shortened working directory, the command, and the exit pill", () => {
    render(
      <TerminalBlock
        command="npm test"
        cwd="/home/dev/project"
        home="/home/dev"
        output="ok\n"
        exitCode={0}
      />,
    );
    expect(screen.getByText("~/project")).toBeDefined();
    expect(screen.getByTestId("terminal-command").textContent).toBe("npm test");
    expect(screen.getByTestId("terminal-status").textContent).toBe("exit 0");
  });

  it("colours a non-zero exit as a failure, and prefers a signal over a code", () => {
    const { rerender } = render(<TerminalBlock command="false" exitCode={2} output="" />);
    expect(screen.getByTestId("terminal-status").textContent).toBe("exit 2");
    expect(screen.getByTestId("terminal-status").getAttribute("data-tone")).toBe("danger");
    rerender(<TerminalBlock command="sleep 9" exitCode={143} signal="SIGTERM" output="" />);
    // A process killed by a signal is reported as killed, not as the number the shell
    // happened to synthesise for it.
    expect(screen.getByTestId("terminal-status").textContent).toBe("signal SIGTERM");
  });

  it("stays on its last line while the command runs, and is read from the top once it is over", () => {
    const tall = (el: HTMLElement, height: number) => {
      Object.defineProperty(el, "scrollHeight", { configurable: true, value: height });
      Object.defineProperty(el, "clientHeight", { configurable: true, value: 240 });
    };
    const { rerender } = render(<TerminalBlock command="npm run build" running output={"one\n"} />);
    const body = screen.getByTestId("terminal-output");
    tall(body, 900);
    rerender(<TerminalBlock command="npm run build" running output={"one\ntwo\n"} />);
    // Text that arrives below the fold of a bounded body is text nobody sees arrive.
    expect(body.scrollTop).toBe(900);
    cleanup();

    // Over: the same card, the same geometry, more text (a re-anchor) — and it does not move.
    const again = render(<TerminalBlock command="npm run build" exitCode={0} output={"one\n"} />);
    const settled = again.getByTestId("terminal-output");
    tall(settled, 900);
    again.rerender(<TerminalBlock command="npm run build" exitCode={0} output={"one\ntwo\n"} />);
    expect(settled.scrollTop).toBe(0);
    // A caller whose reader wants the END of a finished log says so.
    again.rerender(<TerminalBlock command="npm run build" exitCode={0} output={"one\ntwo\n"} follow />);
    tall(settled, 700);
    again.rerender(<TerminalBlock command="npm run build" exitCode={0} output={"one\ntwo\n3\n"} follow />);
    expect(settled.scrollTop).toBe(700);
  });

  it("keeps ANSI colour, mapped onto theme roles for the basic hues", () => {
    render(<TerminalBlock command="echo" output={"\u001b[31mred\u001b[0m plain"} />);
    const output = screen.getByTestId("terminal-output");
    const red = Array.from(output.querySelectorAll("span")).find(
      (node) => node.textContent === "red",
    );
    expect(red).toBeDefined();
    expect(red!.style.color).toContain("--danger");
  });

  it("shows the LAST frame of a redrawn line rather than every frame", () => {
    render(<TerminalBlock command="build" output={"10%\r50%\r100%\ndone"} />);
    const text = screen.getByTestId("terminal-output").textContent ?? "";
    expect(text).toContain("100%");
    expect(text).not.toContain("10%");
    expect(text).not.toContain("50%");
  });

  it("says when the output is short, and by how much", () => {
    // Without this notice the last visible line reads as the command's verdict.
    const { rerender } = render(
      <TerminalBlock command="pytest -q" output="1 failed" exitCode={1} truncated omittedLines={1203} />,
    );
    const foot = screen.getByTestId("terminal-truncated");
    expect(foot.textContent).toContain("output truncated");
    expect(foot.textContent).toContain("1,203 lines omitted");
    // An uncounted truncation still says the output is short.
    rerender(<TerminalBlock command="pytest -q" output="1 failed" exitCode={1} truncated />);
    expect(screen.getByTestId("terminal-truncated").textContent).toContain("output truncated");
    // A running command has no verdict to qualify yet.
    rerender(<TerminalBlock command="pytest -q" running truncated />);
    expect(screen.queryByTestId("terminal-truncated")).toBeNull();
  });

  it("reports no output rather than an empty box, and shows a running dot while in flight", () => {
    render(<TerminalBlock command="true" output="" exitCode={0} />);
    expect(screen.getByText("No output")).toBeDefined();
    cleanup();
    render(<TerminalBlock command="sleep 1" running />);
    expect(screen.queryByTestId("terminal-status")).toBeNull();
  });
});

describe("ReadBlock", () => {
  it("keeps the FILE's line numbers in the gutter, not the window's", () => {
    render(
      <ReadBlock
        path="harness/engine.py"
        totalLines={400}
        lines={[
          { number: 200, text: "def run():" },
          { number: 201, text: "    pass" },
        ]}
      />,
    );
    expect(screen.getByText("200")).toBeDefined();
    expect(screen.getByText("201")).toBeDefined();
    // A window is worth saying out loud: a reader who does not notice one believes they
    // have seen the whole file.
    expect(screen.getByTestId("read-window").textContent).toContain("2 / 400");
  });

  it("says nothing about a window when the window is the whole file", () => {
    render(
      <ReadBlock path="a.txt" totalLines={1} lines={[{ number: 1, text: "only" }]} />,
    );
    expect(screen.queryByTestId("read-window")).toBeNull();
  });

  it("labels an empty file instead of drawing an empty body", () => {
    render(<ReadBlock path="empty.txt" totalLines={0} lines={[]} />);
    expect(screen.getByText("Empty file")).toBeDefined();
  });
});

describe("SearchBlock", () => {
  const MATCHES: SearchResultCard = {
    card: "search",
    shape: "matches",
    files: [
      {
        path: "src/a.ts",
        matches: [
          { line_number: 12, line: "const x = 1" },
          { line_number: 40, line: "const y = 2" },
        ],
      },
      { path: "src/b.ts", matches: [{ line_number: 3, line: "const z = 3" }] },
    ],
    truncated: false,
    total: 3,
  };

  it("groups hits under their file and says how many", () => {
    render(<SearchBlock card={MATCHES} />);
    expect(screen.getByText("src/a.ts")).toBeDefined();
    expect(screen.getByText("2 matches")).toBeDefined();
    expect(screen.getByText("3 matches · 2 files")).toBeDefined();
    expect(screen.getByText("12")).toBeDefined();
  });

  it("folds a file group", () => {
    render(<SearchBlock card={MATCHES} />);
    fireEvent.click(screen.getByText("src/a.ts"));
    // The header survives the fold; its hits do not.
    expect(screen.getByText("src/a.ts")).toBeDefined();
    expect(screen.queryByText("12")).toBeNull();
  });

  it("says 'showing N of M' when the result was capped", () => {
    render(
      <SearchBlock
        card={{ ...MATCHES, truncated: true, total: 412 }}
        recovery="Full results stored at: .reeve/output/grep-1.log"
      />,
    );
    expect(screen.getByText("showing 3 of 412 · 2 files")).toBeDefined();
    expect(screen.getByTestId("search-recovery").textContent).toContain("grep-1.log");
  });

  it("hides the recovery line when nothing was capped", () => {
    render(<SearchBlock card={MATCHES} recovery="ignored" />);
    expect(screen.queryByTestId("search-recovery")).toBeNull();
  });

  it("lists paths for a glob-shaped result", () => {
    render(
      <SearchBlock
        card={{
          card: "search",
          shape: "paths",
          paths: ["a.ts", "b.ts"],
          truncated: false,
          total: 2,
        }}
      />,
    );
    expect(screen.getByText("2 paths")).toBeDefined();
    expect(screen.getByText("a.ts")).toBeDefined();
  });
});

describe("WebBlock", () => {
  it("links only http(s) and renders anything else as text", () => {
    render(
      <WebBlock
        card={{
          card: "web",
          kind: "search",
          truncated: false,
          sources: [
            { url: "https://example.com/a", title: "Example" },
            // A scheme this app will not hand to the OS: shown, not linked.
            { url: "javascript:alert(1)", title: "Nope" },
          ],
        }}
      />,
    );
    const linked = screen.getByText("Example");
    expect(linked.tagName).toBe("A");
    expect(linked.getAttribute("href")).toBe("https://example.com/a");
    expect(screen.getByText("Nope").tagName).not.toBe("A");
  });

  it("numbers citations from one, in order", () => {
    render(
      <WebBlock
        card={{
          card: "web",
          kind: "search",
          truncated: false,
          answer: "The answer.",
          sources: [{ url: "https://a.example/x" }, { url: "https://b.example/y" }],
        }}
      />,
    );
    const items = screen.getByTestId("web-sources").querySelectorAll("li");
    expect(items).toHaveLength(2);
    expect(items[0].getAttribute("value")).toBe("1");
    expect(items[1].getAttribute("value")).toBe("2");
  });

  it("draws a kept site icon from this computer, and a letter where none was kept", async () => {
    const asked: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        asked.push(String(url));
        return { ok: true, blob: async () => new Blob(["png"], { type: "image/png" }) } as Response;
      }),
    );
    vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: () => "blob:icon" }));
    const { container } = render(
      <WebBlock
        card={{
          card: "web",
          kind: "search",
          truncated: false,
          sources: [
            { url: "https://www.kept-icon.example/a", title: "Kept", icon: true },
            { url: "https://letter.example/b", title: "Letter" },
          ],
        }}
      />,
    );
    await waitFor(() => expect(container.querySelector("img.web-fav-img")?.getAttribute("src")).toBe("blob:icon"));
    // Only the local server is asked, by host; the site itself never is.
    expect(asked).toHaveLength(1);
    expect(asked[0]).toMatch(/\/v1\/favicons\/kept-icon\.example$/);
    expect(screen.getByText("L")).toBeTruthy(); // the row without a kept icon keeps its letter
    vi.unstubAllGlobals();
  });

  it("shows the provider and highlights the query terms in a snippet", () => {
    render(
      <WebBlock
        card={{
          card: "web",
          kind: "search",
          truncated: false,
          provider: "tavily",
          query: "python pathlib",
          sources: [
            {
              url: "https://docs.python.org/3/library/pathlib.html",
              title: "pathlib",
              snippet: "The pathlib module offers classes.",
            },
          ],
        }}
      />,
    );
    expect(screen.getByTestId("web-provider").textContent).toBe("Tavily");
    const marks = screen.getByTestId("web-sources").querySelectorAll("mark");
    expect(marks).toHaveLength(1);
    expect(marks[0].textContent?.toLowerCase()).toBe("pathlib");
  });

  it("marks whole words of the query only, never a piece of another word", () => {
    // Real result, 2026-10-05: "in" lit up inside "window", "working" and "includes".
    render(
      <WebBlock
        card={{
          card: "web",
          kind: "search",
          truncated: false,
          query: "context compaction in coding agents",
          sources: [
            {
              url: "https://example.test/a",
              snippet: "Context fills the window while working; compaction includes coding agents.",
            },
          ],
        }}
      />,
    );
    const marks = [...screen.getByTestId("web-sources").querySelectorAll("mark")].map((m) => m.textContent);
    expect(marks).toEqual(["Context", "compaction", "coding", "agents"]);
  });

  it("shows when a page was published and who wrote it", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(2026, 9, 5, 15, 0));
    try {
      render(
        <WebBlock
          card={{
            card: "web",
            kind: "search",
            truncated: false,
            sources: [
              { url: "https://news.example/a", title: "A", published_at: "2026-10-04T10:00:00.000Z", author: "A. Writer" },
              { url: "https://www.docs.example/b", title: "B", published_at: "2026-07-29T16:00:00.000Z" },
              { url: "https://c.example/c", title: "C", published_at: "not a date" },
            ],
          }}
        />,
      );
      const rows = screen.getAllByTestId("web-result");
      expect(rows[0].querySelector("time")?.textContent).toBe("yesterday");
      expect(rows[0].textContent).toContain("news.example");
      expect(rows[0].textContent).toContain("A. Writer");
      expect(rows[1].querySelector("time")?.textContent).toBe("29 Jul");
      // `www.` is the site's furniture, not its name.
      expect(rows[1].textContent).toContain("docs.example");
      expect(rows[1].textContent).not.toContain("www.");
      expect(rows[2].querySelector("time")).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it("shows two lines of a long snippet, and the rest when the snippet is pressed", () => {
    const long = "word ".repeat(80).trim();
    render(
      <WebBlock
        card={{
          card: "web",
          kind: "search",
          truncated: false,
          sources: [
            { url: "https://a.example/", title: "Long", snippet: long },
            { url: "https://b.example/", title: "Short", snippet: "Short and whole." },
          ],
        }}
      />,
    );
    const [first, second] = screen.getAllByTestId("web-snippet");
    expect(first.tagName).toBe("BUTTON");
    expect(first.hasAttribute("data-clamped")).toBe(true);
    fireEvent.click(first);
    expect(first.hasAttribute("data-clamped")).toBe(false);
    expect(first.getAttribute("aria-expanded")).toBe("true");
    // A snippet that fits is text, not a control that does nothing.
    expect(second.tagName).toBe("P");
  });

  it("says when the expected service did not answer, and what was merged or removed", () => {
    render(
      <WebBlock
        card={{
          card: "web",
          kind: "search",
          truncated: false,
          provider: "duckduckgo",
          fallback_from: "exa",
          fallback_reason: "exa mcp: rate limited",
          queries: ["first angle", "second angle"],
          filtered_out: 3,
          failed_queries: 1,
          sources: [{ url: "https://a.example/", title: "A" }],
        }}
      />,
    );
    const provider = screen.getByTestId("web-provider");
    expect(provider.textContent).toBe("DuckDuckGo");
    expect(provider.hasAttribute("data-fallback")).toBe(true);
    expect(provider.getAttribute("title")).toContain("rate limited");
    expect(screen.getByTestId("web-fallback").textContent).toBe("Exa unavailable");
    expect(screen.getByTestId("web-queries").textContent).toBe("first anglesecond angle");
    expect(screen.getByTestId("web-notes").textContent).toBe(
      "3 removed by the domain or date filters · 1 query failed",
    );
  });

  it("says nothing extra about a search that went as planned", () => {
    render(
      <WebBlock
        card={{
          card: "web",
          kind: "search",
          truncated: false,
          provider: "exa",
          sources: [{ url: "https://a.example/", title: "A" }],
        }}
      />,
    );
    expect(screen.getByTestId("web-provider").hasAttribute("data-fallback")).toBe(false);
    expect(screen.queryByTestId("web-fallback")).toBeNull();
    expect(screen.queryByTestId("web-notes")).toBeNull();
  });

  // The fetch card has its own file (FetchCard.test.tsx); this is only the dispatch.
  it("hands a fetch to the fetch card", () => {
    const { container } = render(<WebBlock card={{ card: "web", kind: "fetch", url: "https://example.com/p" }} />);
    expect(container.querySelector("[data-web='fetch']")).not.toBeNull();
  });
});

describe("CodeBlock", () => {
  it("renders plain text before a grammar has loaded, and its label", () => {
    render(<CodeBlock code={"print(1)\nprint(2)"} label="Program" />);
    expect(screen.getByText("Program")).toBeDefined();
    expect(screen.getByText("print(1)")).toBeDefined();
  });

  it("drops one trailing newline so a block has no blank last row", () => {
    const { container } = render(<CodeBlock code={"only\n"} />);
    const rows = container.querySelectorAll(".rv-term-line");
    expect(rows).toHaveLength(1);
  });
});
