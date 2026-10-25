import { describe, expect, it } from "vitest";
import {
  ensureLanguage,
  grammarLoadCount,
  highlightLines,
  isHighlightable,
  languageForPath,
  resolveLanguageId,
} from "./highlight";
// The stylesheet as text: the palette is CSS custom properties, and jsdom will not resolve
// them, so the shipped file is the only place the literal values can be asserted.
import css from "../../styles.css?raw";

describe("languageForPath", () => {
  it("maps the extensions sessions actually touch", () => {
    expect(languageForPath("src/harness/engine.py")).toBe("python");
    expect(languageForPath("surfaces/gui/src/App.tsx")).toBe("tsx");
    expect(languageForPath("package.json")).toBe("json");
    expect(languageForPath("docker/Dockerfile")).toBe("docker");
    expect(languageForPath("scripts/run.sh")).toBe("bash");
  });

  it("resolves every common extension, not just the old hand-picked set", () => {
    expect(languageForPath("internal/gateway.go")).toBe("go");
    expect(languageForPath("crates/core/src/lib.rs")).toBe("rust");
    expect(languageForPath("src/main/java/App.java")).toBe("java");
    expect(languageForPath("app/models/user.rb")).toBe("ruby");
    expect(languageForPath("public/index.php")).toBe("php");
    expect(languageForPath("ios/App.swift")).toBe("swift");
    expect(languageForPath("android/Main.kt")).toBe("kotlin");
    expect(languageForPath("android/Main.kts")).toBe("kotlin");
    expect(languageForPath("db/schema.sql")).toBe("sql");
    expect(languageForPath("deploy/service.yaml")).toBe("yaml");
    expect(languageForPath("deploy/service.yml")).toBe("yaml");
    expect(languageForPath("Cargo.toml")).toBe("toml");
    expect(languageForPath("config/app.ini")).toBe("ini");
    expect(languageForPath("web/theme.scss")).toBe("scss");
    expect(languageForPath("web/App.vue")).toBe("vue");
    expect(languageForPath("web/Widget.svelte")).toBe("svelte");
    expect(languageForPath("native/main.c")).toBe("c");
    expect(languageForPath("native/engine.h")).toBe("c");
    expect(languageForPath("native/engine.cpp")).toBe("cpp");
    expect(languageForPath("native/engine.hpp")).toBe("cpp");
    expect(languageForPath("native/engine.cc")).toBe("cpp");
    expect(languageForPath("scripts/run.pl")).toBe("perl");
    expect(languageForPath("scripts/cleanup.psm1")).toBe("powershell");
    expect(languageForPath("lib/engine.ml")).toBe("ocaml");
    expect(languageForPath("infra/main.tf")).toBe("terraform");
    // A language the fixed table never carried: it now works without a code change.
    expect(languageForPath("src/engine.zig")).toBe("zig");
  });

  it("resolves shiki's fence aliases, so a ```rs fence and a .rs path agree", () => {
    expect(resolveLanguageId("rs")).toBe("rust");
    expect(resolveLanguageId("py")).toBe("python");
    expect(resolveLanguageId("rb")).toBe("ruby");
    expect(resolveLanguageId("kt")).toBe("kotlin");
    expect(resolveLanguageId("yml")).toBe("yaml");
    expect(resolveLanguageId("dockerfile")).toBe("docker");
    // A fence that names the file rather than the language.
    expect(resolveLanguageId("src/main.rs")).toBe("rust");
    // Case is not a language.
    expect(resolveLanguageId("Rust")).toBe("rust");
  });

  it("reads a Windows path by its filename, separators and all", () => {
    expect(languageForPath("D:\\Project\\reeve\\harness\\changes.py")).toBe("python");
  });

  it("answers undefined rather than guessing", () => {
    // A file highlighted as the WRONG language is worse than an unhighlighted one: it
    // invents structure that is not there.
    expect(languageForPath("LICENSE")).toBeUndefined();
    expect(languageForPath("notes.unknownext")).toBeUndefined();
    expect(languageForPath("")).toBeUndefined();
    // A dotfile is matched by name or a suffix after the leading dot, never by the first
    // segment of the dot.
    expect(languageForPath(".env")).toBe("ini");
    expect(languageForPath(".env.local")).toBe("ini");
    expect(languageForPath(".eslintrc.json")).toBe("json");
    expect(languageForPath(".bashrc")).toBeUndefined();
  });
});

describe("isHighlightable", () => {
  it("covers every shiki grammar and answers false only for nothing at all", () => {
    expect(isHighlightable("python")).toBe(true);
    // Zig was outside the old fixed table; the manifest includes it now.
    expect(isHighlightable("zig")).toBe(true);
    expect(isHighlightable("mojo")).toBe(true);
    expect(isHighlightable("not-a-language")).toBe(false);
    expect(isHighlightable("")).toBe(false);
    expect(isHighlightable(undefined)).toBe(false);
  });
});

