// What an answer's markdown gained on 2026-10-13 (owner: "còn gì cho coding agentic cho cả dev và
// dân officer"), each from a count over the owner's store: Copy that keeps the formatting, a run
// of `**Label:** value` lines as facts, a text tree as a tree, a draft in a fence as a draft, a
// long answer's contents, and `file.py:42` opening the folder's file at the line.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";

vi.mock("../../api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api")>()),
  sessionFileIndex: vi.fn(),
}));

import { sessionFileIndex } from "../../api";
import { Markdown, OPEN_ARTIFACT_EVENT } from "../Markdown";
import { SessionShellContext } from "../../sessionShell";
import { CodeView } from "../rail/fileViewers";
import { joinFactRuns } from "./facts";
import { isProse, parseTextTree } from "./fenceKinds";
import { contentsOf, headingsOf } from "./Contents";
import { fileWithLine, resetFolderFiles, resolveInFolder } from "./folderFiles";
import { copyAnswer, markdownToHtml } from "./richCopy";

const index = vi.mocked(sessionFileIndex);

/** jsdom's Blob has no `.text()`. */
const read = (blob: Blob) =>
  new Promise<string>((resolve) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.readAsText(blob);
  });

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("Copy keeps the formatting", () => {
  it("draws the answer as a document: bold, a bordered table, no app links", () => {
    const html = markdownToHtml(
      ["**Tổng kết:** 3 đơn trễ.", "", "| Mã | Trễ |", "|---|--:|", "| DH-1 | 2 |", "", "Xem [plan](artifact:docs/plan.md) và [RFC](https://example.com)."].join("\n"),
    );
    expect(html).toContain("<strong>Tổng kết:</strong>");
    expect(html).toMatch(/<table style="[^"]*border-collapse:collapse/);
    expect(html).toMatch(/<td [^>]*style="[^"]*border:1px solid[^"]*text-align:right">2<\/td>/);
    expect(html).toContain('<span>plan</span>');
    expect(html).toContain('<a href="https://example.com">RFC</a>');
  });

  it("puts the Markdown and the document on the clipboard together", async () => {
    const items: Record<string, Blob>[] = [];
    vi.stubGlobal("ClipboardItem", class {
      constructor(public data: Record<string, Blob>) {
        items.push(data);
      }
    });
    const write = vi.fn(async () => {});
    vi.stubGlobal("navigator", { clipboard: { write, writeText: vi.fn() } });
    await copyAnswer("**A** b");
    expect(write).toHaveBeenCalledTimes(1);
    expect(await read(items[0]["text/plain"])).toBe("**A** b");
    expect(await read(items[0]["text/html"])).toContain("<strong>A</strong>");
  });

  it("falls back to the Markdown alone where a rich write is refused", async () => {
    vi.stubGlobal("ClipboardItem", class {});
    const writeText = vi.fn(async () => {});
    vi.stubGlobal("navigator", { clipboard: { write: vi.fn(async () => Promise.reject(new Error("denied"))), writeText } });
    await copyAnswer("**A** b");
    expect(writeText).toHaveBeenCalledWith("**A** b");
  });
});

