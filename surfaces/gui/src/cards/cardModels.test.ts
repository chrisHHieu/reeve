import { describe, expect, it } from "vitest";
import {
  classifyTool,
  diffCardModel,
  genericCallInput,
  readCardModel,
  searchCardModel,
  terminalCardModel,
  terminalDescription,
  terminalFailed,
  toolRowModel,
  webCardModel,
  type ToolItem,
} from "./cardModels";
import type { Presented } from "./types";

/**
 * A presentation wrapper whose card parameter is `unknown`.
 *
 * Deliberate: half of what this file tests is that the narrowing REFUSES a shape the wire can
 * genuinely deliver — a line number that is a string, a hunk whose `new_text` is a number —
 * and no well-typed literal can express those. Passing them through `unknown` keeps the test
 * honest about what it is feeding in.
 */
const presented = (card: unknown): Presented => ({ summary: "done", card: card as never });

const tool = (extra: Partial<ToolItem> = {}): ToolItem =>
  ({
    kind: "tool",
    id: "t1",
    name: "run_shell",
    args: { command: "ls" },
    status: "ok",
    ...extra,
  }) as ToolItem;

describe("classifyTool", () => {
  it("maps a wire name to its family, aliases included", () => {
    expect(classifyTool("run_shell")).toBe("terminal");
    expect(classifyTool("shell_task_output")).toBe("terminal");
    expect(classifyTool("read_image")).toBe("read");
    expect(classifyTool("apply_unified_diff")).toBe("edit");
    expect(classifyTool("list_files")).toBe("search");
    expect(classifyTool("todo_write")).toBe("plan");
  });

  it("catches a family by PREFIX, so a new verb is covered the day it appears", () => {
    expect(classifyTool("terminal_send")).toBe("terminal");
    expect(classifyTool("terminal_whatever_comes_next")).toBe("terminal");
  });

  it("answers `other` for a name it has never seen", () => {
    expect(classifyTool("mcp__wealthlog__query")).toBe("other");
  });
});

describe("the row model", () => {
  it("prefers the tool's account of the result over anything derived", () => {
    const model = toolRowModel(tool({ presentation: presented({ card: "generic" }) }));
    expect(model.summary).toBe("done");
  });

  it("prefers the CALL's account while the call is still in flight", () => {
    const model = toolRowModel(
      tool({
        status: "…",
        presentation: presented({ card: "generic" }),
        callPresentation: { summary: "Run the tests" },
      }),
    );
    expect(model.summary).toBe("Run the tests");
  });

  it("falls back to Reeve's prose, then to the arguments", () => {
    expect(toolRowModel(tool({ name: "read_file", args: { path: "a/b.py" } })).summary).toBe(
      "Read b.py",
    );
    expect(
      toolRowModel(tool({ name: "mystery", args: { thing: "a value" } })).summary,
    ).toContain("a value");
  });

  it("gives an error row the failure's own first line", () => {
    const model = toolRowModel(
      tool({ status: "error", preview: '{"error":"boom\\nsecond line"}' }),
    );
    expect(model.state).toBe("error");
    // Not `{` — the first line of a pretty-printed result dict, which is the one thing
    // nobody needs telling about a failure.
    expect(model.errorSummary).toBe("boom");
  });

  it("keeps saying what a failed call asked for, not the failure twice", () => {
    // grep's presenter returns the error as its summary; the row draws the error on its own
    // line, so the summary must stay the search (owner's screenshot, 2026-10-15).
    const model = toolRowModel(
      tool({
        name: "grep",
        args: { pattern: "OAuth", path: "a|b" },
        status: "error",
        preview: '{"error":"grep failed: rg: a|b: IO error"}',
        presentation: { summary: "grep failed: rg: a|b: IO error" },
      } as Partial<ToolItem>),
    );
    expect(model.errorSummary).toBe("grep failed: rg: a|b: IO error");
    expect(model.summary).not.toContain("grep failed");
    expect(model.summary).toContain("OAuth");
  });

  it("reads the output out of a result dict for the OUT section", () => {
    const model = toolRowModel(tool({ preview: '{"exit_code":0,"output":"3 passed"}' }));
    expect(model.output).toBe("3 passed");
  });

  it("treats an interruption and a missing result as stopped, not failed", () => {
    // Neither is a failure: nothing said the tool broke. The reference design draws an interruption amber for
    // the same reason.
    expect(toolRowModel(tool({ status: "interrupted" })).state).toBe("stopped");
    expect(toolRowModel(tool({ status: "no result" })).state).toBe("stopped");
    expect(toolRowModel(tool({ status: "denied" })).state).toBe("error");
  });

  it("drops the home prefix from a path it shows, whichever separator arrives", () => {
    // The two sides do not share a dialect: a Windows tool result writes backslashes while
    // the home directory may be handed over with either.
    const model = toolRowModel(
      tool({ name: "read_file", args: { path: "C:\\ws\\src\\a.py" } }),
      "C:\\ws",
    );
    expect(model.summary).not.toContain("C:");
    expect(model.summary).toContain("src/a.py");
  });
});