describe("highlightLines", () => {
  it("returns undefined until the grammar has loaded, then real spans", async () => {
    const code = "def add(a, b):\n    return a + b\n";
    // Never loaded: the card must render plain text rather than block on the import.
    expect(highlightLines(code, "python")).toBeUndefined();

    const before = grammarLoadCount();
    await ensureLanguage("python");
    // The counter is what turns "the grammar arrived" into a re-render; without the
    // change, a card that asked during render would stay unhighlighted forever.
    expect(grammarLoadCount()).toBeGreaterThan(before);

    const lines = highlightLines(code, "python");
    expect(lines).toBeDefined();
    // Three, not two: the text ends in a newline and shiki counts the line it terminates
    // as an empty one. Callers that render a gutter drop that trailing row themselves —
    // a file ending in `\n` must not show a blank last line.
    expect(lines).toHaveLength(3);
    expect(lines![2]).toEqual([]);
    // The whole window is tokenized in one call, so line 1's `return` is still coloured.
    const keyword = lines![1].find((span) => span.text.includes("return"));
    expect(keyword).toBeDefined();
    // The exact custom property from the theme mapping, not just "some var".
    expect(keyword!.style.color).toBe("var(--syntax-keyword)");
    const string = lines![0].find((span) => span.text === "add");
    expect(string).toBeDefined();
    expect(string!.style.color).toBe("var(--syntax-primitive)");
  });

  it("highlights a non-Python grammar through the manifest loader", async () => {
    // The complaint this answers: anything outside the old 38-language table rendered
    // flat. Rust here exercises the same manifest path zig, svelte and haskell now take —
    // only shiki's table decides what highlights, not ours.
    const code = "fn main() {\n    let total = 1;\n}\n";
    await ensureLanguage("rust");
    const lines = highlightLines(code, "rust");
    expect(lines).toBeDefined();
    const keyword = lines![0].find((span) => span.text === "fn");
    expect(keyword).toBeDefined();
    expect(keyword!.style.color).toBe("var(--syntax-keyword)");
    // At least one run carries a theme colour, not the plain foreground.
    const coloured = lines!.flat().filter((span) => /var\(--syntax-/.test(String(span.style.color)));
    expect(coloured.length).toBeGreaterThan(0);
  });

  it("returns undefined for a language nothing ships", () => {
    expect(highlightLines("x", "not-a-language")).toBeUndefined();
    expect(highlightLines("x", undefined)).toBeUndefined();
  });
});

/**
 * The palette itself, checked in both themes.
 *
 * `highlightLines` returns `var(--syntax-*)` references; jsdom will not resolve them,
 * so the values are read from the stylesheet that ships them. Both occurrences of each
 * name are asserted — the first is the light block, the second the dark one — which is
 * what pins "same token names, Harbor's tuned colours" to the two blocks.
 */
describe("Harbor code palette", () => {
  const nth = (name: string, index: number) => {
    const matches = [...css.matchAll(new RegExp(`--${name}:\\s*([^;]+);`, "g"))];
    expect(matches.length, `${name} occurrences`).toBeGreaterThan(index);
    return matches[index]![1]!.trim();
  };

  it("pins the Harbor light values", () => {
    expect(nth("syntax-comment", 0)).toBe("#6a665d");
    expect(nth("syntax-keyword", 0)).toBe("#a63d6e");
    expect(nth("syntax-string", 0)).toBe("#2c7441");
    expect(nth("syntax-primitive", 0)).toBe("#a63d6e");
    expect(nth("syntax-property", 0)).toBe("#9e4f16");
    expect(nth("syntax-type", 0)).toBe("#6d3fc4");
    expect(nth("syntax-constant", 0)).toBe("#955608");
    expect(nth("syntax-info", 0)).toBe("#8a3e9e");
    expect(nth("syntax-warning", 0)).toBe("#8a5b00");
    expect(nth("syntax-critical", 0)).toBe("#b3342f");
  });

  it("pins the Harbor dark values", () => {
    expect(nth("syntax-comment", 1)).toBe("#8e8a82");
    expect(nth("syntax-keyword", 1)).toBe("#e58bb4");
    expect(nth("syntax-string", 1)).toBe("#86ce9c");
    expect(nth("syntax-primitive", 1)).toBe("#e58bb4");
    expect(nth("syntax-property", 1)).toBe("#f0a375");
    expect(nth("syntax-type", 1)).toBe("#b9a6f2");
    expect(nth("syntax-constant", 1)).toBe("#e0b268");
    expect(nth("syntax-info", 1)).toBe("#d9a0e8");
    expect(nth("syntax-warning", 1)).toBe("#e8c069");
    expect(nth("syntax-critical", 1)).toBe("#f09a90");
  });
});
