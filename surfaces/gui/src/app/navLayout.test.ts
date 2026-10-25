import { describe, expect, it } from "vitest";
import { NAV_OVERLAY_BELOW, NAV_RAIL_BELOW, NAV_RAIL_WITH_PANEL_BELOW, navLayout } from "./navLayout";

const base = { preferCollapsed: false, peek: false };

describe("navLayout", () => {
  it("honours the saved preference where there is room for either", () => {
    expect(navLayout({ ...base, viewport: 1600 })).toEqual({ auto: false, collapsed: false, overlay: false });
    expect(navLayout({ ...base, viewport: 1600, preferCollapsed: true }).collapsed).toBe(true);
  });

  it("is the rail in a narrow window whatever the preference said", () => {
    const layout = navLayout({ ...base, viewport: NAV_RAIL_BELOW - 1 });
    expect(layout).toMatchObject({ auto: true, collapsed: true });
    // ...including a preference of "expanded": a small window is not a decision to widen
    expect(navLayout({ ...base, viewport: 900, preferCollapsed: false }).collapsed).toBe(true);
  });

  it("keeps the sidebar on a laptop: the list of conversations is the primary navigation", () => {
    expect(navLayout({ ...base, viewport: 1280 }).collapsed).toBe(false);
    expect(navLayout({ ...base, viewport: NAV_RAIL_BELOW }).collapsed).toBe(false);
  });

  it("lets the reader expand it anyway, beside the page while there is room", () => {
    const peeked = navLayout({ ...base, viewport: 1000, peek: true });
    expect(peeked).toEqual({ auto: true, collapsed: false, overlay: false });
  });

  it("covers the page instead of squeezing it on a phone-width window", () => {
    expect(navLayout({ ...base, viewport: NAV_OVERLAY_BELOW - 1, peek: true }).overlay).toBe(true);
    // the rail itself is never an overlay
    expect(navLayout({ ...base, viewport: 400 }).overlay).toBe(false);
  });

  it("in a tight window the reader's peek decides, not the saved collapse", () => {
    expect(navLayout({ ...base, viewport: 1000, preferCollapsed: true, peek: true }).collapsed).toBe(false);
  });

  it("folds the queue on a laptop while the side panel is open, and only then", () => {
    expect(navLayout({ ...base, viewport: 1440, panelOpen: true })).toEqual({ auto: true, collapsed: true, overlay: false });
    expect(navLayout({ ...base, viewport: 1280, panelOpen: true }).collapsed).toBe(true);
    expect(navLayout({ ...base, viewport: 1440, panelOpen: false }).collapsed).toBe(false);
    // a big window keeps the queue beside the panel
    expect(navLayout({ ...base, viewport: NAV_RAIL_WITH_PANEL_BELOW, panelOpen: true }).collapsed).toBe(false);
  });

  it("covers the page when the reader expands the queue folded for the panel", () => {
    expect(navLayout({ ...base, viewport: 1440, panelOpen: true, peek: true })).toEqual({ auto: true, collapsed: false, overlay: true });
    expect(navLayout({ ...base, viewport: 1000, panelOpen: true, peek: true }).overlay).toBe(true);
  });
});
