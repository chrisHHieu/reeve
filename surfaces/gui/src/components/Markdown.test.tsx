import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { Markdown, OPEN_ARTIFACT_EVENT, REVEAL_ARTIFACT_EVENT, fileTypeLabel, splitBlocks } from "./Markdown";
import { DeliveredFiles } from "./markdown/Deliverables";
import { ensureLanguage } from "./primitives/highlight";

const stopListening: (() => void)[] = [];
afterEach(() => {
  cleanup();
  while (stopListening.length) stopListening.pop()!();
});

/** The paths sent with each `event` from now until the test ends. */
function heard(event: string): string[] {
  const paths: string[] = [];
  const listener = (e: Event) => paths.push((e as CustomEvent).detail.path);
  window.addEventListener(event, listener);
  stopListening.push(() => window.removeEventListener(event, listener));
  return paths;
}

// A file the agent made is linked as [Title](artifact:path): in the sentence it is a mention
// that opens the viewer through a window event. Any other link opens outside the app.
describe("Markdown artifact links", () => {
  it("draws an artifact: link as a mention with the link's words, which opens its path", () => {
    const opened = heard(OPEN_ARTIFACT_EVENT);
    render(<Markdown text="Done — [Semiconductor dashboard](artifact:reports/semi.html)" />);
    const mention = screen.getByTestId("artifact-mention");
    // The file's own card is the one `present` declared, under the turn (the next test).
    expect(mention.textContent).toContain("Semiconductor dashboard");
    fireEvent.click(mention);
    expect(opened).toEqual(["reports/semi.html"]);
  });

  it("names the file when the link has no words", () => {
    render(<Markdown text="[](artifact:out/report.pdf)" />);
    expect(screen.getByTestId("artifact-mention").textContent).toContain("report.pdf");
  });

  it("leaves any other link a link out of the app", () => {
    const { container } = render(<Markdown text="see [the docs](https://example.com)" />);
    expect(screen.queryByTestId("artifact-mention")).toBeNull();
    const link = container.querySelector("a")!;
    expect(link.getAttribute("target")).toBe("_blank");
    // Where it goes, not how it is spelt: a transform that adds the root path goes to the same place.
    expect(new URL(link.getAttribute("href")!).origin).toBe("https://example.com");
  });

  it("draws a produced file as a row under the turn: the name, then folder / menu", () => {
    // The card moved to the turn tail (2026-10-03, the shape). Every action must do
    // something real: the folder button asks the session to reveal the file, the menu opens
    // it in its own app or reveals it, and the row itself previews it.
    const opened = heard(OPEN_ARTIFACT_EVENT);
    const revealed: { path: string; mode?: string }[] = [];
    const onReveal = (e: Event) => revealed.push((e as CustomEvent).detail);
    window.addEventListener(REVEAL_ARTIFACT_EVENT, onReveal);
    stopListening.push(() => window.removeEventListener(REVEAL_ARTIFACT_EVENT, onReveal));
    // The row is the `present` declaration's; the link in the sentence is only the mention.
    render(<DeliveredFiles files={[{ path: "notes/fast-api.md" }]} />);
    const card = screen.getByTestId("delivered-file-card");
    expect(card.textContent).toContain("fast-api.md");
    // The second line is the model's description when it wrote one, else the file's kind;
    // the folder is gone because the full path rides the title attribute.
    expect(card.textContent).toContain("Markdown");
    expect(card.querySelector('[title="notes/fast-api.md"]')).not.toBeNull();
    fireEvent.click(screen.getByTestId("delivered-file-reveal"));
    expect(revealed).toEqual([{ path: "notes/fast-api.md", mode: "reveal" }]);
    fireEvent.click(screen.getByTestId("delivered-file-open-main"));
    expect(opened).toEqual(["notes/fast-api.md"]);
  });

  it("names a file's kind in a non-developer's words", () => {
    expect(fileTypeLabel("report.md")).toBe("Markdown");
    expect(fileTypeLabel("deck.PPTX")).toBe("PowerPoint");
    expect(fileTypeLabel("data.parquet")).toBe("PARQUET");
    expect(fileTypeLabel("Makefile")).toBe("");
  });

  // The other half of the file mention (2026-10-03): the model writes the file's name in
  // backticks, and the turn's OWN files decide whether that token is a file. Without a
  // resolver the extension heuristic guesses; with one, `hello.md` opens `docs/hello.md`.
  it("resolves a backticked file name against the turn's files and opens the full path", () => {
    const seen = heard(OPEN_ARTIFACT_EVENT);
    render(
      <Markdown
        text="Đã tạo xong file `hello.md` trong workspace."
        fileMentions={(token) => (token === "hello.md" ? "docs/hello.md" : undefined)}
      />,
    );
    const chip = screen.getByTitle("Open docs/hello.md");
    expect(chip.textContent).toContain("hello.md");
    expect(chip.getAttribute("aria-label")).toBe("Open docs/hello.md in sidebar");
    fireEvent.click(chip);
    expect(seen).toEqual(["docs/hello.md"]);
  });

  it("leaves a token the turn produced nothing for as ordinary code", () => {
    render(<Markdown text="The `file_path` argument." fileMentions={() => undefined} />);
    expect(screen.queryByTitle(/^Open file_path/)).toBeNull();
    expect(screen.getByText("file_path").closest("code")).not.toBeNull();
  });
});

