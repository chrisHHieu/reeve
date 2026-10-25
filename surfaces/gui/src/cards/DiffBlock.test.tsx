import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";
import { DiffBlock } from "./DiffBlock";

describe("DiffBlock", () => {
  it("never shows jsdiff's end-of-file marker as a line of code", () => {
    // GUI tools review, B2: both sides lose their final newline before diffing, so a change
    // on the last line made jsdiff emit `\ No newline at end of file`, rendered as a
    // numbered context line — between the − and the + for a patch.
    const { container } = render(
      <DiffBlock diffs={[{ path: "a.py", old_text: "a\nb\n", new_text: "a\nc\n" }]} />,
    );
    expect(container.textContent).not.toContain("No newline");
    expect(container.textContent).toContain("c");
  });
});

describe("DiffBlock line numbers", () => {
  const window = { path: "orders/line.py", old_text: "a\nb\nqty = 1\nc\n", new_text: "a\nb\nqty = 2\nc\n" };

  it("numbers the file's real lines when the hunk says where its window starts", () => {
    const { container } = render(<DiffBlock diffs={[{ ...window, old_start: 5, new_start: 5 }]} />);
    const added = container.querySelector(".rv-diff-add .rv-diff-no");
    // `qty` is line 7 of the file, not line 3 of the window.
    expect(added?.textContent).toBe("7");
  });

  it("shows no numbers for a diff whose place in the file is unknown", () => {
    const { container } = render(<DiffBlock diffs={[window]} />);
    const numbers = [...container.querySelectorAll(".rv-diff-no")].map((el) => el.textContent);
    expect(numbers.every((text) => text === "")).toBe(true);
  });
});
