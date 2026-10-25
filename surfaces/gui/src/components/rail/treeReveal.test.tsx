// A file opened from outside the tree (a transcript card, Review) is shown where it sits: its folders
// open, its row is marked and scrolled to, whichever way the sender spelled its path.
// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, waitFor } from "@testing-library/react";

const ROOT = "C:\\Users\\me\\Downloads";
const LISTINGS: Record<string, { name: string; dir: boolean; size: number }[]> = {
  [ROOT]: [
    { name: ".git", dir: true, size: 0 },
    { name: ".locks", dir: true, size: 0 },
    { name: "design", dir: true, size: 0 },
    { name: "AGENTS.md", dir: false, size: 2048 },
  ],
  [`${ROOT}/.locks`]: [{ name: "pricing.lock", dir: false, size: 40 }],
  [`${ROOT}/design`]: [{ name: "pricing.b.html", dir: false, size: 900 }],
};

vi.mock("../../api", async () => {
  const actual = await vi.importActual<Record<string, unknown>>("../../api");
  return {
    ...actual,
    readArtifact: vi.fn(async (_sid: string, path: string) => ({ kind: "folder", entries: LISTINGS[path] ?? [] })),
  };
});

import { FileTree } from "../RightRail";
import { treeChain } from "./treeReveal";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const roots = [
  { path: ROOT, label: "workspace", writable: true, primary: true, exists: true },
  { path: `${ROOT}\\scratch\\s1`, label: "scratch", writable: true, primary: false, exists: true },
];

const row = (path: string) =>
  Array.from(document.querySelectorAll<HTMLElement>("[data-tree-path]"))
    .find((el) => el.dataset.treePath === path)
    ?.closest(".tree-row");

describe("treeChain", () => {
  it("spells a path the way the tree builds its rows", () => {
    const want = { dirs: [ROOT, `${ROOT}/design`], file: `${ROOT}/design/pricing.b.html` };
    expect(treeChain("design/pricing.b.html", roots as never)).toEqual(want);
    expect(treeChain("./design/pricing.b.html", roots as never)).toEqual(want);
    expect(treeChain("C:/Users/me/Downloads/design/pricing.b.html", roots as never)).toEqual(want);
    expect(treeChain("c:\\users\\me\\downloads\\design\\pricing.b.html", roots as never)?.file).toBe(
      `${ROOT}/design/pricing.b.html`,
    );
  });

  it("takes the deepest folder that holds it, and nothing outside the folders", () => {
    expect(treeChain(`${ROOT}\\scratch\\s1\\notes.md`, roots as never)).toEqual({
      dirs: [`${ROOT}\\scratch\\s1`],
      file: `${ROOT}\\scratch\\s1/notes.md`,
    });
    expect(treeChain("D:/elsewhere/AGENTS.md", roots as never)).toBeNull();
    expect(treeChain("../AGENTS.md", roots as never)).toBeNull();
  });
});

describe("the tree shows the file on show", () => {
  it("opens the folder, marks the row and scrolls it into sight", async () => {
    const scrolled = vi.fn();
    Element.prototype.scrollIntoView = scrolled;
    render(<FileTree sessionId="s" roots={roots as never} current="AGENTS.md" onSelect={vi.fn()} hideIgnored />);
    await waitFor(() => expect(row(`${ROOT}/AGENTS.md`)?.className).toContain(" on"));
    await waitFor(() => expect(scrolled).toHaveBeenCalledWith({ block: "nearest" }));
  });

  it("opens a hidden folder the file is in, and only that one", async () => {
    render(
      <FileTree sessionId="s" roots={roots as never} current={`${ROOT}\\.locks\\pricing.lock`} onSelect={vi.fn()} hideIgnored />,
    );
    await waitFor(() => expect(row(`${ROOT}/.locks/pricing.lock`)?.className).toContain(" on"));
    expect(row(`${ROOT}/.git/`)).toBeFalsy();
  });
});
