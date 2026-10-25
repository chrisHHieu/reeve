import { describe, expect, it } from "vitest";
import { hunkCaption, parsePatch, withGaps } from "./parsePatch";

const patch = (...lines: string[]) => parsePatch(lines);
const hunks = (rows: ReturnType<typeof withGaps>) => rows.filter((r) => r.kind === "hunk");

describe("withGaps", () => {
  it("counts the unchanged lines above the first hunk and between two hunks, on the hunk", () => {
    const rows = withGaps(
      patch(
        "@@ -38,3 +38,3 @@ class A:",
        " a",
        "-b",
        "+c",
        " d",
        "@@ -88,2 +88,2 @@ def f():",
        " x",
        "-y",
        "+z",
      ),
    );
    // 37 lines above line 38; the first hunk ends at old line 40, the next starts at 88.
    expect(hunks(rows).map((h) => h.count)).toEqual([37, 47]);
    expect(hunks(rows).map((h) => h.label)).toEqual(["class A:", "def f():"]);
    // The raw header stays as the row's text: the hunk actions identify the hunk by it.
    expect(hunks(rows)[0].text).toBe("@@ -38,3 +38,3 @@ class A:");
    expect(hunkCaption(hunks(rows)[0])).toBe("37 unmodified lines  \u00b7  class A:");
    expect(hunkCaption(hunks(rows)[0], false)).toBe("class A:");
  });

  it("says nothing when the hunk starts the file or follows the last one directly", () => {
    const rows = withGaps(patch("@@ -1,2 +1,2 @@", " a", "-b", "+c", "@@ -3,1 +3,1 @@", "-d", "+e"));
    expect(hunks(rows).map((h) => hunkCaption(h))).toEqual(["", ""]);
  });

  it("reads an insertion's header (-a,0) as after line a, and one line as singular", () => {
    expect(hunks(withGaps(patch("@@ -5,0 +6,1 @@", "+new")))[0].count).toBe(5);
    const one = hunks(withGaps(patch("@@ -2,1 +2,1 @@", "-a", "+b")))[0];
    expect(hunkCaption(one)).toBe("1 unmodified line");
  });
});
