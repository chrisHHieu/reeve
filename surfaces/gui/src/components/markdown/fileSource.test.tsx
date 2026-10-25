// A Markdown FILE has a folder: its local pictures are read from there, and (only there) a
// mermaid fence is drawn. A chat message has neither, and stays exactly as it was.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Markdown } from "../Markdown";
import { localImagePath, MarkdownFileContext } from "./fileSource";
import { readArtifact } from "../../api";

vi.mock("../../api", () => ({ readArtifact: vi.fn() }));
vi.mock("mermaid", () => ({
  default: {
    initialize: vi.fn(),
    render: vi.fn(async (_id: string, code: string) => {
      if (code.includes("broken")) throw new Error("Parse error on line 1");
      return { svg: '<svg viewBox="0 0 320 120"><text>drawn</text></svg>' };
    }),
  },
}));

const read = vi.mocked(readArtifact);
const PNG = "data:image/png;base64,iVBORw0KGgo=";

function inFile(text: string, path = "docs/guide/notes.md") {
  return render(
    <MarkdownFileContext.Provider value={{ sessionId: "s1", path, diagrams: true }}>
      <Markdown text={text} />
    </MarkdownFileContext.Provider>,
  );
}

beforeEach(() => {
  read.mockImplementation(async (_session, path) =>
    path.endsWith("missing.png")
      ? ({ ok: false, path, kind: "image", error: "not found" } as never)
      : ({ ok: true, path, kind: "image", data_url: PNG } as never),
  );
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("localImagePath", () => {
  it("reads a relative path from the file's own folder", () => {
    expect(localImagePath("docs/guide/notes.md", "images/flow.png")).toBe("docs/guide/images/flow.png");
    expect(localImagePath("docs/guide/notes.md", "./flow.png")).toBe("docs/guide/flow.png");
    expect(localImagePath("docs/guide/notes.md", "../shared/flow%20chart.png")).toBe("docs/shared/flow chart.png");
    expect(localImagePath("notes.md", "flow.png?v=2#top")).toBe("flow.png");
    expect(localImagePath("D:\\work\\docs\\notes.md", "img\\a.png")).toBe("D:/work/docs/img/a.png");
  });

  it("keeps a full path, and leaves the web and embedded pictures alone", () => {
    expect(localImagePath("notes.md", "C:\\shots\\a.png")).toBe("C:/shots/a.png");
    expect(localImagePath("notes.md", "/home/me/a.png")).toBe("/home/me/a.png");
    expect(localImagePath("notes.md", "file:///C:/shots/a.png")).toBe("C:/shots/a.png");
    expect(localImagePath("notes.md", "https://example.com/a.png")).toBeNull();
    expect(localImagePath("notes.md", PNG)).toBeNull();
  });
});

describe("a Markdown file's pictures", () => {
  it("draws a local picture from where the file lives", async () => {
    inFile("![The flow](images/flow.png)");
    await waitFor(() => expect(document.querySelector("img.md-img")?.getAttribute("src")).toBe(PNG));
    expect(read).toHaveBeenCalledWith("s1", "docs/guide/images/flow.png");
    expect(document.querySelector("img.md-img")?.getAttribute("alt")).toBe("The flow");
  });

  it("says a picture is not there, in its place", async () => {
    inFile("![Shot](missing.png)");
    const note = await screen.findByTestId("md-image-missing");
    expect(note.textContent).toContain("Shot: picture not found at missing.png");
    expect(document.querySelector("img")).toBeNull();
  });

  it("leaves a picture on the web to the browser, and draws an embedded one", () => {
    inFile(`![a](https://example.com/a.png)\n\n![b](${PNG})`);
    const sources = Array.from(document.querySelectorAll("img.md-img")).map((img) => img.getAttribute("src"));
    expect(sources).toEqual(["https://example.com/a.png", PNG]);
    expect(read).not.toHaveBeenCalled();
  });

  it("a run of local pictures is one gallery, each read from the file's folder", async () => {
    inFile("![one](a.png)\n![two](b.png)");
    const gallery = await screen.findByTestId("md-image-gallery");
    expect(gallery.querySelectorAll("img").length).toBe(2);
    expect(read).toHaveBeenCalledWith("s1", "docs/guide/a.png");
    expect(read).toHaveBeenCalledWith("s1", "docs/guide/b.png");
  });

  it("a chat message is untouched: nothing is read, and a data: link is still emptied", () => {
    render(<Markdown text={"![x](images/flow.png)\n\n[run](data:text/html;base64,PHNjcmlwdD4=)"} />);
    expect(read).not.toHaveBeenCalled();
    expect(document.querySelector("img.md-img")?.getAttribute("src")).toBe("images/flow.png");
    expect(document.querySelector("a")?.getAttribute("href") || "").toBe("");
  });
});

describe("a Markdown file's diagrams", () => {
  it("draws a mermaid fence as an image, with its source one click away", async () => {
    inFile("```mermaid\ngraph TD; A-->B\n```");
    const image = (await screen.findByTestId("mermaid-image")) as HTMLImageElement;
    // An image, never markup: whatever the source says, nothing in it can run.
    expect(image.getAttribute("src")).toMatch(/^data:image\/svg\+xml;charset=utf-8,%3Csvg/);
    expect(image.getAttribute("width")).toBe("320");
    expect(document.querySelector(".md-diagram-stage svg")).toBeNull();

    fireEvent.click(screen.getByTestId("mermaid-source"));
    expect(screen.getByTestId("mermaid-diagram").textContent).toContain("graph TD; A-->B");
    expect(screen.queryByTestId("mermaid-image")).toBeNull();
  });

  it("says why a diagram could not be drawn, and shows what was written", async () => {
    inFile("```mermaid\nbroken diagram\n```");
    const error = await screen.findByTestId("mermaid-error");
    expect(error.textContent).toContain("could not be drawn: Parse error on line 1");
    expect(screen.getByTestId("mermaid-diagram").textContent).toContain("broken diagram");
  });

  it("only where a file preview asks: other fences, and a file that does not ask, stay code", () => {
    inFile("```python\nprint(1)\n```");
    expect(screen.queryByTestId("mermaid-diagram")).toBeNull();
    cleanup();
    render(
      <MarkdownFileContext.Provider value={{ sessionId: "s1", path: "a.md" }}>
        <Markdown text={"```mermaid\ngraph TD; A-->B\n```"} />
      </MarkdownFileContext.Provider>,
    );
    expect(screen.queryByTestId("mermaid-diagram")).toBeNull();
    expect(screen.getByTestId("code-language").textContent).toBe("mermaid");
  });
});