// The bug that started this (2026-09-08): Gemini writes `$\rightarrow$` and the transcript
// printed the TeX source, because nothing in the pipeline knew `$` meant math.
//
// Two things these tests have to get right, both learned the hard way:
//
//  - `String.raw`. In a JS expression `"\("` is an unknown escape and collapses to `(`, so a
//    test written without it renders no math and fails for a reason that has nothing to do
//    with the renderer.
//  - Assert on `.katex-html`, not `textContent`. KaTeX deliberately repeats the original TeX
//    inside a MathML `<annotation>` so screen readers can reach it, so the source IS present
//    in `textContent` of a perfectly rendered formula. Asserting its absence there says
//    nothing about what the eye sees.
const visibleMath = (container: HTMLElement) =>
  container.querySelector(".katex-html")?.textContent ?? "";

describe("Markdown math", () => {
  it("renders inline TeX instead of printing its source", () => {
    const { container } = render(<Markdown text={String.raw`Timeline $\rightarrow$ Stack`} />);
    expect(container.querySelector(".katex")).not.toBeNull();
    expect(visibleMath(container)).toContain("→"); // the arrow, not its macro
    expect(visibleMath(container)).not.toContain(String.raw`\rightarrow`);
    expect(container.textContent).toContain("Timeline");
  });

  it("renders the delimiters OpenAI emits, which remark-math does not know", () => {
    const { container } = render(<Markdown text={String.raw`area is \(\pi r^2\)`} />);
    expect(container.querySelector(".katex")).not.toBeNull();
    expect(visibleMath(container)).toContain("π");
    expect(visibleMath(container)).not.toContain(String.raw`\pi`);
  });

  it("renders a display block written as \\[ ... \\]", () => {
    const { container } = render(<Markdown text={String.raw`\[\sum_{i=1}^{n} x_i\]`} />);
    expect(container.querySelector(".katex")).not.toBeNull();
    expect(visibleMath(container)).toContain("∑");
    expect(visibleMath(container)).not.toContain(String.raw`\sum`);
  });

  it("leaves money as money", () => {
    const { container } = render(<Markdown text="Phí là $5 và gói kia $10 mỗi tháng." />);
    expect(container.querySelector(".katex")).toBeNull();
    expect(container.textContent).toContain("$5");
    expect(container.textContent).toContain("$10");
    // and the escaping must not leak onto the screen
    expect(container.textContent).not.toContain(String.raw`\$`);
  });

  it("still renders a formula that opens on a digit", () => {
    // Straight out of the real session store, and the reason the currency rule needs its
    // exemption: an agent answered a sum with `$1 + 2 + … = \mathbf{78}$`.
    const { container } = render(
      <Markdown text={String.raw`Kết quả: $1 + 2 + 3 = \mathbf{6}$ thôi.`} />,
    );
    expect(container.querySelector(".katex")).not.toBeNull();
    expect(visibleMath(container)).toContain("6");
    expect(visibleMath(container)).not.toContain(String.raw`\mathbf`);
    expect(container.textContent).toContain("Kết quả");
  });

  it("does not blank the message when the model invents a macro", () => {
    // rehype-katex renders once with throwOnError, then retries the failure with
    // `throwOnError: false, strict: 'ignore'`. KaTeX prints the unparseable source in red
    // inside ordinary `.katex` markup — the `katex-error` span only appears if that RETRY
    // also throws, which a bad macro does not trigger.
    const { container } = render(<Markdown text={String.raw`before $\notarealmacro{x}$ after`} />);
    expect(container.querySelector(".katex")).not.toBeNull();
    // the source is shown rather than swallowed, so the reader can see what went wrong
    expect(visibleMath(container)).toContain("notarealmacro");
    expect(container.textContent).toContain("before");
    expect(container.textContent).toContain("after");
  });

  it("leaves a shell dollar inside code alone", () => {
    const { container } = render(<Markdown text="run `echo $5` now" />);
    expect(container.querySelector("code")?.textContent).toBe("echo $5");
    expect(container.textContent).not.toContain(String.raw`\$`);
  });
});

