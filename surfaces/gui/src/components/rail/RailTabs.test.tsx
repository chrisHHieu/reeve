// @vitest-environment jsdom
// The rail's tab strip: a chip per open tab (Start is not one), the + menu, the overview when
// a tab can be out of sight or brought back, and the window controls.
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RailTabs } from "./RailTabs";
import type { RailTab } from "./tabs";

afterEach(cleanup);

const tabs: RailTab[] = [
  { id: "review", kind: "review" },
  { id: "browser-1", kind: "browser", url: "http://127.0.0.1:1420/" },
  { id: "terminal", kind: "terminal" },
];

function strip(over: Partial<Parameters<typeof RailTabs>[0]> = {}) {
  const props = {
    tabs,
    activeId: "review",
    onSelect: vi.fn(),
    onClose: vi.fn(),
    onNew: vi.fn(),
    onReorder: vi.fn(),
    recentClosed: [] as RailTab[],
    onReopen: vi.fn(),
    onHide: vi.fn(),
    fullscreen: false,
    onToggleFullscreen: vi.fn(),
    ...over,
  };
  render(<RailTabs {...props} />);
  return props;
}

describe("RailTabs", () => {
  it("renders a chip per open tab, marking the active one, and none for Start", () => {
    strip({ tabs: [{ id: "guide", kind: "guide" }, ...tabs] });
    expect(screen.queryByTestId("rail-tab-guide")).toBeNull();
    expect(screen.getByTestId("rail-tab-review").getAttribute("aria-selected")).toBe("true");
    expect(screen.getByTestId("rail-tab-browser-1").textContent).toContain("127.0.0.1:1420");
    expect(screen.getByTestId("rail-tab-terminal").getAttribute("aria-selected")).toBe("false");
  });

  it("with only Start, the strip holds the + and the window controls", () => {
    strip({ tabs: [{ id: "guide", kind: "guide" }], activeId: "guide" });
    expect(screen.queryAllByRole("tab")).toHaveLength(0);
    expect(screen.getByTestId("rail-new")).toBeTruthy();
    expect(screen.getByTestId("rail-hide")).toBeTruthy();
  });

  it("hands select the tab id; close fades the chip, then hands its id", async () => {
    const props = strip();
    fireEvent.click(screen.getByTestId("rail-tab-terminal"));
    expect(props.onSelect).toHaveBeenCalledWith("terminal");
    fireEvent.click(screen.getByTestId("rail-tab-close-browser-1"));
    expect(screen.getByTestId("rail-tab-browser-1").className).toContain("closing");
    await waitFor(() => expect(props.onClose).toHaveBeenCalledWith("browser-1"));
  });

  it("middle-click closes", async () => {
    const onClose = vi.fn();
    strip({ onClose });
    fireEvent(screen.getByTestId("rail-tab-terminal"), new MouseEvent("auxclick", { bubbles: true, button: 1 }));
    await waitFor(() => expect(onClose).toHaveBeenCalledWith("terminal"));
  });

  it("right-click offers Close / others / all", () => {
    const onClose = vi.fn();
    strip({ onClose });
    fireEvent.contextMenu(screen.getByTestId("rail-tab-terminal"));
    const menu = screen.getByTestId("rail-tab-menu");
    expect(menu.textContent).toContain("Close other tabs");
    fireEvent.click(screen.getByText("Close all tabs"));
    expect(onClose.mock.calls.map((call) => call[0]).sort()).toEqual(["browser-1", "review", "terminal"]);
  });

  it("arrow keys move between tabs and only the open tab is in the Tab order", () => {
    const props = strip();
    expect(screen.getByTestId("rail-tab-review").getAttribute("tabindex")).toBe("0");
    expect(screen.getByTestId("rail-tab-terminal").getAttribute("tabindex")).toBe("-1");
    fireEvent.keyDown(screen.getByTestId("rail-tab-review"), { key: "ArrowRight" });
    expect(props.onSelect).toHaveBeenCalledWith("browser-1");
    fireEvent.keyDown(screen.getByTestId("rail-tab-review"), { key: "End" });
    expect(props.onSelect).toHaveBeenLastCalledWith("terminal");
  });

  it("the + sits directly after the chips, then the fill, the open tab's actions and the window controls", () => {
    const slot = vi.fn();
    strip({ onActionsSlot: slot });
    const order = Array.from(screen.getByTestId("rail-tabs").children).map((el) => el.className);
    expect(order[0]).toContain("rail-chips");
    expect(order[1]).toContain("rail-new-wrap");
    expect(order[2]).toContain("rail-strip-fill");
    expect(order[3]).toContain("rail-actions");
    expect(order[4]).toContain("rail-chrome");
    // The open tab is handed the place for its actions.
    expect(slot).toHaveBeenCalledWith(screen.getByTestId("rail-tabs").children[3]);
  });

  it("dragging a chip onto another reorders them (the dragged id goes where it landed)", () => {
    const onReorder = vi.fn();
    strip({ onReorder });
    const dragged = screen.getByTestId("rail-tab-terminal");
    const target = screen.getByTestId("rail-tab-review");
    fireEvent.dragStart(dragged, { dataTransfer: { setData: vi.fn(), effectAllowed: "" } });
    fireEvent.dragOver(target, { dataTransfer: { dropEffect: "" } });
    expect(onReorder).toHaveBeenCalledWith("terminal", "review");
    onReorder.mockClear();
    fireEvent.dragOver(dragged, { dataTransfer: { dropEffect: "" } });
    expect(onReorder).not.toHaveBeenCalled();
  });

  it("the overview shows only when a tab can come back, lists the open ones and reopens", () => {
    strip();
    expect(screen.queryByTestId("rail-overview")).toBeNull();
    cleanup();
    const props = strip({
      recentClosed: [{ id: "browser-2", kind: "browser", url: "http://localhost:8000/x" }],
    });
    fireEvent.click(screen.getByTestId("rail-overview"));
    const pop = screen.getByTestId("rail-overview-pop");
    expect(pop.textContent).toContain("Recently closed");
    expect(screen.getByTestId("rail-overview-tab-browser-1").textContent).toContain("127.0.0.1:1420");

    fireEvent.change(screen.getByTestId("rail-overview-search"), { target: { value: "local" } });
    expect(screen.queryByTestId("rail-overview-tab-browser-1")).toBeNull();
    expect(screen.getByTestId("rail-overview-recent")).toBeTruthy();

    fireEvent.click(screen.getByTestId("rail-overview-reopen-browser-2"));
    expect(props.onReopen).toHaveBeenCalledWith(expect.objectContaining({ id: "browser-2", kind: "browser" }));
    expect(screen.queryByTestId("rail-overview-pop")).toBeNull();
  });

  it("the + menu lists the kinds in the Start page's order with their facts, and closes on pick", () => {
    const props = strip({ counts: { review: 3 }, folderCount: 2 });
    fireEvent.click(screen.getByTestId("rail-new"));
    const menu = screen.getByTestId("rail-new-menu");
    const items = Array.from(menu.querySelectorAll("[role=menuitem]")).map((el) => el.textContent);
    expect(items).toEqual(["Review3 files", "Files2 folders", "TerminalCtrl `", "Browser", "Designs"]);
    fireEvent.click(screen.getByTestId("rail-new-browser"));
    expect(props.onNew).toHaveBeenCalledWith("browser");
    expect(screen.queryByTestId("rail-new-menu")).toBeNull();
  });

  it("hide and fullscreen defer to the app; there is no split control", () => {
    const props = strip();
    fireEvent.click(screen.getByTestId("rail-hide"));
    expect(props.onHide).toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("rail-fullscreen"));
    expect(props.onToggleFullscreen).toHaveBeenCalled();
    expect(screen.queryByTestId("rail-split")).toBeNull();
  });
});

