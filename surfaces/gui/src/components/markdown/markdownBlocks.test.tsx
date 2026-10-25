import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Markdown, mergeImageOnlyLines } from "../Markdown";
import { MarkdownBoundary } from "./MarkdownBoundary";
import { MarkdownImage, MarkdownImageGallery } from "./MarkdownImage";
import { tableToCsv, tableToMarkdown } from "./MarkdownTable";
import { repairStreamingMarkdown } from "./streamRepair";
import { fadeEdge } from "../primitives/ScrollFade";

afterEach(cleanup);

describe("repairStreamingMarkdown", () => {
  it("closes what is still open, so a half-written answer stops flickering", () => {
    expect(repairStreamingMarkdown("A **bold claim")).toBe("A **bold claim**");
    expect(repairStreamingMarkdown("Use `run_shell")).toContain("`");
    // An open fence stays a fence (the parser already handles it) and so does block math.
    expect(repairStreamingMarkdown("```python\nx = 1")).toContain("```");
    expect(repairStreamingMarkdown("$$\nE = mc^2")).toContain("$$");
  });

  it("does not invent money or strikethrough from settled text", () => {
    // prepareMath has already escaped prices by the time repair runs; a lone ~ is a character.
    expect(repairStreamingMarkdown("It costs \\$5 today")).toContain("\\$5");
    expect(repairStreamingMarkdown("about 20~25 degrees")).toContain("20~25");
  });

  it("shows an incomplete link as its text, not as a dead anchor", () => {
    const repaired = repairStreamingMarkdown("see [the docs](https://exa");
    expect(repaired).toContain("the docs");
    expect(repaired).not.toContain("streamdown:incomplete-link");
  });
});

describe("fadeEdge", () => {
  it("names the same state the way each axis reads", () => {
    expect(fadeEdge("vertical", "top")).toBe("top");
    expect(fadeEdge("vertical", "bottom")).toBe("bottom");
    expect(fadeEdge("horizontal", "top")).toBe("left");
    expect(fadeEdge("horizontal", "bottom")).toBe("right");
    expect(fadeEdge("horizontal", "both")).toBe("both");
    expect(fadeEdge("horizontal", "none")).toBe("none");
  });
});

describe("markdown tables", () => {
  it("renders a copy/CSV toolbar over the table it belongs to", async () => {
    render(<Markdown text={"| a | b |\n| --- | --- |\n| 1 | 2 |"} />);
    expect(document.querySelector(".md-tablewrap > table")).toBeTruthy();
    const copy = screen.getByTestId("table-copy");
    expect(screen.getByTestId("table-csv")).toBeTruthy();
    const writes: string[] = [];
    Object.assign(navigator, {
      clipboard: { writeText: vi.fn((value: string) => writes.push(value)) },
    });
    fireEvent.click(copy);
    await waitFor(() => expect(writes.length).toBe(1));
    expect(writes[0]).toContain("| a | b |");
    expect(writes[0]).toContain("| 1 | 2 |");
  });

  it("sorts by a clicked header, keeps a total row last, and reads the sorted column", () => {
    const text = [
      "| Khoản | Số tiền |",
      "| --- | ---: |",
      "| [Tiền nhà](https://example.com) | 6.500.000 |",
      "| Điện | 780.000 |",
      "| Học phí | 2.400.000 |",
      "| **Tổng** | 9.680.000 |",
    ].join("\n");
    const { container } = render(<Markdown text={text} />);
    const firstCells = () => [...container.querySelectorAll("tbody tr")].map((row) => row.querySelector("td")!.textContent);
    expect(firstCells()).toEqual(["Tiền nhà", "Điện", "Học phí", "Tổng"]);
    const amount = screen.getByText("Số tiền").closest("th")!;
    fireEvent.click(amount);
    expect(amount.getAttribute("aria-sort")).toBe("ascending");
    expect(firstCells()).toEqual(["Điện", "Học phí", "Tiền nhà", "Tổng"]);
    // The row moved with its rendered content: the link inside the cell is still a link.
    expect(container.querySelector("tbody tr:nth-child(3) a")?.textContent).toBe("Tiền nhà");
    expect(screen.getByTestId("table-stats").textContent).toMatch(/Sum 9.680.000|Sum 9,680,000/);
    fireEvent.click(amount);
    expect(firstCells()).toEqual(["Tiền nhà", "Học phí", "Điện", "Tổng"]);
    fireEvent.click(amount);
    expect(amount.getAttribute("aria-sort")).toBe("none");
    expect(firstCells()).toEqual(["Tiền nhà", "Điện", "Học phí", "Tổng"]);
    expect(screen.queryByTestId("table-status")).toBeNull();
  });

  it("filters a long table and says how many rows are left", () => {
    const rows = Array.from({ length: 9 }, (_, i) => `| ${i % 2 ? "Ăn uống" : "Đi lại"} ${i} | ${i} |`);
    render(<Markdown text={["| Mục | N |", "| --- | --- |", ...rows].join("\n")} />);
    fireEvent.change(screen.getByTestId("table-filter"), { target: { value: "an uong" } });
    expect(document.querySelectorAll("tbody tr")).toHaveLength(4);
    expect(screen.getByTestId("table-status").textContent).toContain("4 of 9 rows");
  });

  it("gives a short table no filter box", () => {
    render(<Markdown text={"| a | b |\n| --- | --- |\n| 1 | 2 |\n| 3 | 4 |"} />);
    expect(screen.queryByTestId("table-filter")).toBeNull();
    expect(document.querySelector("th.md-th-sort")).toBeTruthy();
  });

  it("serialises rows as Markdown and as RFC-4180 CSV", () => {
    const rows = [
      ["name", "note"],
      ["a,b", 'said "hi"'],
    ];
    expect(tableToMarkdown(rows)).toBe('| name | note |\n| --- | --- |\n| a,b | said "hi" |');
    expect(tableToCsv(rows)).toBe('name,note\r\n"a,b","said ""hi"""');
  });
});

