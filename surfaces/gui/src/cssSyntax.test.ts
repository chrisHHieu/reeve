// Every stylesheet tokenizes cleanly. A string the line ends inside is a bad string in CSS:
// the dev server serves each file as its own <style>, where it costs one declaration, but the
// build joins every file into one, where it swallowed every rule after it and shipped 1.0.1
// unstyled (fonts.css had `url("….woff)` six times). postcss pairs the quotes across the line
// and accepts it, so this reads the files the way the CSS tokenizer does.
import { describe, expect, it } from "vitest";

const sheets = import.meta.glob("./**/*.css", { query: "?raw", import: "default", eager: true }) as Record<
  string,
  string
>;

/** The first tokenizer fault in `css`, or null: a string cut by a newline or the end of the
 *  file, an unclosed comment, or a bracket that does not match its opener. */
function fault(css: string): string | null {
  const stack: { ch: string; line: number }[] = [];
  const pairs: Record<string, string> = { ")": "(", "}": "{", "]": "[" };
  let line = 1;
  for (let i = 0; i < css.length; i++) {
    const c = css[i];
    if (c === "\n") line++;
    else if (c === "/" && css[i + 1] === "*") {
      const end = css.indexOf("*/", i + 2);
      if (end < 0) return `line ${line}: unclosed comment`;
      line += css.slice(i, end).split("\n").length - 1;
      i = end + 1;
    } else if (c === '"' || c === "'") {
      const start = line;
      let j = i + 1;
      for (; j < css.length && css[j] !== c; j++) {
        if (css[j] === "\\") j++;
        else if (css[j] === "\n") return `line ${start}: string cut by the end of the line`;
      }
      if (j >= css.length) return `line ${start}: string cut by the end of the file`;
      i = j;
    } else if (c === "(" || c === "{" || c === "[") stack.push({ ch: c, line });
    else if (c in pairs) {
      const open = stack.pop();
      if (!open || open.ch !== pairs[c]) return `line ${line}: "${c}" closes ${open ? `"${open.ch}" from line ${open.line}` : "nothing"}`;
    }
  }
  const open = stack.pop();
  return open ? `line ${open.line}: "${open.ch}" never closed` : null;
}

describe("stylesheets", () => {
  it("finds the stylesheets", () => {
    expect(Object.keys(sheets)).toEqual(expect.arrayContaining(["./styles.css", "./fonts.css", "./cupertino.css"]));
  });

  it.each(Object.keys(sheets))("%s tokenizes cleanly", (path) => {
    expect(fault(sheets[path])).toBeNull();
  });

  it("names the fault that shipped 1.0.1 unstyled", () => {
    const broken = '@font-face { src: url("a.woff2") format("woff2"), url("a.woff) format("woff");\n}\n.x { color: red; }';
    expect(fault(broken)).toBe("line 1: string cut by the end of the line");
    expect(fault('.a { width: calc(1px + 2px; }')).toMatch(/closes "\("/);
    expect(fault(".a { color: red; }\n/* note")).toBe("line 2: unclosed comment");
  });
});
