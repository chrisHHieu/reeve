// @vitest-environment jsdom
// Comments and marks on a design (step 2 of the workspace plan): what the page reports is
// read as text and numbers only, a comment says how its part looks as well as where it is, every
// ask carries the scope rule, a mark names its area and the parts under it, and the dock sends the
// comments picked, or all of them.
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { asElement } from "./DesignTools";
import { CommentPopover, CommentsDock } from "./DesignComments";
import { markBounds } from "./MarkLayer";
import { markAttachment, pinsAttachment, pointedAttachment, SCOPE_RULE, styleLine, type DesignPin } from "./designAsks";

afterEach(cleanup);

const reported = {
  selector: "main > div.plan:nth-of-type(2) > button.cta",
  tag: "button",
  cls: "cta",
  text: "Try Team",
  html: '<button class="cta">Try Team</button>',
  w: 188,
  h: 48,
  x: 612,
  y: 840,
  vw: 1440,
  style: { font: "Manrope", size: 15, weight: "600", color: "#ffffff", bg: "#6a3577", radius: "8px", extra: "<script>" },
  evil: { nested: true },
};

describe("what a comment says", () => {
  it("reads only text and numbers from the page, the style included", () => {
    const el = asElement(reported)!;
    expect(el).toMatchObject({ x: 612, y: 840, style: { font: "Manrope", size: 15, color: "#ffffff", bg: "#6a3577" } });
    expect(el).not.toHaveProperty("evil");
    expect(el.style).not.toHaveProperty("extra");
    expect(asElement({ ...reported, x: "12" })!.x).toBeUndefined();
  });

  it("says where the part is and how it looks, with the scope rule", () => {
    const el = asElement(reported)!;
    expect(styleLine(el.style)).toBe("Manrope 15 px / 600 · #ffffff on #6a3577 · corners 8px");
    const point = pointedAttachment("design/pricing.html", el, 1440);
    expect(point.text).toContain(SCOPE_RULE);
    expect(point.text).toContain("position: x 612, y 840 on the page");
    expect(point.text).toContain("style: Manrope 15 px / 600 · #ffffff on #6a3577 · corners 8px");
    const pins: DesignPin[] = [{ n: 1, el, fx: 0.5, fy: 0.5, comment: "Make it stand out" }];
    expect(pinsAttachment("design/pricing.html", pins, 1440)!.text).toContain(SCOPE_RULE);
  });

  it("a mark names what was drawn, its area, the parts under it as selectors, and its picture", () => {
    const el = asElement(reported)!;
    const mark = markAttachment("design/pricing.html", {
      note: "too much space here",
      kinds: ["box", "text"],
      area: { x: 0, y: 520, w: 1440, h: 80 },
      width: 1440,
      under: [el],
      pictured: "inked",
    });
    expect(mark.name).toBe("design/pricing.html · a mark");
    expect(mark.text).toContain("The person drew a box and words on design/pricing.html and wrote: “too much space here”.");
    expect(mark.text).toContain("area: x 0–1440, y 520–600 on the page, in a 1440 px window");
    expect(mark.text).toContain("picture: attached beside this, the area with the ink on it");
    // The harness reads `selector:` lines to know what was pointed at (phase 3d).
    expect(mark.text).toContain("selector: main > div.plan:nth-of-type(2) > button.cta");
    const blind = markAttachment("p.html", { note: "", kinds: ["pen"], area: { x: 1, y: 2, w: 3, h: 4 }, width: 390, under: [], pictured: "none" });
    expect(blind.text).toContain("picture: none could be taken");
  });

  it("a mark's area is its ink with room around it, never past the page's top or left", () => {
    expect(markBounds([])).toBeNull();
    expect(markBounds([{ kind: "box", x: 100, y: 200, w: 50, h: 20 }])).toEqual({ x: 84, y: 184, w: 82, h: 52 });
    expect(markBounds([{ kind: "pen", points: [[4, 6], [30, 9]] }])).toEqual({ x: 0, y: 0, w: 46, h: 25 });
  });
});

describe("the comment box and the dock", () => {
  const pin = (n: number, more: Partial<DesignPin> = {}): DesignPin => ({
    n,
    el: asElement(reported)!,
    fx: 0.5,
    fy: 0.5,
    comment: "",
    ...more,
  });

  it("the box is typed in where the pin fell, sends with Ctrl+Enter and cancels with Esc", () => {
    const onChange = vi.fn();
    const onSend = vi.fn();
    const onCancel = vi.fn();
    const { rerender } = render(
      <CommentPopover pin={pin(1)} left={10} top={20} onChange={onChange} onSend={onSend} onKeep={vi.fn()} onCancel={onCancel} />,
    );
    expect(document.activeElement).toBe(screen.getByTestId("design-comment-input"));
    expect((screen.getByTestId("design-comment-send") as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByTestId("design-comment-input"), { target: { value: "Bigger" } });
    expect(onChange).toHaveBeenCalledWith("Bigger");
    rerender(<CommentPopover pin={pin(1, { comment: "Bigger" })} left={10} top={20} onChange={onChange} onSend={onSend} onKeep={vi.fn()} onCancel={onCancel} />);
    fireEvent.keyDown(screen.getByTestId("design-comment-input"), { key: "Enter", ctrlKey: true });
    expect(onSend).toHaveBeenCalled();
    fireEvent.keyDown(screen.getByTestId("design-comment-input"), { key: "Escape" });
    expect(onCancel).toHaveBeenCalled();
  });

  it("the dock sends the comments picked, or all with words, and says which no longer have a place", () => {
    const onSend = vi.fn();
    const pins = [pin(1, { comment: "Bigger" }), pin(2, { comment: "Shorter", lost: true }), pin(3, { comment: "Done", sent: true }), pin(4)];
    render(<CommentsDock pins={pins} onChange={vi.fn()} onRemove={vi.fn()} onSend={onSend} onClose={vi.fn()} />);
    expect(screen.getByTestId("design-comments-send").textContent).toBe("Put all 2 in your message");
    expect(screen.getByTestId("design-comment-2").textContent).toContain("No longer on the page");
    expect(screen.getByTestId("design-comment-3").textContent).toContain("In your message");
    fireEvent.click(screen.getByLabelText("Pick comment 2"));
    expect(screen.getByTestId("design-comments-send").textContent).toBe("Put 1 in your message");
    fireEvent.click(screen.getByTestId("design-comments-send"));
    expect(onSend).toHaveBeenCalledWith([2]);
  });
});
