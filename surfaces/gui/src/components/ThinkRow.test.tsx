import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { ThinkRow, scrollMaskStyle } from "./ThinkRow";

afterEach(cleanup);

/** jsdom has no layout: give the body the numbers a real scroll port would report. */
function mockMetrics(element: HTMLElement, { clientHeight, scrollHeight }: { clientHeight: number; scrollHeight: number }) {
  Object.defineProperty(element, "clientHeight", { configurable: true, value: clientHeight });
  Object.defineProperty(element, "scrollHeight", { configurable: true, value: scrollHeight });
}

describe("scrollMaskStyle", () => {
  it("is silent when nothing is hidden", () => {
    expect(scrollMaskStyle("none")).toBeUndefined();
  });

  it("draws ONE gradient whose stops depend on which edges are hidden", () => {
    // Two stacked masks would UNION (everything visible); the stops are what intersect them.
    const bottom = scrollMaskStyle("bottom")!.maskImage as string;
    expect(bottom).toContain("black 0px, black 24px");
    expect(bottom).toContain("black calc(100% - 24px), transparent 100%");
    const top = scrollMaskStyle("top")!.maskImage as string;
    expect(top).toContain("transparent 0px, black 24px");
    expect(top).toContain("black calc(100% - 24px), black 100%");
    const both = scrollMaskStyle("both")!.maskImage as string;
    expect(both).toContain("transparent 0px, black 24px");
    expect(both).toContain("black calc(100% - 24px), transparent 100%");
  });
});

describe("ThinkRow", () => {
  it("says Thinking while it runs and offers the newest line, the reference design's shape", () => {
    render(<ThinkRow text={"first line\nnewest line"} running />);
    expect(screen.getByTestId("think-row").getAttribute("data-state")).toBe("running");
    expect(document.querySelector(".think-row-title")!.textContent).toBe("Thinking");
    const summary = screen.getByTestId("think-summary");
    expect(summary.textContent).toBe("newest line");
    // The mask hooks are there for the CSS/test layer even when jsdom cannot measure.
    expect(summary.hasAttribute("data-reasoning-streaming-line")).toBe(true);
    expect(summary.getAttribute("data-mask")).toBe("none");
  });

  it("shows the FIRST line once the trace has settled", () => {
    render(<ThinkRow text={"first line\nnewest line"} />);
    expect(screen.getByTestId("think-row").getAttribute("data-state")).toBe("ok");
    expect(document.querySelector(".think-row-title")!.textContent).toBe("Thought");
    expect(screen.getByTestId("think-summary").textContent).toBe("first line");
  });

  describe("an overflowing summary fades only the edge whose text is out of view", () => {
    // GUI rendering review, G2: a settled row is pinned to its START, yet the mask faded both
    // edges — "All 9 modules…" rendered as "\ll 9 modules…", the first letter eaten.
    const widths = (element: HTMLElement) =>
      element.classList?.contains("think-row-summary") ? { scroll: 600, client: 200 } : null;
    let restore: () => void = () => {};
    const overflow = () => {
      const proto = HTMLElement.prototype;
      const scroll = Object.getOwnPropertyDescriptor(proto, "scrollWidth");
      const client = Object.getOwnPropertyDescriptor(proto, "clientWidth");
      Object.defineProperty(proto, "scrollWidth", {
        configurable: true,
        get(this: HTMLElement) {
          return widths(this)?.scroll ?? 0;
        },
      });
      Object.defineProperty(proto, "clientWidth", {
        configurable: true,
        get(this: HTMLElement) {
          return widths(this)?.client ?? 0;
        },
      });
      restore = () => {
        if (scroll) Object.defineProperty(proto, "scrollWidth", scroll);
        if (client) Object.defineProperty(proto, "clientWidth", client);
      };
    };
    afterEach(() => restore());

    it("settled: pinned to the start, so only the right edge fades", () => {
      overflow();
      render(<ThinkRow text={"All 9 modules import cleanly and the suite is green"} />);
      const summary = screen.getByTestId("think-summary");
      expect(summary.getAttribute("data-mask")).toBe("end");
      expect(summary.style.maskImage).toMatch(/^linear-gradient\(to right, black 0/);
      expect(summary.style.maskImage).toContain("transparent 100%");
    });

    it("running: pinned to the end, so only the left edge fades", () => {
      overflow();
      render(<ThinkRow text={"weighing the two options against the tests we have"} running />);
      const summary = screen.getByTestId("think-summary");
      expect(summary.getAttribute("data-mask")).toBe("start");
      expect(summary.style.maskImage).toMatch(/^linear-gradient\(to right, transparent 0/);
      expect(summary.style.maskImage).toContain("black 100%");
    });
  });

  it("follows the tail of the open body while it streams", () => {
    const { rerender } = render(<ThinkRow text={"a"} running />);
    fireEvent.click(document.querySelector("[data-disclosure-row]")!);
    const body = screen.getByTestId("think-body");
    mockMetrics(body, { clientHeight: 100, scrollHeight: 300 });
    rerender(<ThinkRow text={"a\nb\nc"} running />);
    expect(body.scrollTop).toBe(300);
    // Pinned to the tail: the content that is out of view is ABOVE, so that is the edge
    // that fades.
    expect(body.getAttribute("data-mask")).toBe("top");
  });

  it("stops following the moment the reader scrolls up", () => {
    const { rerender } = render(<ThinkRow text={"a"} running />);
    fireEvent.click(document.querySelector("[data-disclosure-row]")!);
    const body = screen.getByTestId("think-body");
    mockMetrics(body, { clientHeight: 100, scrollHeight: 300 });
    rerender(<ThinkRow text={"a\nb"} running />);
    // The reader drags the port away from the bottom…
    body.scrollTop = 40;
    fireEvent.scroll(body);
    // …and a new token must not yank it back.
    rerender(<ThinkRow text={"a\nb\nc"} running />);
    expect(body.scrollTop).toBe(40);
    expect(body.getAttribute("data-mask")).toBe("both");
  });
});
