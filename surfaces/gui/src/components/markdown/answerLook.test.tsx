// How an answer's markdown is drawn (owner, 2026-10-13: no grey pills, links that look like
// links, callouts, a code block that says what it is). What can be read from the DOM is pinned
// here; the colours are in styles.css and are looked at in the browser.
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { Markdown, NUMBER_LINES_PAST, fenceInfo } from "../Markdown";
import css from "../../styles.css?raw";

afterEach(cleanup);

/** The declarations inside one rule, by its exact selector. */
function rule(selector: string): string {
  const start = css.indexOf(`\n${selector} {`);
  expect(start, `no rule for ${selector}`).toBeGreaterThan(-1);
  return css.slice(start, css.indexOf("}", start));
}

describe("code inside a sentence", () => {
  // jsdom has no cascade worth reading, so the chip is pinned in the source: its wash and its
  // edge are mixed from the code ink (it follows every palette), and a link's code wears none.
  it("is a chip of its own ink, never a grey, and none inside a link", () => {
    const chip = rule(".md :not(pre) > code");
    expect(chip).toContain("color: var(--syntax-property)");
    expect(chip).toContain("background: color-mix(in srgb, var(--syntax-property) 7%, transparent)");
    expect(chip).toContain("color-mix(in srgb, var(--syntax-property) 26%, transparent)");
    expect(chip).not.toContain("--md-code-bg");
    const inLink = rule(".md a :not(pre) > code, .md a code");
    expect(inLink).toContain("background: none");
    expect(inLink).toContain("box-shadow: none");
  });
});

describe("callouts", () => {
  it("draws GitHub's five alerts with their kind and title, and the marker is gone", () => {
    const kinds = ["NOTE", "TIP", "IMPORTANT", "WARNING", "CAUTION"];
    render(<Markdown text={kinds.map((k) => `> [!${k}]\n> Body of ${k.toLowerCase()}.`).join("\n\n")} />);
    const alerts = screen.getAllByTestId("md-alert");
    expect(alerts.map((a) => a.getAttribute("data-alert"))).toEqual(["note", "tip", "important", "warning", "caution"]);
    expect(alerts.map((a) => a.querySelector(".md-alert-title")?.textContent)).toEqual([
      "Note",
      "Tip",
      "Important",
      "Warning",
      "Caution",
    ]);
    expect(alerts[3].textContent).toContain("Body of warning.");
    expect(document.body.textContent).not.toContain("[!");
  });

  it("keeps a plain quote a quote, and a marker that is not on the first line is text", () => {
    render(<Markdown text={"> Just a quote.\n\n> First line\n> [!NOTE] later"} />);
    expect(screen.queryByTestId("md-alert")).toBeNull();
    expect(document.querySelectorAll("blockquote")).toHaveLength(2);
    expect(document.body.textContent).toContain("[!NOTE] later");
  });
});

describe("the fenced block", () => {
  it("reads the file a fence names, in the ways models write it", () => {
    expect(fenceInfo("python", "upload_client.py")).toEqual({ lang: "python", file: "upload_client.py" });
    expect(fenceInfo("ts", 'title="src/a.ts"')).toEqual({ lang: "ts", file: "src/a.ts" });
    expect(fenceInfo("python:tools/run.py", undefined)).toEqual({ lang: "python", file: "tools/run.py" });
    expect(fenceInfo("python", "{1,3} showLineNumbers")).toEqual({ lang: "python", file: undefined });
    expect(fenceInfo(undefined, undefined)).toEqual({ lang: undefined, file: undefined });
  });

  it("names the file in its bar and numbers its lines past five", () => {
    const long = Array.from({ length: NUMBER_LINES_PAST + 1 }, (_, i) => `x${i} = ${i}`).join("\n");
    render(<Markdown text={"```python upload_client.py\n" + long + "\n```"} />);
    expect(screen.getByTestId("code-file").textContent).toBe("upload_client.py");
    expect(screen.getByTestId("code-language").textContent).toBe("python");
    const numbers = [...document.querySelectorAll(".md-ln")].map((n) => n.getAttribute("data-n"));
    expect(numbers).toEqual(["1", "2", "3", "4", "5", "6"]);
    // The numbers are drawn, not written: the code's own text has none of them.
    expect(document.querySelector("pre code")!.textContent).toBe(long);
  });

  it("leaves a short block and a command unnumbered", () => {
    render(<Markdown text={"```js\na()\nb()\n```\n\n```bash\n" + "echo 1\n".repeat(8) + "```"} />);
    expect(document.querySelectorAll(".md-ln")).toHaveLength(0);
  });
});

describe("links and tables", () => {
  it("marks a link that leaves the app, and not one to a file", () => {
    render(<Markdown text={"See [the RFC](https://www.rfc-editor.org/rfc/rfc9110) and [mail](mailto:a@b.c)."} />);
    const links = [...document.querySelectorAll("a.external-link")];
    expect(links.map((a) => !!a.querySelector(".md-link-out"))).toEqual([true, false]);
  });

  it("lines a column of numbers up at the right, unless the writer aligned it", () => {
    const table = [
      "| Run | Passed | Time | Note |",
      "|---|---|:--|---|",
      "| before | 71% | 1.2s | slow |",
      "| after | 98% | 1.9s | ok |",
      "| none | 93% | 2.4s | ok |",
    ].join("\n");
    render(<Markdown text={table} />);
    const head = [...document.querySelectorAll("th")].map((th) => th.classList.contains("md-num"));
    expect(head).toEqual([false, true, false, false]);
    const first = [...document.querySelectorAll("tbody tr")[0].querySelectorAll("td")].map((td) => td.classList.contains("md-num"));
    expect(first).toEqual([false, true, false, false]);
  });
});

describe("a line break written as HTML", () => {
  it("breaks the line in a table's cell and in a sentence, and nothing else written as HTML is drawn", () => {
    const text = [
      "| Group | Words |",
      "|---|---|",
      "| Weather | Warm<br>Cool<br/>Mild<BR /> |",
      "",
      "One<br>two, `a<br>b` stays code, and <script>x</script> <b>stays</b> text.",
    ].join("\n");
    render(<Markdown text={text} />);
    const cell = document.querySelectorAll("td")[1];
    expect(cell.querySelectorAll("br")).toHaveLength(3);
    expect(cell.textContent!.replace(/\s+/g, "")).toBe("WarmCoolMild"); // no "<br>" left as text
    const paragraph = document.querySelector("p")!;
    expect(paragraph.querySelectorAll("br")).toHaveLength(1);
    expect(paragraph.querySelector("code")!.textContent).toBe("a<br>b");
    expect(document.querySelector("script")).toBeNull();
    expect(paragraph.querySelector("b")).toBeNull();
    expect(document.body.textContent).not.toContain("<br>two");
  });
});
