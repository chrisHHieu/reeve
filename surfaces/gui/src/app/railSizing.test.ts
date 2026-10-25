// @vitest-environment jsdom
import { afterEach, describe, expect, it } from "vitest";
import {
  CHAT_WIDTH_MIN,
  chatWidthMin,
  PANEL_WIDTH_MIN,
  PANEL_WIDTH_MIN_SPLIT,
  clampPanelWidth,
  clampTreeWidth,
  clearStoredSize,
  defaultPanelWidth,
  panelWidthMax,
  workPanelWidth,
  panelWidthMin,
  readStoredSize,
  writeStoredSize,
} from "./railSizing";

describe("defaultPanelWidth", () => {
  it("mirrors the CSS fallback clamp(480px, 44vw, 720px)", () => {
    expect(defaultPanelWidth(1920)).toBe(720); // 844 > cap
    expect(defaultPanelWidth(1024)).toBe(PANEL_WIDTH_MIN); // 451 < min
    expect(defaultPanelWidth(1600)).toBe(704); // 44vw in range
  });

  it("gives a laptop a smaller share, so the conversation keeps its room", () => {
    expect(defaultPanelWidth(1366)).toBe(546); // 40vw
    expect(defaultPanelWidth(1280)).toBe(512);
    expect(defaultPanelWidth(1440)).toBe(576);
    expect(defaultPanelWidth(1441)).toBe(634); // back to 44vw just past the laptop tier
  });
});

describe("the work pane's width before a drag (2026-10-13)", () => {
  it("takes all the room the conversation does not need", () => {
    // 1920 beside the 356px nav: the conversation keeps a third, the pane the rest.
    expect(workPanelWidth(1564, 1920)).toBe(1564 - 516);
    // a laptop: the conversation keeps its 450px floor
    expect(workPanelWidth(960, 1280)).toBe(510);
    // never under the pane's own minimum; unmeasured, the vw default
    expect(workPanelWidth(800, 1100)).toBe(PANEL_WIDTH_MIN);
    expect(workPanelWidth(undefined, 1920)).toBe(defaultPanelWidth(1920));
  });
});

describe("panel width bounds", () => {
  it("caps the pane so the conversation keeps its floor: 450px, or a third of a big window", () => {
    expect(panelWidthMax(1000)).toBe(1000 - CHAT_WIDTH_MIN);
    expect(panelWidthMax(600)).toBe(150);
    expect(panelWidthMax(0)).toBe(0);
    // a 1620px workspace keeps 535px for the conversation, not 450px beside a 1170px panel
    expect(chatWidthMin(1620)).toBe(535);
    expect(panelWidthMax(1620)).toBe(1085);
    // ...but never more than the transcript's own column can use
    expect(chatWidthMin(3000)).toBe(720);
  });

  it("reserves 480px for a unified diff and 800px for a split diff", () => {
    expect(panelWidthMin(1700, false)).toBe(PANEL_WIDTH_MIN);
    expect(panelWidthMin(1700, true)).toBe(PANEL_WIDTH_MIN_SPLIT);
  });

  it("yields the pane minimum to the chat minimum on small windows", () => {
    expect(panelWidthMin(700, false)).toBe(panelWidthMax(700));
    expect(panelWidthMin(700, true)).toBe(panelWidthMax(700));
  });
});

describe("clampPanelWidth", () => {
  it("keeps a width already inside the bounds", () => {
    expect(clampPanelWidth({ width: 800, available: 1700, split: false })).toBe(800);
  });

  it("forces the pane down when the window shrinks, never under the chat floor", () => {
    expect(clampPanelWidth({ width: 1600, available: 1700, split: false })).toBe(1139); // 1700 - a third
    expect(clampPanelWidth({ width: 1600, available: 1000, split: true })).toBe(550);
  });

  it("raises the pane to the split minimum when there is room", () => {
    expect(clampPanelWidth({ width: 700, available: 1700, split: true })).toBe(800);
    expect(clampPanelWidth({ width: 700, available: 1000, split: true })).toBe(550);
  });

  it("leaves the width untouched before the row is measured", () => {
    expect(clampPanelWidth({ width: 1600, available: undefined, split: false })).toBe(1600);
    expect(clampPanelWidth({ width: 1600, available: 0, split: false })).toBe(1600);
  });
});

describe("clampTreeWidth", () => {
  it("pins the file tree to the reference design's 240–480px range", () => {
    expect(clampTreeWidth(200)).toBe(240);
    expect(clampTreeWidth(481)).toBe(480);
    expect(clampTreeWidth(300)).toBe(300);
  });
});

describe("stored sizes", () => {
  afterEach(() => clearStoredSize("test:size"));

  it("reads, writes and clears a positive number", () => {
    expect(readStoredSize("test:size")).toBeUndefined();
    writeStoredSize("test:size", 611.4);
    expect(readStoredSize("test:size")).toBe(611);
    clearStoredSize("test:size");
    expect(readStoredSize("test:size")).toBeUndefined();
  });

  it("ignores corrupt or non-positive values", () => {
    localStorage.setItem("test:size", "nope");
    expect(readStoredSize("test:size")).toBeUndefined();
    localStorage.setItem("test:size", "-3");
    expect(readStoredSize("test:size")).toBeUndefined();
  });
});
