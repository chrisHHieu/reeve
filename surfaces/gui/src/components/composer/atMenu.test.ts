// The "@" list (atMenu.ts): one list of the session's files, filtered as it is typed, with
// folders, and what the conversation touched first.

import { describe, expect, it } from "vitest";
import { atMenuRows, foldersOf, matchIn, pointersIn, touchedFilesOf } from "./atMenu";

const FILES = ["README.md", "cli/export.py", "cli/exporters/csv_exp.py", "docs/API.md", "docs/export-formats.md", "tests/test_export.py"];

describe("atMenuRows", () => {
  it("a bare @ is what the conversation touched, then the top of the folder, folders first", () => {
    const rows = atMenuRows(FILES, "", [
      { path: "cli/export.py", what: "changed" },
      { path: "cli/export.py", what: "read" },
      { path: "tests/test_export.py", what: "read" },
    ]);
    expect(rows.map((r) => `${r.kind}:${r.path}`)).toEqual([
      "touched:cli/export.py",
      "touched:tests/test_export.py",
      "dir:cli/",
      "dir:docs/",
      "dir:tests/",
      "file:README.md",
    ]);
    expect(rows[2]).toMatchObject({ count: 2 });
  });

  it("a folder's slash lists what is inside it, the folder itself first", () => {
    const rows = atMenuRows(FILES, "cli/");
    expect(rows.map((r) => `${r.kind}:${r.path}`)).toEqual(["here:cli/", "dir:cli/exporters/", "file:cli/export.py"]);
  });

  it("anything else searches every name and path: starts with, then contains, then the path", () => {
    const rows = atMenuRows(FILES, "exp");
    expect(rows.map((r) => r.path)).toEqual([
      "cli/export.py",
      "cli/exporters/",
      "docs/export-formats.md",
      // Both only contain it: the shorter path first.
      "tests/test_export.py",
      "cli/exporters/csv_exp.py",
    ]);
  });

  it("finds a folder by name", () => {
    expect(atMenuRows(FILES, "doc")[0]).toEqual({ kind: "dir", path: "docs/", count: 2 });
  });
});

describe("the helpers", () => {
  it("counts the files under every folder", () => {
    expect(foldersOf(FILES).get("cli/")).toBe(2);
    expect(foldersOf(FILES).get("cli/exporters/")).toBe(1);
  });

  it("marks where the typed text matches a name", () => {
    expect(matchIn("test_export.py", "EXP")).toEqual(["test_", "exp", "ort.py"]);
    expect(matchIn("README.md", "zzz")).toBeNull();
  });

  it("reads the files and folders a draft points at", () => {
    expect(pointersIn("compare @cli/export.py with @docs/ and @nothing", FILES)).toEqual(["cli/export.py", "docs/"]);
  });
});

describe("touchedFilesOf", () => {
  it("lists what the agent read or changed, newest first, under the folder", () => {
    const items = [
      { kind: "tool", name: "read_file", args: { path: "D:/work/app/cli/export.py" }, status: "ok" },
      { kind: "tool", name: "run_shell", args: { command: "pytest" }, status: "ok" },
      { kind: "tool", name: "edit_file", args: { path: "D:\\work\\app\\tests\\test_export.py" }, status: "ok" },
      { kind: "tool", name: "read_file", args: { path: "D:/work/app/tests/test_export.py" }, status: "ok" },
      { kind: "tool", name: "read_file", args: { path: "missing.md" }, status: "error" },
      { kind: "user" },
    ];
    expect(touchedFilesOf(items, "D:\\work\\app")).toEqual([
      // Read last, but changed before that: a change outranks a read.
      { path: "tests/test_export.py", what: "changed" },
      { path: "cli/export.py", what: "read" },
    ]);
  });
});