/**
 * The fenced block: the chrome and token colours.
 *
 * The values behind the `var(--syntax-*)` references are pinned in
 * primitives/highlight.test.ts (both themes); here the DOM is what is checked — the
 * wrapper, the pre, the language class, the keyword span and the icon-only copy button.
 */
describe("Markdown fenced code", () => {
  it("renders the reference design's block chrome, token colours and copy affordance", async () => {
    await ensureLanguage("python");
    const { container } = render(<Markdown text={"```python\nreturn 1\n```"} />);
    const block = container.querySelector(".md-codeblock")!;
    expect(block).not.toBeNull();
    expect(block.querySelector("pre.md-codeblock-pre")).not.toBeNull();
    expect(block.querySelector("code.language-python")?.textContent).toBe("return 1");
    const keyword = [...block.querySelectorAll("span")].find(
      (span) => span.textContent === "return",
    );
    expect(keyword?.getAttribute("style")).toContain("var(--syntax-keyword)");
    const copy = block.querySelector('[data-testid="copy-button"]')!;
    expect(copy.getAttribute("aria-label")).toBe("Copy");
    // Icon-only, as upstream: no "Copy" text in the block's content.
    expect(copy.textContent).toBe("");
  });

  it("renders a language it cannot load as plain text rather than dropping it", () => {
    const { container } = render(<Markdown text={"```notalanguage\nhello\n```"} />);
    expect(container.querySelector(".md-codeblock code")?.textContent).toBe("hello");
  });

  it("repaints plain text once the grammar arrives", async () => {
    // The bug this guards: a memo on [code, lang] alone would cache the `undefined` from
    // before the import landed, re-render on arrival, and still paint plain text forever.
    const { container } = render(<Markdown text={"```go\nfunc main() {}\n```"} />);
    expect(container.querySelectorAll(".md-codeblock code span").length).toBe(0);
    await ensureLanguage("go");
    await waitFor(() =>
      expect(container.querySelectorAll(".md-codeblock code span").length).toBeGreaterThan(0),
    );
  });

  it("highlights any shiki language, not only the old hand-picked list", async () => {
    // The complaint: everything outside the old 38-language map rendered flat (svelte,
    // zig, haskell, elixir, …). Rust is the probe; `languageForPath`/manifest tests cover
    // the rest.
    await ensureLanguage("rust");
    const { container } = render(<Markdown text={"```rust\nfn main() {}\n```"} />);
    const block = container.querySelector(".md-codeblock")!;
    expect(block.querySelector("code.language-rust")?.textContent).toBe("fn main() {}");
    const fn = [...block.querySelectorAll("span")].find((span) => span.textContent === "fn");
    expect(fn?.getAttribute("style")).toContain("var(--syntax-keyword)");
  });

  it("tags a shell fence with the reference design's shell kind and leaves other fences untagged", () => {
    // Shell blocks are monochrome upstream (); the tag is what the
    // stylesheet keys on to flatten their tokens.
    const { container } = render(
      <Markdown text={"```bash\nls -la\n```\n\n```python\nx = 1\n```"} />,
    );
    expect(container.querySelector('.md-codeblock[data-code-kind="shell"]')?.getAttribute("data-language")).toBe("bash");
    const plain = [...container.querySelectorAll(".md-codeblock")].find((block) =>
      block.querySelector(".language-python"),
    );
    expect(plain?.getAttribute("data-code-kind")).toBeNull();
  });
});

