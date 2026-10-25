// The delivery row's geometry, pinned against the stylesheet SOURCE — the same kind of pin
// The reference design keeps for its turn tail (`turn-tail-spacing.client.spec.ts`), and for the same reason:
// these are the numbers that make a row read as a delivery rather than as a list item, and
// none of them is observable in jsdom (no layout).
//
// The menu escaping the row was a source pin here (`position: absolute`, no `overflow:
// hidden`). The turn around the row clipped it anyway (`content-visibility: auto`, owner-hit
// 2026-10-13), so the menu is portaled now and that is pinned by behaviour in
// Deliverables.test.tsx; the overflow pin stays, since a clipping row still costs the focus ring.

import { describe, expect, it } from "vitest";
// `?raw` rather than `node:fs`: this package's TypeScript config has no Node types (it is a
// browser build), and Vite already hands a test the file's bytes this way.
import css from "../../styles.css?raw";

/** The declarations inside one rule, by its exact selector. */
function rule(selector: string): string {
  const start = css.indexOf(`\n${selector} {`);
  expect(start, `no rule for ${selector}`).toBeGreaterThan(-1);
  return css.slice(start, css.indexOf("}", start));
}

describe("delivery row geometry", () => {
  it("is a 60px row with a 40px tile and 8/10 padding (the reference design's card)", () => {
    const row = rule(".deliverable");
    expect(row).toContain("height: 60px");
    expect(row).toContain("padding: 8px 10px");
    expect(rule(".deliverable-tile")).toContain("width: 40px");
    expect(rule(".deliverable-tile")).toContain("height: 40px");
    // The mark alone: no grey tile around it (owner, 2026-10-13).
    expect(rule(".deliverable-tile")).not.toContain("background:");
    expect(rule(".deliverable-tile")).not.toContain("border:");
    expect(rule(".deliverable-name")).toContain("font-size: var(--fs-ui)");
  });

  it("lets the action menu escape the row", () => {
    expect(rule(".deliverable")).not.toContain("overflow: hidden");
    // Placed by AnchoredPopover, never by the stylesheet against the row.
    expect(rule(".deliverable-popover")).not.toContain("position: absolute");
  });

  it("pairs rows in two columns, one when a single file was delivered", () => {
    expect(rule(".deliverable-grid")).toContain("grid-template-columns: repeat(2, minmax(0, 1fr))");
    expect(css).toContain(".deliverable-grid[data-single] { grid-template-columns: minmax(0, 1fr); }");
  });

  it("swaps the description for the affordance on hover", () => {
    expect(css).toContain(".deliverable:hover .deliverable-desc { display: none; }");
    expect(css).toContain(".deliverable:hover .deliverable-hint { display: inline; }");
  });
});