describe("the terminal card", () => {
  it("is result-side once the call has settled", () => {
    const model = terminalCardModel(
      tool({
        presentation: presented({
          card: "terminal",
          title: "ls -la",
          output: "x\n",
          exit_code: 0,
        }),
      }),
    );
    expect(model).toEqual({
      command: "ls -la",
      cwd: undefined,
      output: "x\n",
      exitCode: 0,
      signal: undefined,
      truncated: false,
    });
  });

  it("is call-side while the call is still running", () => {
    const model = terminalCardModel(
      tool({
        status: "…",
        callPresentation: presented({ card: "terminal", title: "pytest", cwd: "/ws" }),
      }),
    );
    expect(model).toEqual({ command: "pytest", cwd: "/ws", running: true });
  });

  it("draws nothing while running with no call card — the row keeps its sentence", () => {
    expect(terminalCardModel(tool({ status: "…" }))).toBeNull();
  });

  it("draws nothing when the result card is a generic one", () => {
    // A background acknowledgement or a start failure. A terminal card promising a terminal
    // for something that returned a task id is a lie the row would have to un-tell.
    expect(terminalCardModel(tool({ presentation: presented({ card: "generic" }) }))).toBeNull();
  });

  it("draws nothing when neither side names a command", () => {
    // A result card may defer its title to the pending one; with no pending card either,
    // there is no prompt line to head and so no terminal to draw.
    expect(
      terminalCardModel(
        tool({ presentation: presented({ card: "terminal", output: "x", exit_code: 0 }) }),
      ),
    ).toBeNull();
  });

  it("reports a failure the CALL did not", () => {
    expect(terminalFailed({ command: "x", exitCode: 2 })).toBe(true);
    expect(terminalFailed({ command: "x", signal: "SIGTERM" })).toBe(true);
    expect(terminalFailed({ command: "x", exitCode: 0 })).toBe(false);
    // Still running: there is no verdict yet, so there is nothing to fail.
    expect(terminalFailed({ command: "x", running: true, exitCode: 2 })).toBe(false);
  });

  it("reads the description off the CALL card", () => {
    expect(
      terminalDescription(
        tool({
          callPresentation: presented({ card: "terminal", title: "ls", description: "List files" }),
        }),
      ),
    ).toBe("List files");
    expect(terminalDescription(tool())).toBeNull();
  });
});