/**
 * Inline code: a URL is a blue external link, a token that names a file THIS TURN produced
 * is a file mention, and everything else — including anything that merely LOOKS like a path —
 * is plain code. The last rule is the one worth stating twice: before 2026-10-03 an
 * extension-in-a-dictionary test made `shell.py`, `node_modules/d3` and every version string
 * clickable, so a transcript could not be read for facts (the renderer never guesses; the
 * file a model wants clickable, it links).
 */
describe("Markdown inline code", () => {
  it("renders plain inline code with no kind attribute", () => {
    const { container } = render(
      <Markdown text={"set `shell.py:53 _DEFAULT_TIMEOUT = 120.0` first"} />,
    );
    const code = container.querySelector(":not(pre) > code")!;
    expect(code.textContent).toBe("shell.py:53 _DEFAULT_TIMEOUT = 120.0");
    expect(code.getAttribute("data-inline-code-kind")).toBeNull();
  });

  it("leaves a path-shaped token the turn never produced as ordinary code", () => {
    const { container } = render(
      <Markdown
        text={"see `shell.py` under `packages/desktop-electron` and `Transcript.tsx:1019-1038`"}
        fileMentions={() => undefined}
      />,
    );
    expect(container.querySelectorAll("code")).toHaveLength(3);
    // No chip, no glyph, no button, nothing to click: the whole point of the change.
    expect(container.querySelectorAll('code[data-inline-code-kind="file"]')).toHaveLength(0);
    expect(container.querySelectorAll("button")).toHaveLength(0);
    expect(container.querySelector("code svg")).toBeNull();
  });

  it("renders a url inline code as an external link carrying the url kind", () => {
    const { container } = render(<Markdown text={"read `https://opencode.ai/docs` now"} />);
    const link = container.querySelector("a.external-link")!;
    expect(link).not.toBeNull();
    expect(link.getAttribute("target")).toBe("_blank");
    expect(link.getAttribute("href")).toBe("https://opencode.ai/docs");
    expect(link.querySelector('code[data-inline-code-kind="url"]')?.textContent).toBe(
      "https://opencode.ai/docs",
    );
  });

  it("leaves a dot-containing code expression as plain code", () => {
    const { container } = render(<Markdown text={"the `sync.data.session` map"} />);
    expect(container.querySelector("code")?.getAttribute("data-inline-code-kind")).toBeNull();
  });

  it("does not nest an anchor when a url is already inside a markdown link", () => {
    const { container } = render(
      <Markdown text={"[`https://opencode.ai/docs`](https://opencode.ai/docs)"} />,
    );
    expect(container.querySelectorAll("a")).toHaveLength(1);
    expect(container.querySelector('code[data-inline-code-kind="url"]')).not.toBeNull();
  });
});

/**
 * A link the model wrote to a LOCAL file (`[name](relative/path)`, the other file-link
 * route) is a file chip, and a link to anything else stays an anchor. Without the local
 * branch the anchor would carry a relative href to a browser tab that cannot resolve it —
 * and an absolute Windows path would be emptied by react-markdown's URL transform, so the
 * link text would render with no destination at all.
 */
describe("Markdown file links", () => {
  it.each([
    ["[files.py](harness/tools/files.py)", "harness/tools/files.py"],
    ["[files.py](harness/tools/files.py#L24)", "harness/tools/files.py"],
    ["[files.py](harness/tools/files.py#L24-L30)", "harness/tools/files.py"],
    ["[x.ts](C:/work/x.ts#L2)", "C:/work/x.ts"],
    ["[My Notes](docs/My%20Notes.md)", "docs/My Notes.md"],
    ["[x.ts](C:%5Cwork%5Cx.ts)", "C:\\work\\x.ts"],
  ])("renders %s as a file chip opening %s", (markdown, path) => {
    const seen: string[] = [];
    const listener = (e: Event) => seen.push((e as CustomEvent).detail.path);
    window.addEventListener(OPEN_ARTIFACT_EVENT, listener);
    try {
      const { container } = render(<Markdown text={markdown} />);
      expect(container.querySelector("a")).toBeNull();
      const chip = screen.getByTestId("file-link");
      expect(chip.getAttribute("title")).toBe(`Open ${path}`);
      fireEvent.click(chip);
      expect(seen).toEqual([path]);
    } finally {
      window.removeEventListener(OPEN_ARTIFACT_EVENT, listener);
    }
  });

  it.each([
    "[docs](https://example.com/a.md)",
    "[host](//example.com/file.ts)",
    "[share](%5C%5Cserver%5Cshare%5Cfile.ts)",
    "[run](javascript:alert(1))",
    "[raw](file.ts?raw=1)",
    "[anchor](#results)",
    "[bad](file%ZZ.ts)",
  ])("keeps %s an ordinary link, never a file chip", (markdown) => {
    render(<Markdown text={markdown} />);
    expect(screen.queryByTestId("file-link")).toBeNull();
  });

  it("keeps an empty-text file link readable by falling back to the file name", () => {
    render(<Markdown text="[](reports/q3.docx)" />);
    expect(screen.getByTestId("file-link").textContent).toContain("q3.docx");
  });
});

