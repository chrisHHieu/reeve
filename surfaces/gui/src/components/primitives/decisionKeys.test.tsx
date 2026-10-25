import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { ScrollFade, scrollFadeState } from "./ScrollFade";
import { useDecisionKeyboard } from "./decisionKeys";

afterEach(cleanup);

describe("scrollFadeState", () => {
  it("is silent when there is nothing behind an edge", () => {
    // A body that fits cannot scroll: no fade, however the numbers round.
    expect(scrollFadeState(0, 100, 100)).toBe("none");
    expect(scrollFadeState(0, 96, 100)).toBe("none");
  });

  it("fades the edge that hides content", () => {
    // 300px of content in a 100px window.
    expect(scrollFadeState(0, 300, 100)).toBe("bottom");
    expect(scrollFadeState(100, 300, 100)).toBe("both");
    expect(scrollFadeState(200, 300, 100)).toBe("top");
  });

  it("tolerates the sub-pixel scroll heights browsers report", () => {
    expect(scrollFadeState(0.4, 300.6, 100)).toBe("bottom");
    expect(scrollFadeState(200.4, 300.4, 100)).toBe("top");
  });
});

describe("ScrollFade", () => {
  it("renders its content in a scroller that starts out silent", () => {
    render(
      <ScrollFade className="rv-scroll" data-testid="body">
        <div>line one</div>
      </ScrollFade>,
    );
    const body = screen.getByTestId("body");
    expect(body.className).toContain("rv-scroll");
    expect(body.getAttribute("data-fade")).toBe("none");
    expect(body.textContent).toContain("line one");
  });
});

describe("useDecisionKeyboard", () => {
  function Dock({
    onPick,
    onDismiss,
  }: {
    onPick?: (n: string) => void;
    onDismiss?: () => void;
  }) {
    const [container, setContainer] = useState<HTMLDivElement | null>(null);
    useDecisionKeyboard({ current: container }, { onDismiss });
    return (
      <div ref={setContainer} data-testid="dock">
        <button data-option onClick={() => onPick?.("first")}>
          Allow
        </button>
        <button data-option onClick={() => onPick?.("second")}>
          Always allow
        </button>
        <button data-option onClick={() => onPick?.("deny")}>
          Deny
        </button>
      </div>
    );
  }

  it("selects the first option on mount, without a pointer", () => {
    render(<Dock />);
    const buttons = screen.getAllByRole("button");
    expect(buttons[0].hasAttribute("data-kbd")).toBe(true);
    expect(buttons[1].hasAttribute("data-kbd")).toBe(false);
  });

  it("moves the selection with the arrows, wrapping at both ends", () => {
    render(<Dock />);
    const dock = screen.getByTestId("dock");
    fireEvent.keyDown(dock, { key: "ArrowDown" });
    expect(screen.getAllByRole("button")[1].hasAttribute("data-kbd")).toBe(true);
    fireEvent.keyDown(dock, { key: "ArrowUp" });
    fireEvent.keyDown(dock, { key: "ArrowUp" });
    // Wrapped from the first option to the last.
    expect(screen.getAllByRole("button")[2].hasAttribute("data-kbd")).toBe(true);
  });

  it("answers with a digit, immediately", () => {
    const picked: string[] = [];
    render(<Dock onPick={(n) => picked.push(n)} />);
    fireEvent.keyDown(screen.getByTestId("dock"), { key: "2" });
    expect(picked).toEqual(["second"]);
  });

  it("confirms the selection with Enter", () => {
    const picked: string[] = [];
    render(<Dock onPick={(n) => picked.push(n)} />);
    const dock = screen.getByTestId("dock");
    fireEvent.keyDown(dock, { key: "ArrowDown" });
    fireEvent.keyDown(dock, { key: "Enter" });
    expect(picked).toEqual(["second"]);
  });

  it("dismisses with Escape only when the surface offers one", () => {
    let dismissed = 0;
    const { unmount } = render(<Dock onDismiss={() => dismissed++} />);
    fireEvent.keyDown(screen.getByTestId("dock"), { key: "Escape" });
    expect(dismissed).toBe(1);
    unmount();
    // No onDismiss (a plan must be answered): Escape is not the card's to swallow.
    render(<Dock />);
    fireEvent.keyDown(screen.getByTestId("dock"), { key: "Escape" });
    expect(dismissed).toBe(1);
  });

  it("clears the keyboard highlight once the pointer takes over", () => {
    render(<Dock />);
    const dock = screen.getByTestId("dock");
    fireEvent.mouseMove(dock);
    expect(screen.getAllByRole("button")[0].hasAttribute("data-kbd")).toBe(false);
  });
});
