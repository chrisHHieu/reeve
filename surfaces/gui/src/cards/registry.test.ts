import { describe, expect, it } from "vitest";
import { rowSpec } from "./registry";
import type { ToolItem } from "./cardModels";
import type { Presented } from "./types";

const presented = (card: unknown): Presented => ({ summary: "done", card: card as never });

const tool = (extra: Partial<ToolItem> = {}): ToolItem =>
  ({ kind: "tool", id: "t1", name: "run_code", args: {}, status: "ok", ...extra }) as ToolItem;

describe("run_code row", () => {
  it("shows the program as a code block when the call card carries it", () => {
    const spec = rowSpec(
      tool({
        callPresentation: presented({ card: "generic", title: "Count", raw_input: "print(1)" }),
      }),
    );
    expect(spec.code?.text).toBe("print(1)");
  });

  it("still shows the program from args on a replay with no call card", () => {
    // The bug: a restored call has no generic call card, so the row fell to the generic IN
    // dump and printed the program as wrapped text with no line numbers.
    const spec = rowSpec(tool({ args: { code: "print(2)", description: "x" } }));
    expect(spec.code?.text).toBe("print(2)");
  });

  it("renders no code block for a non-code tool", () => {
    expect(rowSpec(tool({ name: "read_file", args: { path: "a.py" } })).code).toBeUndefined();
  });
});

describe("load_skill row", () => {
  it("carries the skill card so the body renders instead of a JSON dump", () => {
    const spec = rowSpec(
      tool({
        name: "load_skill",
        args: { name: "pdf" },
        presentation: presented({
          card: "skill",
          name: "pdf",
          description: "extract text",
          instructions: "Use pdfplumber.",
        }),
      }),
    );
    expect(spec.icon).toBe("book");
    expect(spec.skill?.name).toBe("pdf");
    expect(spec.skill?.instructions).toContain("pdfplumber");
  });
});

describe("propose_plan row", () => {
  it("carries the plan markdown as a step card inside the turn", () => {
    const spec = rowSpec(
      tool({ name: "propose_plan", args: { plan: "# Plan\n\nPhase 1." }, status: "…" }),
    );
    expect(spec.icon).toBe("checklist");
    expect(spec.plan?.text).toContain("Phase 1.");
    // It is a step, not a card that replaces the row's chrome.
    expect(spec.title).toBeTruthy();
  });

  it("never says Approved when full access asked nobody", () => {
    const row = (preview: string) =>
      rowSpec(tool({ name: "propose_plan", args: { plan: "1. a\n2. b" }, preview } as Partial<ToolItem>));
    // Today's shape: shown, not approved, nobody asked.
    const unasked = row('{"not_asked": "full access", "note": "Do what the person asked"}');
    expect(unasked.title).toBe("Planned");
    expect(unasked.fields?.map((f) => f.value)).toEqual(["Not asked · Full access"]);
    // A transcript from before 2026-10-18 said it as an approval; it still reads as not asked.
    const legacy = row('{"approved": true, "approved_by": "full access", "mode": "danger-full-access"}');
    expect(legacy.title).toBe("Planned");
    expect(legacy.fields?.map((f) => f.value)).toEqual(["Not asked · Full access"]);
    expect(row('{"approved": true, "mode": "danger-full-access"}').title).toBe("Plan approved");
  });
});

describe("row title follows the call's state, like Thinking → Thought", () => {
  const edit = (status: string) =>
    rowSpec(tool({ name: "replace_in_file", args: { path: "a.py" }, status: status as never }));

  it("says Editing while it runs and Edited once it has", () => {
    expect(edit("…").title).toBe("Editing");
    expect(edit("ok").title).toBe("Edited");
  });

  it("keeps the noun when the call failed, so the row never claims it happened", () => {
    expect(edit("error").title).toBe("Edit");
  });

  it("reads the same way for search, fetch and an MCP call", () => {
    expect(rowSpec(tool({ name: "grep", status: "…" })).title).toBe("Searching");
    expect(rowSpec(tool({ name: "grep", status: "ok" })).title).toBe("Searched");
    expect(rowSpec(tool({ name: "web_fetch", status: "ok" })).title).toBe("Fetched");
    expect(rowSpec(tool({ name: "mcp__wealthlog__q", status: "ok" })).title).toBe("Wealthlog");
  });
});

describe("a web search row", () => {
  const searched = (card: Record<string, unknown>, args: Record<string, unknown>) =>
    rowSpec(
      tool({
        name: "web_search",
        status: "ok",
        args,
        presentation: { summary: "Found 2 web results", kind: "search", card } as unknown as Presented,
      }),
    );
  const card = (extra: Record<string, unknown>) => ({
    card: "web",
    kind: "search",
    truncated: false,
    sources: [{ url: "https://a.example/" }, { url: "https://b.example/" }],
    ...extra,
  });

  it("names the query it ran and how many sources came back", () => {
    const spec = searched(card({ query: "widget budget" }), { query: "widget budget" });
    expect(spec.summary).toBe("“widget budget”");
    expect(spec.summarySuffix).toBe("2 sources");
  });

  it("names the first of several merged searches, and how many rode with it", () => {
    const spec = searched(card({ queries: ["widget compaction", "widget budget", "third"] }), {
      queries: ["widget compaction", "widget budget", "third"],
    });
    expect(spec.summary).toBe("“widget compaction” +2");
  });
});

describe("a web fetch row", () => {
  it("names the host, then what came of the fetch", () => {
    const fetched = (extra: Record<string, unknown>) =>
      rowSpec(
        tool({
          name: "web_fetch",
          status: "ok",
          presentation: presented({ card: "web", kind: "fetch", url: "https://www.docs.example/a", ...extra }),
        }),
      );
    const find = fetched({ mode: "find", found: { phrase: "x", count: 18, matches: [] } });
    expect([find.summary, find.summarySuffix]).toEqual(["docs.example", "18 matches"]);
    expect(fetched({ mode: "page", read: { start: 0, end: 20000, page_chars: 54000 } }).summarySuffix).toBe("20k of 54k");
    expect(fetched({ status_code: 404 }).summarySuffix).toBe("HTTP 404");
  });
});

describe("show_visual row", () => {
  const chart = { type: "chart", kind: "bar", labels: ["a", "b"], series: [{ name: "n", values: [1, 2] }] };

  it("draws the harness's checked blocks once the call has run, not the arguments it was given", () => {
    const spec = rowSpec(
      tool({
        name: "show_visual",
        args: { blocks: [{ type: "chart", source: { file: "q3.csv", x: "cat" } }] },
        presentation: presented({ card: "visual", blocks: [chart] }),
      }),
    );
    expect(spec.visual).toHaveLength(1);
    expect(spec.visual?.[0].type).toBe("chart");
  });

  it("draws the arguments while the call runs, and a refused call keeps its plain row", () => {
    expect(rowSpec(tool({ name: "show_visual", status: "…", args: { blocks: [chart] } })).visual?.[0].type).toBe("chart");
    const refused = rowSpec(tool({ name: "show_visual", status: "error", args: { blocks: [chart] } }));
    expect(refused.visual).toBeUndefined();
  });
});
