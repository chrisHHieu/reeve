import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { Markdown } from "../Markdown";
import { DeliveredFiles } from "./Deliverables";
import { CodeCommentCards } from "./CodeCommentCards";
import { codeCommentsOf, hideOpenTail, parseDirectives } from "./directives";

afterEach(cleanup);

describe("parseDirectives", () => {
  it("rewrites a citation in place, as a link the renderer can chip", () => {
    const { text, citations } = parseDirectives(
      'Chỗ cắm ở :file-citation{path="harness/tools/files.py" purpose="nơi parser patch sống"} đó.',
    );
    expect(text).toContain("[files.py](file:harness/tools/files.py");
    expect(text).toContain("nơi parser patch sống");
    expect(citations).toEqual([
      { path: "harness/tools/files.py", purpose: "nơi parser patch sống" },
    ]);
  });

  it("tolerates smart quotes and a missing purpose", () => {
    const { citations } = parseDirectives(':file-citation{path=“src/a.ts”}');
    expect(citations).toEqual([{ path: "src/a.ts" }]);
  });

  it("lifts review comments out of the prose and keeps their fields", () => {
    const { text, comments } = parseDirectives(
      'Xong.\n:code-comment{title="Thiếu fallback" body="Trả None đi" file="inbox_routing.py" start=52 priority="medium"}',
    );
    expect(text.trim()).toBe("Xong.");
    expect(comments).toEqual([
      {
        title: "Thiếu fallback",
        body: "Trả None đi",
        file: "inbox_routing.py",
        start: 52,
        priority: "medium",
      },
    ]);
  });

  it("leaves ordinary colons and unknown directives alone", () => {
    const { text, citations, comments } = parseDirectives("Lưu ý: điều này ổn. `a:b` và :unknown{x=1}");
    expect(text).toContain("Lưu ý: điều này ổn.");
    expect(text).toContain(":unknown{x=1}");
    expect(citations).toEqual([]);
    expect(comments).toEqual([]);
  });

  it("hides a half-written directive while the answer streams", () => {
    expect(hideOpenTail('Xong. :code-comment{title="Thiếu')).toBe("Xong. ");
    expect(hideOpenTail("Xong. :code-comment{title=\"ok\"} đã đóng")).toContain("đã đóng");
    expect(hideOpenTail("không có gì")).toBe("không có gì");
  });
});

describe("markdown citations", () => {
  it("draws a citation as an inline pill: the file's logo and name, the rest in the tooltip", () => {
    // Design C (2026-09-28): the folder in the line was noise; the pill is the name, and
    // the full path and the purpose are one hover away.
    render(<Markdown text={':file-citation{path="harness/tools/files.py" purpose="parser"}'} />);
    const chip = screen.getByTestId("file-citation");
    expect(chip.textContent).toBe("files.py");
    expect(chip.querySelector("svg")).not.toBeNull();
    expect(chip.getAttribute("title")).toBe("harness/tools/files.py — parser");
    // The directive itself never reaches the page.
    expect(document.body.textContent).not.toContain(":file-citation");
  });
});

describe("CodeCommentCards", () => {
  const comment = {
    title: "Regex này ăn cả tiếng Việt",
    body: "Cần normalize NFD trước khi so.",
    file: "harness/inbox_routing.py",
    start: 41,
    end: 48,
    priority: "high" as const,
  };

  it("lists the comments as cards, with the file, the lines and the priority", () => {
    render(<CodeCommentCards comments={[comment]} />);
    expect(screen.getByTestId("code-comments").textContent).toContain("1 review comment");
    expect(screen.getByText("Regex này ăn cả tiếng Việt")).toBeTruthy();
    expect(screen.getByText("inbox_routing.py")).toBeTruthy();
    expect(screen.getByText("dòng 41–48")).toBeTruthy();
    expect(document.querySelector('.code-comment[data-priority="high"]')).toBeTruthy();
  });

  it("collapses past the first few, and opens again on click", () => {
    render(<CodeCommentCards comments={[comment, comment, comment, comment]} />);
    expect(screen.queryByText("Regex này ăn cả tiếng Việt")).toBeNull();
    fireEvent.click(screen.getByTestId("code-comments-toggle"));
    expect(screen.getAllByText("Regex này ăn cả tiếng Việt")).toHaveLength(4);
  });

  it("renders nothing when the answer left no comments", () => {
    const { container } = render(<CodeCommentCards comments={[]} />);
    expect(container.firstChild).toBeNull();
    expect(codeCommentsOf("prose only")).toEqual([]);
  });
});

describe("DeliveredFiles", () => {
  it("draws what `present` declared, and nothing for a turn that presented nothing", () => {
    // 2026-10-09: a bare `index.html` in the prose drew an "HTML page" row beside the row
    // `present` had declared for `site/index.html` — the same file twice. Files come from
    // `present` alone. A dev server the prose names is no card either (removed 2026-10-15).
    const { container, rerender } = render(<DeliveredFiles files={[]} />);
    expect(container.firstChild).toBeNull();
    rerender(<DeliveredFiles files={[{ path: "site/index.html" }]} />);
    expect(screen.getAllByTestId("delivered-file-card")).toHaveLength(1);
  });
});