describe("narrowing: every model answers null on ANY doubt", () => {
  // The card arrived over a wire from a host that may be newer than this build. The contract
  // is that an unrecognised shape costs the card and nothing else.

  it("a malformed hunk costs the diff, not the row", () => {
    expect(diffCardModel(tool({ presentation: presented({ card: "diff", diffs: [] }) }))).toBeNull();
    expect(
      diffCardModel(
        tool({ presentation: presented({ card: "diff", diffs: [{ path: "a", new_text: 7 }] }) }),
      ),
    ).toBeNull();
    expect(
      diffCardModel(
        tool({ presentation: presented({ card: "diff", diffs: [{ path: "  ", new_text: "x" }] }) }),
      ),
    ).toBeNull();
  });

  it("keeps the engine's counts when they are there, and omits them when they are not", () => {
    const model = diffCardModel(
      tool({
        presentation: presented({
          card: "diff",
          diffs: [{ path: "a.py", old_text: "x", new_text: "y", added: 1, removed: 1 }],
        }),
      }),
    );
    expect(model!.diffs[0].added).toBe(1);
    expect(model!.path).toBe("a.py");
    const bare = diffCardModel(
      tool({
        presentation: presented({
          card: "diff",
          diffs: [{ path: "a.py", old_text: null, new_text: "y" }],
        }),
      }),
    );
    expect(bare!.diffs[0].added).toBeUndefined();
  });

  it("a read window with a non-numeric line number costs the read", () => {
    expect(
      readCardModel(
        tool({
          presentation: presented({
            card: "read",
            path: "a.py",
            offset: 1,
            total_lines: 2,
            lines: [{ number: "1", text: "x" }],
          }),
        }),
      ),
    ).toBeNull();
  });

  it("does not read a running read as a card — nothing has been read yet", () => {
    expect(
      readCardModel(
        tool({
          status: "…",
          presentation: presented({
            card: "read",
            path: "a.py",
            offset: 1,
            total_lines: 1,
            lines: [],
          }),
        }),
      ),
    ).toBeNull();
  });

  it("an unknown search shape costs the search, and never reads as paths", () => {
    expect(
      searchCardModel(tool({ presentation: presented({ card: "search", shape: "tree", total: 1 }) })),
    ).toBeNull();
  });

  it("an unknown web kind costs the web card, and never reads as a fetch", () => {
    // Reading it as a fetch would draw an empty URL and "HTTP undefined" beside it.
    expect(
      webCardModel(tool({ presentation: presented({ card: "web", kind: "crawl", url: "" }) })),
    ).toBeNull();
  });

  it("a web search keeps each source's date and byline, and what the head says", () => {
    // Both were sent and dropped here, so no card ever showed a date (found 2026-10-05).
    const card = webCardModel(
      tool({
        presentation: presented({
          card: "web",
          kind: "search",
          truncated: false,
          provider: "duckduckgo",
          fallback_from: "exa",
          fallback_reason: "rate limited",
          queries: ["a", "b"],
          filtered_out: 2,
          failed_queries: 1,
          sources: [
            { url: "https://a.example/", title: "A", published_at: "2026-10-04T10:00:00.000Z", author: "W", icon: true },
            { url: "https://b.example/", title: "B", icon: "yes" },
          ],
        }),
      }),
    );
    expect(card?.kind === "search" && card.sources[0]).toEqual({
      url: "https://a.example/",
      title: "A",
      published_at: "2026-10-04T10:00:00.000Z",
      author: "W",
      icon: true, // the kept site icon reaches the card (it was dropped here, 2026-10-11)
    });
    expect(card?.kind === "search" && card.sources[1]).toEqual({ url: "https://b.example/", title: "B" });
    expect(card?.kind === "search" && card).toMatchObject({
      fallback_from: "exa",
      fallback_reason: "rate limited",
      queries: ["a", "b"],
      filtered_out: 2,
      failed_queries: 1,
    });
  });

  it("a fetch keeps how it read the page, and a malformed part costs that part only", () => {
    const fetched = (extra: Record<string, unknown>) =>
      webCardModel(tool({ presentation: presented({ card: "web", kind: "fetch", url: "https://a.example/p", ...extra }) }));
    const card = fetched({
      mode: "answer",
      excerpt_kind: "markdown",
      read: { page_chars: 9000, start: "0" },
      about: { left_out_chars: 1900, plain_http: true, js_required: "yes" },
      answer: {
        question: "Why?",
        text: "Because.",
        quotes: [{ quote: "q", found: true }, { quote: 7, found: true }, "loose"],
        quotes_total: 3,
        quotes_found: 1,
      },
      found: { phrase: "x", count: 2, matches: [{ at: 4, text: "a x" }, { at: "4", text: "b" }] },
      redirect: { host: "b.example", url: 5 },
    });
    expect(card).toMatchObject({
      mode: "answer",
      excerpt_kind: "markdown",
      read: { page_chars: 9000 },
      about: { left_out_chars: 1900, plain_http: true },
      answer: { question: "Why?", text: "Because.", quotes: [{ quote: "q", found: true }], quotes_total: 3, quotes_found: 1 },
      found: { phrase: "x", count: 2, matches: [{ at: 4, text: "a x" }] },
      redirect: { host: "b.example", url: undefined },
    });
    expect(card?.kind === "fetch" && card.about).not.toHaveProperty("js_required");
    expect(card?.kind === "fetch" && card.read).not.toHaveProperty("start");
    const odd = fetched({ mode: "crawl", excerpt_kind: "html", answer: { quotes: [] }, found: { count: 1 } });
    expect(odd).toMatchObject({ kind: "fetch", url: "https://a.example/p" });
    expect(odd?.kind === "fetch" && [odd.mode, odd.excerpt_kind, odd.answer, odd.found]).toEqual([
      undefined, undefined, undefined, undefined,
    ]);
  });

  it("a card of a kind this build has never heard of costs the card", () => {
    expect(webCardModel(tool({ presentation: presented({ card: "hologram" }) }))).toBeNull();
  });

  it("a search with no total costs the search", () => {
    expect(
      searchCardModel(
        tool({
          presentation: presented({
            card: "search",
            shape: "paths",
            paths: ["a.py"],
            truncated: false,
          }),
        }),
      ),
    ).toBeNull();
  });

  it("an unknown language still yields a read card — highlighting is not required", () => {
    const model = readCardModel(
      tool({
        presentation: presented({
          card: "read",
          path: "notes.unknown",
          offset: 1,
          total_lines: 1,
          lines: [{ number: 1, text: "x" }],
        }),
      }),
    );
    expect(model).not.toBeNull();
    expect(model!.lang).toBeUndefined();
  });

  it("infers the language from the path when the card does not name one", () => {
    const model = readCardModel(
      tool({
        presentation: presented({
          card: "read",
          path: "src/a.py",
          offset: 1,
          total_lines: 1,
          lines: [{ number: 1, text: "x" }],
        }),
      }),
    );
    expect(model!.lang).toBe("python");
  });
});

describe("generic cards", () => {
  it("hands over a program from the call card, which is all a running code row has", () => {
    expect(
      genericCallInput(
        tool({
          status: "…",
          name: "run_code",
          callPresentation: presented({ card: "generic", title: "Count", raw_input: "print(1)" }),
        }),
      ),
    ).toBe("print(1)");
  });

  it("does not mistake a number for a program", () => {
    expect(
      genericCallInput(
        tool({ callPresentation: presented({ card: "generic", title: "t", raw_input: 42 }) }),
      ),
    ).toBeNull();
  });
});
