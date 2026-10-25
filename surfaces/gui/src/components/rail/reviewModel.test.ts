import { describe, expect, it } from "vitest";
import {
  defaultScope,
  isViewed,
  lastTurnChanges,
  mergeChanges,
} from "./reviewModel";

describe("mergeChanges", () => {
  it("shows a file git lists twice (staged, then edited again) as one partly staged file", () => {
    const [file] = mergeChanges([
      { path: "a.py", status: "M", add: 2, del: 1, staged: true },
      { path: "a.py", status: "M", add: 1, del: 0 },
    ]);
    expect(file).toMatchObject({ path: "a.py", add: 3, del: 1, staged: "some", signature: "M:3:1" });
  });

  it("puts the person's code first and tool-generated files last", () => {
    const files = mergeChanges([
      { path: "src/__pycache__/x.pyc", status: "A", add: 0, del: 0 },
      { path: "z.py", status: "M", add: 1, del: 0 },
      { path: "a.py", status: "M", add: 1, del: 0 },
    ]);
    expect(files.map((f) => f.path)).toEqual(["a.py", "z.py", "src/__pycache__/x.pyc"]);
    expect(files[2].generated).toBe(true);
  });
});

describe("a Viewed mark", () => {
  it("stops applying when the file's change moves", () => {
    const [file] = mergeChanges([{ path: "a.py", status: "M", add: 2, del: 1 }]);
    expect(isViewed({ "a.py": file.signature }, file)).toBe(true);
    const [edited] = mergeChanges([{ path: "a.py", status: "M", add: 3, del: 1 }]);
    expect(isViewed({ "a.py": file.signature }, edited)).toBe(false);
  });
});

describe("scopes", () => {
  it("opens git's working tree in a git folder, Reeve's work elsewhere, whatever the counts", () => {
    expect(defaultScope(true)).toBe("uncommitted");
    expect(defaultScope(false)).toBe("session");
  });

  it("the last turn is the newest turn's changes", () => {
    const rows = [
      { path: "a", turn: 1 },
      { path: "b", turn: 3 },
      { path: "c", turn: 3 },
    ] as never[];
    expect(lastTurnChanges(rows).map((c: { path: string }) => c.path)).toEqual(["b", "c"]);
    expect(lastTurnChanges([])).toEqual([]);
  });
});
