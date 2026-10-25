// Where the anchored panel opens: under its trigger, or above it when the window has no room
// below and room above (a delivered file's menu sits just over the composer, 2026-10-13).
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { useRef } from "react";
import { AnchoredPopover } from "./AnchoredPopover";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function Harness({ triggerTop }: { triggerTop: number }) {
  const anchor = useRef<HTMLButtonElement>(null);
  return (
    <>
      <button ref={anchor} data-top={triggerTop}>
        open
      </button>
      <AnchoredPopover anchorRef={anchor} open onClose={() => {}} width={200} label="Actions" testId="panel" className="p">
        <span>row</span>
      </AnchoredPopover>
    </>
  );
}

function place(triggerTop: number) {
  // jsdom lays nothing out: the trigger's box and the panel's height are given.
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
    const top = Number(this.getAttribute("data-top") ?? 0);
    return { top, bottom: top + 28, left: 600, right: 628, width: 28, height: 28, x: 600, y: top, toJSON: () => ({}) } as DOMRect;
  });
  vi.spyOn(HTMLElement.prototype, "offsetHeight", "get").mockReturnValue(120);
  Object.assign(window, { innerHeight: 800, innerWidth: 1200 });
  render(<Harness triggerTop={triggerTop} />);
  return screen.getByTestId("panel").style;
}

describe("AnchoredPopover placement", () => {
  it("opens under the trigger when there is room", () => {
    expect(place(100).top).toBe("136px");
  });

  it("opens above the trigger when the room below is too short", () => {
    const style = place(700);
    expect(style.top).toBe(`${700 - 8 - 120}px`);
    expect(style.maxHeight).toBe("120px");
  });
});
