import { describe, expect, it } from "vitest";
import { fixAttachment, reviewHeading } from "./DesignReview";
import { openDesignAppTab, railTabTitle, readStoredRailTabs, writeStoredRailTabs } from "./tabs";

describe("DesignReview", () => {
  it("says what is open in words, kept ones aside", () => {
    expect(reviewHeading([])).toBe("The same as the design in what was measured");
    expect(
      reviewHeading([
        { id: "a", kind: "fix", text: "x" },
        { id: "b", kind: "check", text: "y" },
        { id: "c", kind: "accepted", text: "z" },
      ]),
    ).toBe("1 to fix, 1 to check");
    expect(reviewHeading([{ id: "c", kind: "accepted", text: "z" }])).toBe("The same as the design in what was measured");
  });

  it("asks the agent to fix one difference and compare again", () => {
    const ask = fixAttachment("design/pricing.html", "http://localhost:5173/pricing", { id: "order", kind: "fix", text: "The parts are in another order" });
    expect(ask.name).toBe("pricing.html · difference");
    const text = ask.kind === "text" ? ask.text : "";
    expect(text).toContain("http://localhost:5173/pricing");
    expect(text).toContain("- The parts are in another order");
    expect(text).toContain("compare_design again");
  });

  it("opens one review tab per page, keeps its address, and survives a reload", () => {
    const first = openDesignAppTab([{ id: "guide", kind: "guide" }], "design/pricing.html", "http://localhost:5173/pricing");
    expect(first.tabs).toEqual([{ id: "design-app:design/pricing.html", kind: "design-app", path: "design/pricing.html", url: "http://localhost:5173/pricing" }]);
    expect(railTabTitle(first.tabs[0])).toBe("pricing.html · app");
    const moved = openDesignAppTab(first.tabs, "design/pricing.html", "http://localhost:3000/pricing");
    expect(moved.tabs).toHaveLength(1);
    expect(moved.tabs[0].url).toBe("http://localhost:3000/pricing");
    writeStoredRailTabs("s-review", moved);
    expect(readStoredRailTabs("s-review")?.tabs[0]).toMatchObject({ kind: "design-app", url: "http://localhost:3000/pricing" });
  });
});

import { rowSpec } from "../../cards/registry";

describe("a compare_design row", () => {
  const item = (status: string, args: Record<string, unknown>) =>
    ({ kind: "tool", id: "t1", name: "compare_design", args, status, preview: "[design compare] … 2 to fix." }) as Parameters<typeof rowSpec>[0];

  it("offers the review once it finished, with the page and the app's address", () => {
    expect(rowSpec(item("ok", { url: "http://localhost:5173/pricing", page: "design/pricing.html" })).designCompare).toEqual({
      page: "design/pricing.html",
      url: "http://localhost:5173/pricing",
    });
  });

  it("offers nothing while it runs, after it failed, or for another tool", () => {
    expect(rowSpec(item("running", { url: "http://localhost:5173/", page: "design/a.html" })).designCompare).toBeUndefined();
    expect(rowSpec(item("error", { url: "http://localhost:5173/", page: "design/a.html" })).designCompare).toBeUndefined();
    expect(rowSpec({ ...item("ok", { url: "u", page: "p" }), name: "check_design" }).designCompare).toBeUndefined();
  });
});
