// What an open file offers is decided by these few functions; the view only draws the answer.
import { beforeEach, describe, expect, it } from "vitest";
import {
  fileView,
  hasRenderedForm,
  readDraft,
  readFileMode,
  renderedLabel,
  revealLabel,
  writeDraft,
  writeFileMode,
} from "./fileKinds";

beforeEach(() => localStorage.clear());

describe("fileKinds", () => {
  it("gives a rendered form to Markdown, HTML, SVG, CSV, JSON and notebooks, and to nothing else", () => {
    const rendered = ["notes.md", "a.markdown", "page.mdx", "index.html", "x.HTM", "logo.svg", "rows.csv", "rows.tsv", "data.json", "c.jsonc", "analysis.ipynb"];
    for (const path of rendered) expect(hasRenderedForm(fileView(path)), path).toBe(true);
    const plain = ["main.py", "app.ts", "pyproject.toml", "notes.txt", "Makefile", ".env"];
    for (const path of plain) expect(hasRenderedForm(fileView(path)), path).toBe(false);
  });

  it("names the reading side by what it is", () => {
    expect(renderedLabel(fileView("a.md"))).toBe("Preview");
    expect(renderedLabel(fileView("a.svg"))).toBe("Preview");
    expect(renderedLabel(fileView("a.csv"))).toBe("Table");
    expect(renderedLabel(fileView("a.json"))).toBe("Tree");
  });

  it("remembers the side last picked per view, and reads first by default", () => {
    expect(readFileMode("markdown")).toBe("rendered");
    writeFileMode("markdown", "edit");
    expect(readFileMode("markdown")).toBe("edit");
    expect(readFileMode("table")).toBe("rendered");
  });

  it("calls the system's file manager by its own name", () => {
    expect(revealLabel("Win32")).toBe("Show in Explorer");
    expect(revealLabel("MacIntel")).toBe("Show in Finder");
    expect(revealLabel("Linux x86_64")).toBe("Show in file manager");
  });

  it("keeps a draft per conversation and file until it is cleared", () => {
    writeDraft("s1", "a.py", "x = 1");
    expect(readDraft("s1", "a.py")).toBe("x = 1");
    expect(readDraft("s2", "a.py")).toBeNull();
    expect(readDraft("s1", "b.py")).toBeNull();
    writeDraft("s1", "a.py", null);
    expect(readDraft("s1", "a.py")).toBeNull();
  });
});
