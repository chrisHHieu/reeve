import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render } from "@testing-library/react";

/**
 * Does an arriving token actually stop re-rendering the whole answer?
 *
 * That is the entire point of splitting a message into blocks, and it is invisible in the
 * DOM: React reuses the nodes of unchanged output either way, so a correct screen proves
 * nothing about the work done to produce it. So react-markdown is replaced with a recorder
 * and the question is asked directly — which text did the renderer actually run on?
 *
 * `vi.mock` is module-wide, which is why this lives in its own file rather than beside the
 * tests that need the real renderer.
 */
const rendered: string[] = [];
vi.mock("react-markdown", () => ({
  default: ({ children }: { children: string }) => {
    rendered.push(children);
    return <div data-testid="block">{children}</div>;
  },
  defaultUrlTransform: (u: string) => u,
}));

const { Markdown } = await import("./Markdown");

beforeEach(() => {
  rendered.length = 0;
});
afterEach(cleanup);

describe("block memoization", () => {
  it("runs the renderer once per block on first paint", () => {
    render(<Markdown text={"# Title\n\nfirst para\n\nsecond para"} />);
    expect(rendered).toEqual(["# Title", "first para", "second para"]);
  });

  it("re-renders only the block the new token landed in", () => {
    const { rerender } = render(<Markdown text={"# Title\n\nfirst para\n\nsec"} />);
    rendered.length = 0;

    rerender(<Markdown text={"# Title\n\nfirst para\n\nsecond"} />);

    // The heading and the settled paragraph are not handed to the renderer again. Without
    // the memo this array holds all three, and the cost of a token grows with the length
    // of the answer — quadratic over a long one.
    expect(rendered).toEqual(["second"]);
  });

  it("does not skip a block whose text changed earlier in the message", () => {
    // The memo must key on the block's own text, not on its position: a correction that
    // rewrites an earlier paragraph has to reach the screen.
    const { rerender } = render(<Markdown text={"alpha\n\nomega"} />);
    rendered.length = 0;

    rerender(<Markdown text={"alpha edited\n\nomega"} />);

    expect(rendered).toEqual(["alpha edited"]);
  });

  it("renders a whole new block when the message grows one", () => {
    const { rerender } = render(<Markdown text={"alpha"} />);
    rendered.length = 0;

    rerender(<Markdown text={"alpha\n\nbeta"} />);

    expect(rendered).toEqual(["beta"]);
  });
});
