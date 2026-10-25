// `parseFileLink` decides, for every markdown link a model writes, whether the reader gets a
// file chip or a browser tab. Both directions cost something real (a dead tab / a file that
// cannot be opened), so the table below is the contract, pinned here.

import { describe, expect, it } from "vitest";
import { parseFileLink } from "./file-link";

describe("parseFileLink", () => {
  it.each([
    ["harness/tools/files.py", { path: "harness/tools/files.py" }],
    ["../docs/My%20Notes.md", { path: "../docs/My Notes.md" }],
    ["src/index.ts#L24", { path: "src/index.ts", line: 24 }],
    ["src/index.ts#L24-L30", { path: "src/index.ts", line: 24 }],
    ["C:/work/file.ts", { path: "C:/work/file.ts" }],
    ["C:%5Cwork%5Cfile.ts", { path: "C:\\work\\file.ts" }],
    ["docs/%E4%B8%AD%E6%96%87.md", { path: "docs/中文.md" }],
    // A `?` or `#` INSIDE a real filename is percent-encoded, so this decodes to one name.
    ["file%23name%3F.txt", { path: "file#name?.txt" }],
    ["./Makefile", { path: "./Makefile" }],
  ])("reads %s as a file destination", (destination, expected) => {
    expect(parseFileLink(destination)).toEqual(expected);
  });

  it.each([
    "https://example.com/a.md",
    "//example.com/file.ts",
    "%5C%5Cserver%5Cshare%5Cfile.ts",
    "javascript:alert(1)",
    "data:text/plain,hi",
    "file:///etc/passwd",
    "vscode:open",
    "#L2",
    "#results",
    "file.ts#heading",
    "file.ts#L0",
    "file.ts#L3-L2",
    "file.ts?raw=1",
    "file%ZZ.ts",
    "file%00.ts",
    "",
  ])("refuses %s", (destination) => {
    expect(parseFileLink(destination)).toBeUndefined();
  });

  it("refuses a control character in the decoded path", () => {
    expect(parseFileLink("a%01b.ts")).toBeUndefined();
  });
});
