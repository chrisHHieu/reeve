// @vitest-environment jsdom
// Designs and a design's own tab: which paths are design pages, one tab per page
// however its path is spelled, Designs a singleton, and both kept across a reload.
import { afterEach, describe, expect, it } from "vitest";
import {
  closeRailTab,
  designKey,
  isDesignPath,
  openDesignTab,
  openRailTab,
  railTabTitle,
  readStoredRailTabs,
  reopenRailTab,
  samePage,
  seedRailTabs,
  writeStoredRailTabs,
} from "./tabs";
import { draggedWidth, widthOptions, DRAG_MAX, DRAG_MIN } from "./DesignView";
import { groupDesigns } from "./DesignsView";
import type { DesignFile } from "../../api";

afterEach(() => localStorage.clear());

describe("design tabs", () => {
  it("knows a design page: a page under a design folder, at any depth, either slash", () => {
    expect(isDesignPath("design/pricing.html")).toBe(true);
    expect(isDesignPath("web\\Design\\screens\\login.htm")).toBe(true);
    expect(isDesignPath("C:/work/app/design/a.html")).toBe(true);
    expect(isDesignPath("design/notes.md")).toBe(false);
    expect(isDesignPath("designs/pricing.html")).toBe(false);
    expect(isDesignPath("pricing.html")).toBe(false);
  });

  it("knows two spellings of one page: equal, or one the tail of the other at a folder line", () => {
    expect(samePage("C:\\ws\\Pricing\\a.html", "pricing/a.html")).toBe(true);
    expect(samePage("pricing/a.html", "./pricing/a.html")).toBe(true);
    expect(samePage("other-pricing/a.html", "pricing/a.html")).toBe(false);
  });

  it("opens one tab per page however its path is spelled, replacing the guide, named by the file", () => {
    const first = openDesignTab(seedRailTabs(), "design/pricing.html");
    expect(first.tabs.map((tab) => tab.kind)).toEqual(["design"]);
    expect(railTabTitle(first.tabs[0])).toBe("pricing.html");
    const again = openDesignTab(first.tabs, ".\\design\\Pricing.html");
    expect(again.tabs).toHaveLength(1);
    expect(again.activeId).toBe(first.activeId);
    const second = openDesignTab(again.tabs, "design/pricing.a.html");
    expect(second.tabs.map((tab) => tab.path)).toEqual(["design/pricing.html", "design/pricing.a.html"]);
    expect(second.activeId).toBe(`design:${designKey("design/pricing.a.html")}`);
  });

  it("keeps Designs a singleton and puts a closed design back as the same page", () => {
    const designs = openRailTab(seedRailTabs(), "designs");
    expect(railTabTitle(designs.tabs[0])).toBe("Designs");
    expect(openRailTab(designs.tabs, "designs").tabs).toHaveLength(1);
    const page = openDesignTab(designs.tabs, "design/a.html");
    const closed = closeRailTab(page.tabs, page.activeId, page.activeId);
    expect(closed.tabs.map((tab) => tab.kind)).toEqual(["designs"]);
    const back = reopenRailTab(closed.tabs, page.tabs[1]);
    expect(back.tabs.map((tab) => tab.path ?? tab.kind)).toEqual(["designs", "design/a.html"]);
  });

  it("comes back after a reload with its pages; a design tab without a path does not", () => {
    const state = openDesignTab(openRailTab(seedRailTabs(), "designs").tabs, "design/a.html");
    writeStoredRailTabs("s1", state);
    expect(readStoredRailTabs("s1")).toEqual(state);
    localStorage.setItem("reeve:rail-tabs:v1:s2", JSON.stringify({ tabs: [{ id: "design:x", kind: "design" }], activeId: "design:x" }));
    expect(readStoredRailTabs("s2")).toBeNull();
  });
});

describe("a design's width", () => {
  it("grows by twice the drag (the page is centred), in the page's own px, within bounds", () => {
    expect(draggedWidth(800, 50, 1)).toBe(900);
    expect(draggedWidth(1440, 50, 0.5)).toBe(1640);
    expect(draggedWidth(400, -500, 1)).toBe(DRAG_MIN);
    expect(draggedWidth(2400, 900, 1)).toBe(DRAG_MAX);
  });

  it("lists Desktop (the panel's width), Tablet and Mobile, and a dragged width only when there is one", () => {
    expect(widthOptions("fit").map((o) => [o.value, o.label, o.icon])).toEqual([
      ["fit", "Desktop", "monitor"],
      ["834", "Tablet", "tablet"],
      ["390", "Mobile", "smartphone"],
    ]);
    expect(widthOptions(834)).toHaveLength(3);
    expect(widthOptions(1000).pop()).toEqual({ value: "1000", label: "1000 px", icon: "tablet" });
    expect(widthOptions(1440).pop()).toEqual({ value: "1440", label: "1440 px", icon: "monitor" });
  });
});

describe("the Designs board", () => {
  const file = (path: string, more: Partial<DesignFile> = {}): DesignFile => ({
    path,
    name: path.split("/").pop()!,
    mtime_ms: 0,
    checks: 0,
    summary: "",
    issues: 0,
    ...more,
  });

  it("hangs a page's variants on its card, in letter order; a variant without its page is a card", () => {
    const cards = groupDesigns([
      file("design/pricing.b.html", { variant_of: "design/pricing.html", letter: "b" }),
      file("design/pricing.html"),
      file("design/pricing.a.html", { variant_of: "design/pricing.html", letter: "a" }),
      file("design/hero.a.html", { variant_of: "design/hero.html", letter: "a" }),
    ]);
    expect(cards.map((card) => card.file.path)).toEqual(["design/pricing.html", "design/hero.a.html"]);
    expect(cards[0].variants.map((v) => v.letter)).toEqual(["a", "b"]);
  });
});