// ```mermaid fences are NOT drawn any more (owner, 2026-10-03): the diagram engine is gone from
// the bundle, and the fence is ordinary code. Pinned so a re-introduction is a deliberate act
// rather than a silent regression back to a renderer nobody asked for.
describe("markdown code fences", () => {
  it("leaves a mermaid fence as plain code, with its language named", () => {
    render(<Markdown text={"```mermaid\ngraph TD; A-->B\n```"} />);
    const block = document.querySelector(".md-codeblock");
    expect(block?.getAttribute("data-code-kind")).toBeNull();
    expect(screen.getByTestId("code-language").textContent).toBe("mermaid");
    expect(block?.textContent).toContain("graph TD; A-->B");
    // The wrap toggle every other fence has, and no diagram surface.
    expect(screen.getByTestId("code-wrap")).toBeTruthy();
    expect(screen.queryByTestId("mermaid-block")).toBeNull();
    expect(screen.queryByTestId("mermaid-svg")).toBeNull();
  });
});

describe("markdown images", () => {
  it("opens a picture full size, and closes it again", async () => {
    render(<MarkdownImage src="https://example.com/shot.png" alt="shot" inLink={false} />);
    fireEvent.click(screen.getByTestId("md-image-open"));
    expect(screen.getByTestId("md-image-viewer")).toBeTruthy();
    expect(screen.getByTestId("md-image-zoom").textContent).toBe("100%");
    fireEvent.click(screen.getByLabelText("Zoom in"));
    expect(screen.getByTestId("md-image-zoom").textContent).toBe("125%");
    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => expect(screen.queryByTestId("md-image-viewer")).toBeNull());
  });

  it("keeps a run of pictures a gallery that steps through them", () => {
    render(
      <MarkdownImageGallery
        images={[
          { src: "https://example.com/a.png", alt: "a" },
          { src: "https://example.com/b.png", alt: "b" },
        ]}
      />,
    );
    expect(screen.getByTestId("md-image-gallery")).toBeTruthy();
    expect(screen.getAllByTestId("md-image-open")).toHaveLength(2);
    fireEvent.click(screen.getAllByTestId("md-image-open")[0]);
    expect(screen.getByTestId("md-image-count").textContent).toBe("1 / 2");
    fireEvent.click(screen.getByTestId("md-image-next"));
    expect(screen.getByTestId("md-image-count").textContent).toBe("2 / 2");
    fireEvent.keyDown(window, { key: "ArrowRight" });
    // Wraps, rather than stopping at the last picture.
    expect(screen.getByTestId("md-image-count").textContent).toBe("1 / 2");
    expect(screen.getByTestId("md-image-download")).toBeTruthy();
  });

  it("stays a plain image inside a link, where a button would be invalid", () => {
    render(<MarkdownImage src="https://example.com/shot.png" alt="shot" inLink />);
    expect(screen.queryByTestId("md-image-open")).toBeNull();
    expect(document.querySelector("img")).toBeTruthy();
  });
});

describe("mergeImageOnlyLines", () => {
  it("makes consecutive pictures ONE block even across blank lines", () => {
    // Markdown would split these into three paragraphs; the rule (and ours) is a gallery.
    expect(mergeImageOnlyLines("![a](1)\n\n![b](2)\n\n![c](3)")).toBe("![a](1)\n![b](2)\n![c](3)");
  });

  it("leaves prose alone, and does not swallow the blank line after a picture", () => {
    expect(mergeImageOnlyLines("text\n\n![a](1)\n\nmore text")).toBe(
      "text\n\n![a](1)\n\nmore text",
    );
    expect(mergeImageOnlyLines("![a](1)\n\nafter")).toBe("![a](1)\n\nafter");
  });
});

describe("MarkdownBoundary", () => {
  function Boom({ explode }: { explode: boolean }) {
    if (explode) throw new Error("renderer bug");
    return <b>fine</b>;
  }

  it("degrades one block to its raw text instead of taking the transcript down", () => {
    const errors = vi.spyOn(console, "error").mockImplementation(() => {});
    render(
      <MarkdownBoundary text={"# Title"}>
        <Boom explode />
      </MarkdownBoundary>,
    );
    expect(screen.getByTestId("markdown-fallback").textContent).toBe("# Title");
    errors.mockRestore();
  });

  it("retries when the text changes — a stream failure is not a verdict", () => {
    const errors = vi.spyOn(console, "error").mockImplementation(() => {});
    const { rerender } = render(
      <MarkdownBoundary text={"# Title"}>
        <Boom explode />
      </MarkdownBoundary>,
    );
    expect(screen.getByTestId("markdown-fallback")).toBeTruthy();
    rerender(
      <MarkdownBoundary text={"# Title fixed"}>
        <Boom explode={false} />
      </MarkdownBoundary>,
    );
    expect(screen.queryByTestId("markdown-fallback")).toBeNull();
    expect(screen.getByText("fine")).toBeTruthy();
    errors.mockRestore();
  });
});
