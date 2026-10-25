import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render } from "@testing-library/react";
import maps from "../assets/file-icons/maps.json";
import SPRITE from "../assets/file-icons/sprite.svg?raw";
import { FileIcon, chooseFolderIcon, chooseIconName } from "./FileIcon";

afterEach(cleanup);

function hrefOf(container: HTMLElement): string {
  const use = container.querySelector("use");
  return use?.getAttribute("href") ?? "";
}

const SYMBOLS = new Set([...SPRITE.matchAll(/<symbol id="([^"]+)"/g)].map((m) => m[1]));

describe("chooseIconName", () => {
  it("maps a plain extension to its language's icon", () => {
    expect(chooseIconName("build_report.py")).toBe("python");
    expect(chooseIconName("main.rs")).toBe("rust");
    expect(chooseIconName("server.go")).toBe("go");
    expect(chooseIconName("Main.kt")).toBe("kotlin");
  });

  it("prefers a known filename over the extension", () => {
    expect(chooseIconName("package.json")).toBe("package-json");
    expect(chooseIconName("pyproject.toml")).toBe("python-config");
    expect(chooseIconName("Dockerfile")).toBe("docker");
  });

  it("prefers the longest dotted suffix (spec.ts before ts)", () => {
    expect(chooseIconName("engine.spec.ts")).toBe("typescript-test");
    expect(chooseIconName("engine.ts")).toBe("typescript");
  });

  it("gives office files, tables and notebooks their own icons, not a text page's", () => {
    // The Material map drew .xlsx, .csv and .ipynb as a plain text document (2026-10-21).
    expect(chooseIconName("q3.xlsx")).toBe("ms-excel");
    expect(chooseIconName("data.csv")).toBe("csv");
    expect(chooseIconName("analysis.ipynb")).toBe("jupyter");
    expect(chooseIconName("letter.docx")).toBe("ms-word");
    expect(chooseIconName("deck.pptx")).toBe("ms-powerpoint");
    expect(chooseIconName("clip.mp4")).toBe("video");
    expect(chooseIconName("song.mp3")).toBe("audio");
  });

  it("falls back to the neutral file for an unknown type, not a text page", () => {
    expect(chooseIconName("mystery.zzz")).toBe("_file");
  });
});

describe("chooseFolderIcon", () => {
  it("names a known folder and its open twin, and a plain folder otherwise", () => {
    expect(chooseFolderIcon("src", false)).toBe("folder_src");
    expect(chooseFolderIcon("src", true)).toBe("folder_src_open");
    expect(chooseFolderIcon("somewhere", false)).toBe("_folder");
    expect(chooseFolderIcon("somewhere", true)).toBe("_folder_open");
  });
});

describe("FileIcon", () => {
  it("draws the language icon for a code block's lang id, by id, extension or alias", () => {
    for (const [lang, icon] of [["python", "python"], ["TypeScript", "typescript"], ["sh", "bash"], ["console", "bash"], ["golang", "go"], ["tsx", "typescript-react"]]) {
      const { container } = render(<FileIcon lang={lang} />);
      expect(hrefOf(container), lang).toContain(`#${icon}`);
      cleanup();
    }
  });

  it("draws the language icon for a Windows file path", () => {
    const { container } = render(<FileIcon path="C:\\Users\\DELL\\build_report.py" />);
    expect(hrefOf(container)).toContain("#python");
  });
});

describe("the sprite", () => {
  it("holds every icon the maps can choose", () => {
    const m = maps as Record<string, Record<string, string>>;
    const chosen = new Set([
      ...Object.values(m.fileNames), ...Object.values(m.fileExtensions), ...Object.values(m.languageIds),
      ...Object.values(m.folderNames), ...Object.values(m.folderNames).map((f) => `${f}_open`), ...Object.values(m.defaults),
    ]);
    expect([...chosen].filter((icon) => !SYMBOLS.has(icon))).toEqual([]);
  });

  it("never repeats an id, so no icon paints with another's gradient", () => {
    const ids = [...SPRITE.matchAll(/\sid="([^"]+)"/g)].map((m) => m[1]);
    expect(ids.length - new Set(ids).size).toBe(0);
  });

  it("colours every icon from the theme's --fi-* variables, never a fixed hex", () => {
    expect(SPRITE).toContain("var(--fi-");
    // A clip path or a mask is a stencil, never painted; its fill is not a colour anyone sees.
    const painted = SPRITE.replace(/<(clipPath|mask)\b[\s\S]*?<\/\1>/g, "");
    expect(painted.match(/(?:stroke|fill)="#[0-9a-f]{3,6}"/gi) ?? []).toEqual([]);
  });
});