/**
 * The markdown chrome the references share: a scroll-wrapped table, a left-ruled quote,
 * GFM task-list checkboxes (which must replace the bullet, not sit beside it), and the
 * GFM rule that a lone `~` is a character rather than a strikethrough opener.
 */
describe("Markdown elements", () => {
  it("wraps a table for scrolling and keeps its structure", () => {
    const { container } = render(
      <Markdown text={"| Language | Year |\n| --- | --- |\n| Rust | 2010 |"} />,
    );
    const wrap = container.querySelector(".md-tablewrap");
    expect(wrap).not.toBeNull();
    expect(wrap!.querySelector("table")).not.toBeNull();
    expect(wrap!.querySelectorAll("th")).toHaveLength(2);
    expect(wrap!.querySelectorAll("tbody tr")).toHaveLength(1);
  });

  it("renders a blockquote as a quote, not a paragraph with a > in it", () => {
    const { container } = render(<Markdown text={"> Rewrites are cheaper than reads."} />);
    const quote = container.querySelector("blockquote");
    expect(quote?.textContent?.trim()).toBe("Rewrites are cheaper than reads.");
  });

  it("renders task-list checkboxes in a list with no bullet marker to pair with them", () => {
    const { container } = render(<Markdown text={"- [x] shipped\n- [ ] pending"} />);
    const boxes = container.querySelectorAll('li input[type="checkbox"]');
    expect(boxes).toHaveLength(2);
    expect((boxes[0] as HTMLInputElement).checked).toBe(true);
    expect((boxes[1] as HTMLInputElement).checked).toBe(false);
    expect(container.querySelectorAll("li")).toHaveLength(2);
  });

  it("keeps a lone tilde as a character while ~~ still deletes", () => {
    const { container } = render(<Markdown text={"~0.1576 and ~~gone~~"} />);
    expect(container.textContent).toContain("~0.1576");
    expect(container.querySelector("del")?.textContent).toBe("gone");
  });
});

/**
 * Block segmentation, tested on the cases a cheaper splitter gets wrong.
 *
 * The obvious implementation is `text.split(/\n\s*\n/)`, and it is wrong: a blank line is
 * not a block boundary. It appears inside a loose list and inside a fenced code block, and
 * cutting there changes what the reader sees — one list becomes several, and a code block
 * becomes two half-blocks. These are the cases that rule that out.
 */
describe("splitBlocks", () => {
  it("keeps a loose list whole, blank lines and all", () => {
    const src = "- one\n\n- two\n\n- three";
    expect(splitBlocks(src)).toEqual([src]);
  });

  it("keeps a fenced code block whole, blank line and all", () => {
    const src = "```py\na = 1\n\nb = 2\n```";
    expect(splitBlocks(src)).toEqual([src]);
  });

  it("separates the blocks that really are separate", () => {
    expect(splitBlocks("# Title\n\nA paragraph.\n\n- a\n- b")).toEqual([
      "# Title",
      "A paragraph.",
      "- a\n- b",
    ]);
  });

  it("gives display math its own block", () => {
    expect(splitBlocks(String.raw`intro` + "\n\n$$x+1$$\n\nafter")).toEqual([
      "intro",
      "$$x+1$$",
      "after",
    ]);
  });

  it("has nothing to render for empty text", () => {
    expect(splitBlocks("")).toEqual([]);
  });

  it("renders every block, so nothing is dropped on the way through", () => {
    const { container } = render(
      <Markdown text={"# Title\n\nA paragraph.\n\n| a | b |\n| - | - |\n| 1 | 2 |"} />,
    );
    expect(container.querySelector("h1")?.textContent).toBe("Title");
    expect(container.textContent).toContain("A paragraph.");
    expect(container.querySelector(".md-tablewrap table")).not.toBeNull();
  });
});