describe("a run of **Label:** lines is a list of facts", () => {
  const terms = () => [...document.querySelectorAll(".md-facts > dt")].map((dt) => dt.textContent);
  const values = () => [...document.querySelectorAll(".md-facts > dd")].map((dd) => dd.textContent);

  it("reads the lines of one paragraph", () => {
    render(<Markdown text={"**Nguyên nhân:** lỗi `503` đầu tiên.\n**Đã sửa:** thử lại 3 lần.\n**Kiểm tra**: 12/12 qua."} />);
    expect(terms()).toEqual(["Nguyên nhân", "Đã sửa", "Kiểm tra"]);
    expect(values()).toEqual(["lỗi 503 đầu tiên.", "thử lại 3 lần.", "12/12 qua."]);
    expect(document.querySelector(".md-facts code")?.textContent).toBe("503");
  });

  it("reads them with blank lines between, and as a bulleted list", () => {
    render(<Markdown text={"**A:** one\n\n**B:** two\n\n- **C:** three\n- **D:** four"} />);
    expect(terms()).toEqual(["A", "B", "C", "D"]);
    expect(document.querySelectorAll(".md-facts")).toHaveLength(2);
    expect(document.querySelector("ul")).toBeNull();
  });

  it("keeps a continuation line with its value", () => {
    render(<Markdown text={"**A:** one\nstill one\n**B:** two"} />);
    expect(values()[0]?.replace(/\s+/g, " ")).toBe("one still one");
  });

  it("leaves one label, a numbered list, a bold phrase and code as they were", () => {
    render(
      <Markdown
        text={[
          "**Lưu ý:** chỉ một dòng.",
          "",
          "1. **A:** x",
          "2. **B:** y",
          "",
          "**Bold** then **More** words",
          "",
          "```",
          "**X:** in code",
          "**Y:** in code",
          "```",
        ].join("\n")}
      />,
    );
    expect(document.querySelector(".md-facts")).toBeNull();
    expect(document.querySelector("ol")).not.toBeNull();
  });

  it("joins only label paragraphs, never inside a fence", () => {
    expect(joinFactRuns("**A:** x\n\n**B:** y")).toBe("**A:** x\n**B:** y");
    expect(joinFactRuns("**A:** x\n\nplain\n\n**B:** y")).toBe("**A:** x\n\nplain\n\n**B:** y");
    const fenced = "```\n**A:** x\n\n**B:** y\n```";
    expect(joinFactRuns(fenced)).toBe(fenced);
  });
});

const TREE = ["shop/", "├── src/", "│   ├── cart.ts        # the cart", "│   └── api/", "│       └── orders.ts", "└── package.json"].join("\n");

describe("a text tree is a tree", () => {
  it("reads entries, depth, folders and notes", () => {
    const tree = parseTextTree(TREE)!;
    expect(tree.root).toBe("shop");
    expect(tree.entries.map((e) => [e.depth, e.name, e.dir, e.note ?? ""])).toEqual([
      [0, "src", true, ""],
      [1, "cart.ts", false, "the cart"],
      [1, "api", true, ""],
      [2, "orders.ts", false, ""],
      [0, "package.json", false, ""],
    ]);
  });

  it("is drawn as rows with a count, and Copy keeps the text", () => {
    render(<Markdown text={"```\n" + TREE + "\n```"} />);
    expect(screen.getByTestId("md-tree-root").textContent).toBe("shop");
    expect(screen.getByTestId("md-tree-count").textContent).toBe("2 folders, 3 files");
    const rows = [...document.querySelectorAll(".md-tree-row")];
    expect(rows.map((r) => r.querySelector(".md-tree-name")?.textContent)).toEqual(["src", "cart.ts", "api", "orders.ts", "package.json"]);
    expect(rows[3].querySelectorAll(".md-tree-guide")).toHaveLength(2);
    expect(document.querySelector(".md-codeblock-pre")).toBeNull();
  });

  it("stays code when a line is not a tree line, or the fence names a language", () => {
    expect(parseTextTree(TREE + "\nnpm install")).toBeNull();
    // A decision tree drawn with the same branches is not a folder.
    expect(parseTextTree("Pick a mode?\n├── Yes → write the file\n│   └── then run the tests\n└── No → ask again")).toBeNull();
    render(<Markdown text={"```python\n" + TREE + "\n```"} />);
    expect(screen.queryByTestId("md-tree")).toBeNull();
  });
});

const DRAFT = [
  "Tiêu đề: Xác nhận lịch giao hàng tuần 42",
  "",
  "Kính gửi anh Minh,",
  "",
  "Em xin xác nhận 3 đơn sẽ giao vào thứ Năm. Nếu anh cần đổi giờ nhận, anh báo em trước 15h thứ Tư giúp em nhé.",
  "",
  "Trân trọng,",
  "Lan",
].join("\n");

