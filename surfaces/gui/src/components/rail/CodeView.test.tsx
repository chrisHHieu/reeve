import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { CodeView } from "./fileViewers";

afterEach(cleanup);

const PY = [
  "class Mixin:",
  "    def approver(self):",
  "        if allowed:",
  "            return ONCE",
  "",
  "        return outcome",
  "",
  "    def seed(self):",
  "        pass",
].join("\n");

const lineAt = (n: number) => document.querySelector<HTMLElement>(`[data-line="${n}"]`);
const guides = (n: number) => Array.from(lineAt(n)?.querySelectorAll(".ig-line") ?? []);

describe("the code view reads like an editor (owner, 2026-10-15)", () => {
  it("draws one guide per level under a line, and none under top-level code", () => {
    render(<CodeView text={PY} path="m.py" />);
    expect(guides(1)).toHaveLength(0);
    expect(guides(4)).toHaveLength(3);
    // The gap inside the method keeps the method's guides; text and copy are untouched.
    expect(guides(5)).toHaveLength(2);
    expect(lineAt(4)?.querySelector(".rev-code-text")?.textContent).toBe("            return ONCE");
  });

  it("folds a block from its first line and opens it again", async () => {
    render(<CodeView text={PY} path="m.py" />);
    // Only lines that open a block carry an arrow.
    expect(lineAt(2)?.querySelector('[data-testid="code-fold"]')).not.toBeNull();
    expect(lineAt(4)?.querySelector('[data-testid="code-fold"]')).toBeNull();

    fireEvent.click(lineAt(2)!.querySelector('[data-testid="code-fold"]')!);
    await waitFor(() => expect(lineAt(3)).toBeNull());
    expect(lineAt(6)).toBeNull();
    // The next block is still there, and the folded line says something is hidden.
    expect(lineAt(8)).not.toBeNull();
    const more = screen.getByTestId("code-folded");
    expect(more.getAttribute("title")).toBe("Show 4 hidden lines");

    fireEvent.click(more);
    await waitFor(() => expect(lineAt(6)).not.toBeNull());
    expect(screen.queryByTestId("code-folded")).toBeNull();
  });

  it("draws the guide of the picked line's block stronger", () => {
    render(<CodeView text={PY} path="m.py" />);
    fireEvent.click(lineAt(6)!);
    // Line 6 sits in `approver`'s body: that block's guide (the second one) is on, lines 3-6.
    for (const n of [3, 4, 5, 6]) expect(guides(n)[1].classList.contains("on")).toBe(true);
    expect(guides(9)[1].classList.contains("on")).toBe(false);
    expect(guides(4)[0].classList.contains("on")).toBe(false);
  });

  it("opens a fold that hides the line an answer points at", async () => {
    const { rerender } = render(<CodeView text={PY} path="m.py" />);
    fireEvent.click(lineAt(1)!.querySelector('[data-testid="code-fold"]')!);
    await waitFor(() => expect(lineAt(4)).toBeNull());
    rerender(<CodeView text={PY} path="m.py" focusLine={{ line: 4, n: 1 }} />);
    await waitFor(() => expect(lineAt(4)).not.toBeNull());
  });
});