describe("RailTabs counts", () => {
  it("wears a number beside a tab's name, and says 99+ past ninety-nine", () => {
    strip({ counts: { review: 5 } });
    expect(screen.getByTestId("rail-tab-count-review").textContent).toBe("5");
    expect(screen.queryByTestId("rail-tab-count-terminal")).toBeNull();
    cleanup();
    strip({ counts: { review: 240 } });
    expect(screen.getByTestId("rail-tab-count-review").textContent).toBe("99+");
    cleanup();
    strip({ counts: { review: 0 } });
    expect(screen.queryByTestId("rail-tab-count-review")).toBeNull();
  });
});

describe("RailTabs open-tab pill", () => {
  it("follows the open tab when its name grows after it opened", () => {
    // A browser tab opens on its host, then its page names itself: the chip grows. The pill was
    // measured only when the open tab or the set of tabs changed, so the close button stood
    // outside it until another click (owner's screenshot, 2026-10-20).
    const observers: Array<() => void> = [];
    vi.stubGlobal(
      "ResizeObserver",
      class {
        constructor(private callback: () => void) {
          observers.push(() => this.callback());
        }
        observe() {}
        disconnect() {}
      },
    );
    const width = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "offsetWidth");
    Object.defineProperty(HTMLElement.prototype, "offsetWidth", {
      configurable: true,
      get(this: HTMLElement) {
        return this.dataset.tabId ? (this.textContent ?? "").length * 8 : 0;
      },
    });
    try {
      const browser: RailTab[] = [{ id: "browser-1", kind: "browser", url: "https://id.example.com/login" }];
      const props = {
        tabs: browser,
        activeId: "browser-1",
        onSelect: vi.fn(),
        onClose: vi.fn(),
        onNew: vi.fn(),
        onReorder: vi.fn(),
        recentClosed: [] as RailTab[],
        onReopen: vi.fn(),
        onHide: vi.fn(),
        fullscreen: false,
        onToggleFullscreen: vi.fn(),
      };
      const { container, rerender } = render(<RailTabs {...props} />);
      const pill = () => (container.querySelector(".rail-rule") as HTMLElement).style.width;
      const before = pill();
      rerender(<RailTabs {...props} titles={{ "browser-1": "Example account settings" }} />);
      act(() => observers.forEach((fire) => fire()));
      expect(pill()).not.toBe(before);
      expect(pill()).toBe(`${screen.getByTestId("rail-tab-browser-1").textContent!.length * 8}px`);
    } finally {
      if (width) Object.defineProperty(HTMLElement.prototype, "offsetWidth", width);
      vi.unstubAllGlobals();
    }
  });
});