describe("a draft in a fence is a draft", () => {
  it("draws prose with its subject pulled out and its lines kept", () => {
    render(<Markdown text={"```\n" + DRAFT + "\n```"} />);
    expect(screen.getByTestId("md-draft-subject").textContent).toBe("Tiêu đề: Xác nhận lịch giao hàng tuần 42");
    const paragraphs = [...document.querySelectorAll(".md-draft-body p")];
    expect(paragraphs).toHaveLength(3);
    expect(paragraphs[2].querySelectorAll("br")).toHaveLength(1);
    expect(screen.getByTestId("md-draft").querySelector("[data-testid=copy-button]")?.textContent).toBe("Copy");
  });

  it("is not code, a command, a log or a diagram", () => {
    expect(isProse(DRAFT)).toBe(true);
    expect(isProse("const total = items.reduce((a, b) => a + b, 0);\nconsole.log(total) and more words here")).toBe(false);
    expect(isProse("git status\ngit commit -m fix the thing that was broken yesterday")).toBe(false);
    expect(isProse("Error: the file was not found in the folder you named\nWarning: three more were skipped as well")).toBe(false);
    expect(isProse("Client -> Server -> Database, then the answer comes back the same way to us")).toBe(false);
    expect(isProse("Too short to be a draft")).toBe(false);
    // Seen on the dev app: two colour settings with a note after each.
    expect(isProse("accent-gold: lab(45.24% 20.77 91.03) ← light, new L\naccent-purple: lab(54.76% 45.15 -71.74) ← dark, new L")).toBe(false);
    expect(isProse("max_items: fifty for the first page and a hundred after that one")).toBe(false);
    expect(isProse("a3784d2b fix the focus ring on the menu that opened under the line\n9d4e2f81 make the tests pass again")).toBe(false);
    expect(isProse('objective: "do all of it, every part, and tell me when it is done please"')).toBe(false);
  });
});

describe("a long answer opens with its contents", () => {
  const long = ["# Plan", "## Nguyên nhân", "x", "## Cách sửa", "```", "# not a heading", "```", "## Kiểm tra", "## Việc còn lại"].join("\n\n");

  it("names the sections at the highest level that holds two or more, outside fences", () => {
    expect(contentsOf(headingsOf(long))?.map((h) => h.text)).toEqual(["Nguyên nhân", "Cách sửa", "Kiểm tra", "Việc còn lại"]);
    expect(contentsOf(headingsOf("## A\n\n## B\n\n## C"))).toBeNull();
  });

  it("draws the line once the answer has settled, and a name jumps to its section", () => {
    const { rerender } = render(<Markdown text={long} streaming />);
    expect(screen.queryByTestId("md-contents")).toBeNull();
    rerender(<Markdown text={long} />);
    const links = [...screen.getByTestId("md-contents").querySelectorAll("button")];
    expect(links.map((b) => b.textContent)).toEqual(["Nguyên nhân", "Cách sửa", "Kiểm tra", "Việc còn lại"]);
    const target = [...document.querySelectorAll("h2")].find((h) => h.textContent === "Kiểm tra")!;
    const scroll = vi.fn();
    target.scrollIntoView = scroll;
    fireEvent.click(links[2]);
    expect(scroll).toHaveBeenCalledTimes(1);
  });
});

