import { describe, expect, it } from "vitest";
import { activeGuide, codeShape, foldRange } from "./codeBlocks";

const PY = [
  "class AutomationMixin:", // 0
  "    def approver(self):", // 1
  "        if allowed:", // 2
  "            return ONCE", // 3
  "", // 4  a gap inside the method
  "        return outcome", // 5
  "", // 6  a gap between methods
  "    def seed(self):", // 7
  "        pass", // 8
  "", // 9
  "print(1)", // 10
];

describe("codeShape", () => {
  it("reads the step the file indents by and each line's depth", () => {
    const shape = codeShape(PY);
    expect(shape.unit).toBe(4);
    expect(shape.levels).toEqual([0, 1, 2, 3, 2, 2, 1, 1, 2, 0, 0]);
  });

  it("carries a guide through a gap inside a block and stops it at one between blocks", () => {
    const { levels } = codeShape(PY);
    // Line 4 sits between depth 3 and depth 2: the method's guides run through it.
    expect(levels[4]).toBe(2);
    // Line 9 sits between the class body and top-level code: nothing runs through it.
    expect(levels[9]).toBe(0);
  });

  it("reads two-space and tab files", () => {
    expect(codeShape(["a {", "  b {", "    c", "  }", "}"]).levels).toEqual([0, 1, 2, 1, 0]);
    const tabs = codeShape(["fn() {", "\tif x {", "\t\ty", "\t}", "}"]);
    expect(tabs.unit).toBe(4);
    expect(tabs.levels).toEqual([0, 1, 2, 1, 0]);
  });
});

describe("foldRange", () => {
  const shape = codeShape(PY);

  it("folds a header's body up to its last non-blank line", () => {
    expect(foldRange(PY, shape, 0)).toEqual({ from: 1, to: 8 });
    expect(foldRange(PY, shape, 1)).toEqual({ from: 2, to: 5 });
    expect(foldRange(PY, shape, 2)).toEqual({ from: 3, to: 3 });
  });

  it("offers no fold on a line that opens nothing", () => {
    expect(foldRange(PY, shape, 3)).toBeNull();
    expect(foldRange(PY, shape, 4)).toBeNull();
    expect(foldRange(PY, shape, 10)).toBeNull();
  });
});

describe("activeGuide", () => {
  const shape = codeShape(PY);

  it("is the block a header opens", () => {
    expect(activeGuide(PY, shape, 1)).toEqual({ column: 1, from: 2, to: 5 });
  });

  it("is the innermost block a plain line sits in", () => {
    expect(activeGuide(PY, shape, 5)).toEqual({ column: 1, from: 2, to: 5 });
    expect(activeGuide(PY, shape, 8)).toEqual({ column: 1, from: 8, to: 8 });
  });

  it("is nothing on a top-level line that opens nothing", () => {
    expect(activeGuide(PY, shape, 10)).toBeNull();
  });
});
