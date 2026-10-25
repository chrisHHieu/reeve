// Following the end of a scroll region (`useFollowTail.ts`): the view stays on the last line
// while text arrives, lets go when the reader scrolls up, and takes hold again when they come
// back to the end. jsdom lays nothing out, so the three numbers a scroller offers are set by
// hand — which is also what makes each case exact.

import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render } from "@testing-library/react";
import { useRef } from "react";
import { atEnd, useFollowTail } from "./useFollowTail";

afterEach(cleanup);

function Log({ lines, on = true }: { lines: string[]; on?: boolean }) {
  const root = useRef<HTMLDivElement>(null);
  useFollowTail(root, ".body", on, lines, lines.length > 0);
  return (
    <div ref={root}>
      {lines.length > 0 && (
        <div className="body" data-testid="body">
          {lines.map((line, index) => (
            <div key={index}>{line}</div>
          ))}
        </div>
      )}
    </div>
  );
}

/** Give the scroller a geometry: `height` of content inside a 100px window. */
function layout(el: HTMLElement, height: number) {
  Object.defineProperty(el, "scrollHeight", { configurable: true, value: height });
  Object.defineProperty(el, "clientHeight", { configurable: true, value: 100 });
}

describe("atEnd", () => {
  it("counts the last line or so as the end", () => {
    expect(atEnd(900, 1000, 100)).toBe(true);
    expect(atEnd(880, 1000, 100)).toBe(true);
    expect(atEnd(700, 1000, 100)).toBe(false);
    // A region that does not scroll is at its end.
    expect(atEnd(0, 80, 100)).toBe(true);
  });
});

describe("useFollowTail", () => {
  it("moves to the end as lines arrive", () => {
    const { getByTestId, rerender } = render(<Log lines={["one"]} />);
    const body = getByTestId("body");
    layout(body, 400);
    rerender(<Log lines={["one", "two"]} />);
    expect(body.scrollTop).toBe(400);
    layout(body, 900);
    rerender(<Log lines={["one", "two", "three"]} />);
    expect(body.scrollTop).toBe(900);
  });

  it("lets go when the reader scrolls up, and follows again from the end", () => {
    const { getByTestId, rerender } = render(<Log lines={["one"]} />);
    const body = getByTestId("body");
    layout(body, 400);
    rerender(<Log lines={["one", "two"]} />);
    // The reader scrolls up to read something.
    body.scrollTop = 40;
    fireEvent.scroll(body);
    layout(body, 900);
    rerender(<Log lines={["one", "two", "three"]} />);
    expect(body.scrollTop).toBe(40);
    // ...and returns to the end: the next line is followed again.
    body.scrollTop = 800;
    fireEvent.scroll(body);
    layout(body, 1200);
    rerender(<Log lines={["one", "two", "three", "four"]} />);
    expect(body.scrollTop).toBe(1200);
  });

  it("attaches when the region first appears", () => {
    const { getByTestId, rerender } = render(<Log lines={[]} />);
    rerender(<Log lines={["one"]} />);
    const body = getByTestId("body");
    // The reader scrolls up in a region that did not exist at mount: it must still be heard.
    layout(body, 400);
    body.scrollTop = 0;
    fireEvent.scroll(body);
    rerender(<Log lines={["one", "two"]} />);
    expect(body.scrollTop).toBe(0);
  });

  it("does nothing when it is off", () => {
    const { getByTestId, rerender } = render(<Log lines={["one"]} on={false} />);
    const body = getByTestId("body");
    layout(body, 400);
    rerender(<Log lines={["one", "two"]} on={false} />);
    expect(body.scrollTop).toBe(0);
  });
});