describe("file.py:42 opens the folder's file at the line", () => {
  const FILES = ["src/upload_client.py", "src/a/index.ts", "src/b/index.ts", "README.md"];
  const inSession = (children: ReactNode, environment: unknown = null) => (
    <SessionShellContext.Provider value={{ environment: environment as never, platform: "windows", sessionId: "s1" }}>
      {children}
    </SessionShellContext.Provider>
  );

  beforeEach(() => {
    resetFolderFiles();
    index.mockReset();
    index.mockResolvedValue({ ok: true, files: FILES, truncated: false });
  });

  it("reads the forms models write a line in", () => {
    expect(fileWithLine("upload_client.py:42")).toEqual({ path: "upload_client.py", line: 42 });
    expect(fileWithLine("src/a.ts:12:5")).toEqual({ path: "src/a.ts", line: 12 });
    expect(fileWithLine("src/a.ts#L7-L9")).toEqual({ path: "src/a.ts", line: 7 });
    expect(fileWithLine("upload_client.py")).toBeNull();
    expect(fileWithLine("localhost:8765")).toBeNull();
    expect(fileWithLine("https://x.io/a.js:3")).toBeNull();
  });

  it("finds the file by its path, its tail or an absolute path, and never guesses between two", () => {
    expect(resolveInFolder("upload_client.py", FILES)).toBe("src/upload_client.py");
    expect(resolveInFolder("C:\\work\\shop\\src\\upload_client.py", FILES)).toBe("src/upload_client.py");
    expect(resolveInFolder("index.ts", FILES)).toBeUndefined();
    expect(resolveInFolder("index.ts", FILES, () => "src/b/index.ts")).toBe("src/b/index.ts");
    expect(resolveInFolder("missing.py", FILES)).toBeUndefined();
  });

  it("becomes a mention that opens the file at the line", async () => {
    render(inSession(<Markdown text={"Lỗi ở `upload_client.py:42`, không ở `missing.py:3` hay `index.ts:1`."} />));
    const mention = await screen.findByTestId("file-line");
    expect(mention.querySelector(".md-path-text")?.textContent).toBe("upload_client.py");
    expect(mention.querySelector(".md-path-line")?.textContent).toBe(":42");
    expect(screen.getAllByTestId("file-line")).toHaveLength(1);
    expect(index).toHaveBeenCalledTimes(1);
    const seen: unknown[] = [];
    const listen = (e: Event) => seen.push((e as CustomEvent).detail);
    window.addEventListener(OPEN_ARTIFACT_EVENT, listen);
    fireEvent.click(mention);
    window.removeEventListener(OPEN_ARTIFACT_EVENT, listen);
    expect(seen).toEqual([{ path: "src/upload_client.py", line: 42 }]);
  });

  it("names a link written in code by its words, and opens it where the folder has it", async () => {
    // The owner's screenshot: [`goals.py`](app/domain/goals.py) read "[object Object]", and the
    // model's path was from `backend/`, so the panel said the file was not in the folder.
    index.mockResolvedValue({ ok: true, files: ["backend/app/domain/goals.py", "README.md"], truncated: false });
    render(inSession(<Markdown text={"Sửa [`goals.py`](app/domain/goals.py#L12) và [README](README.md)."} />));
    const links = screen.getAllByTestId("file-link");
    expect(links.map((l) => l.textContent)).toEqual(["goals.py", "README"]);
    await waitFor(() => expect(index).toHaveBeenCalledTimes(1));
    await act(async () => {});
    const seen: unknown[] = [];
    const listen = (e: Event) => seen.push((e as CustomEvent).detail);
    window.addEventListener(OPEN_ARTIFACT_EVENT, listen);
    fireEvent.click(links[0]);
    fireEvent.click(links[1]);
    window.removeEventListener(OPEN_ARTIFACT_EVENT, listen);
    expect(seen).toEqual([{ path: "backend/app/domain/goals.py", line: 12 }, { path: "README.md" }]);
  });

  it("asks for nothing when no span names a line, or on another machine", async () => {
    render(inSession(<Markdown text={"Just `code` here."} />));
    render(inSession(<Markdown text={"`upload_client.py:42`"} />, { name: "box" }));
    await act(async () => {});
    expect(index).not.toHaveBeenCalled();
    expect(screen.queryByTestId("file-line")).toBeNull();
  });
});

describe("the file view marks the line it was opened at", () => {
  it("marks that line and no other", async () => {
    render(<CodeView text={"a\nb\nc"} focusLine={{ line: 2, n: 1 }} />);
    await waitFor(() => expect(document.querySelector("[data-focus]")?.getAttribute("data-line")).toBe("2"));
    expect(document.querySelectorAll("[data-focus]")).toHaveLength(1);
  });
});
