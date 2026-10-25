// Syntax highlighting, lazily.
//
// Three decisions worth stating, because each is the difference between a card that looks
// like the reference and one that looks like a text file.
//
// THE THEME. One token-scope table, in a fixed order, painted from this app's
// `--syntax-*` palette (styles.css holds the values for light and dark). Tokens are painted with custom
// properties rather than baked hex colours, so flipping the theme repaints open cards
// without re-tokenizing them. The `--shiki-foreground`/`--shiki-background` names are
// shiki's own emit targets for scoped fg/bg and are kept; `--syntax-*` is ours.
//
// EVERY LANGUAGE SHIKI SHIPS, NOT A HANDFUL. The language manifest is shiki's own
// (`shiki/langs`): all 242 grammars plus their fence aliases (`py`, `rs`, `rb`, `kt`,
// `yml`, `dockerfile`, …), each one a dynamic import that Vite keeps as a separate lazy
// chunk. Nothing here is a hand-picked list any more — a ```` ```zig ```` fence works the
// first time it appears, without a code change. Only the handful of extension spellings
// shiki's manifest does not carry (`.h`, `.cpp`, `.pl`, `.psm1`, …) live in an override
// table below, and path lookup is derived from the same manifest.
//
// GRAMMARS LOAD ASYNCHRONOUSLY. A grammar is imported the first time a card needs it. That
// makes "the answer arrives after the render" a normal state rather than an error — and a
// component that ignored it would show every file unhighlighted for the rest of its life.
// Hence `subscribeGrammarLoad`, and hence `useGrammars`, which every highlighting component
// must call.
//
// NO GRAMMAR IS A NORMAL ANSWER. A path whose language is unknown, a build log, an invented
// fence tag — these highlight as nothing, and the card renders plain monospace. That is not
// a degradation to apologise for: a file highlighted as the WRONG language is worse than an
// unhighlighted one, because it invents structure that is not there.
//
// The JavaScript regex engine is used rather than the Oniguruma wasm build: it avoids
// shipping and instantiating a wasm asset in a desktop bundle, at the cost of the handful
// of grammars that need Oniguruma-only features. Those fail the load and fall back to
// plain text, exactly like an unknown extension.

import { useSyncExternalStore } from "react";
import { createHighlighterCore, type HighlighterCore, type ThemeRegistration } from "shiki/core";
import { createJavaScriptRegexEngine } from "shiki/engine/javascript";
import { bundledLanguages, bundledLanguagesInfo } from "shiki/langs";
import type { CSSProperties } from "react";

/** One highlighted run: the text and the styles to paint it with. */
export interface HighlightSpan {
  text: string;
  style: CSSProperties;
}

/** The theme name every `codeToTokens` call refers to. */
const THEME = "reeve";

/**
 * Extension (lowercase, no dot) and language id → shiki language id.
 *
 * Built once from shiki's own manifest: every bundled grammar (`go`, `java`, `swift`,
 * `sql`, `yaml`, `toml`, `ini`, `scss`, `vue`, `svelte`, …) and every fence alias it
 * declares (`py`, `rs`, `rb`, `kt`, `kts`, `yml`, `sh`, `ps1`, `tf`, `dockerfile`, …).
 * That is the whole coverage of the highlighter, so there is no short list to outgrow: a
 * fence that names a shiki language highlights, full stop.
 *
 * Only the spellings shiki does not carry are listed below. The table is intentionally
 * written by FAMILY (all C++ header/source suffixes together, all shell names together)
 * so a reader can see the shape of what is overridden.
 */
const EXTENSION_OVERRIDES: Record<string, string> = {
  // C family: shiki declares only `c` and `cpp` (with the alias `c++`); the header
  // suffixes and the common C++ spellings are not aliases of anything.
  h: "c",
  hh: "cpp",
  hpp: "cpp",
  hxx: "cpp",
  cc: "cpp",
  cxx: "cpp",
  ipp: "cpp",
  // Python: the type-stub and Cython suffixes.
  pyi: "python",
  pyw: "python",
  pyx: "python",
  // Shells: the app has always named the shell grammar `bash`, and shiki's `sh`/`zsh`
  // aliases point at `shellscript`. Keep `bash` so fences, paths and existing tests agree.
  sh: "bash",
  bash: "bash",
  zsh: "bash",
  ksh: "bash",
  // INI-shaped config: `properties` is shiki's alias, these are not.
  cfg: "ini",
  conf: "ini",
  cnf: "ini",
  // Perl.
  pl: "perl",
  pm: "perl",
  // Erlang.
  hrl: "erlang",
  escript: "erlang",
  // Elixir.
  ex: "elixir",
  exs: "elixir",
  // PowerShell script/module/data suffixes.
  psm1: "powershell",
  psd1: "powershell",
  // OCaml / F#.
  ml: "ocaml",
  mli: "ocaml",
  fsx: "fsharp",
  fsi: "fsharp",
  // LaTeX: `tex` is a grammar of its own, so only the other suffixes are remapped.
  ltx: "latex",
  sty: "latex",
  // Diff/patch, HTTP, Solidity, Crystal, Make, Gradle.
  patch: "diff",
  rest: "http",
  sol: "solidity",
  cr: "crystal",
  mk: "make",
  gradle: "groovy",
  // Assembly and SystemVerilog.
  s: "asm",
  sv: "system-verilog",
  svh: "system-verilog",
  vhd: "vhdl",
};

/** Filenames with no extension that still have an obvious language. */
const LANGUAGE_BY_NAME: Record<string, string> = {
  ".env": "ini",
  bashrc: "bash",
  brewfile: "ruby",
  cmakelists: "cmake",
  "cmakelists.txt": "cmake",
  dockerfile: "docker",
  gemfile: "ruby",
  gnumakefile: "make",
  jenkinsfile: "groovy",
  justfile: "just",
  makefile: "make",
  nginx: "nginx",
  podfile: "ruby",
  procfile: "bash",
  rakefile: "ruby",
  vagrantfile: "ruby",
};

let languageIds: Map<string, string> | null = null;

/**
 * id/alias/extension → canonical shiki language id, memoized.
 *
 * The manifest's own ids come first so a base grammar is never remapped by one of its
 * aliases (`jsonc` stays `jsonc` even though `json` aliases exist), then the aliases, then
 * the overrides above. Lookup is lowercase on both sides.
 */
function languageIdTable(): Map<string, string> {
  if (languageIds) return languageIds;
  const table = new Map<string, string>();
  for (const info of bundledLanguagesInfo) {
    table.set(info.id, info.id);
    for (const alias of info.aliases ?? []) if (!table.has(alias)) table.set(alias, info.id);
  }
  for (const [key, id] of Object.entries(EXTENSION_OVERRIDES)) table.set(key, id);
  languageIds = table;
  return table;
}

/**
 * The shiki language id for a fence tag, an extension or a language name, or undefined
 * when there is no confident answer.
 *
 * Fence info strings sometimes name the FILE rather than the language (```src/main.rs);
 * when the direct lookup fails and the text contains a dot, the suffix is tried. A file
 * highlighted as the WRONG language is worse than an unhighlighted one, so anything else
 * returns undefined rather than a guess.
 */
export function resolveLanguageId(id: string | undefined): string | undefined {
  if (!id) return undefined;
  const normalised = id.trim().toLowerCase();
  if (!normalised) return undefined;
  const table = languageIdTable();
  const direct = table.get(normalised);
  if (direct) return direct;
  const dot = normalised.lastIndexOf(".");
  if (dot > 0 && dot < normalised.length - 1) return table.get(normalised.slice(dot + 1));
  return undefined;
}

/**
 * The shiki language id for a path, or undefined when there is no confident answer.
 *
 * A dotfile is checked by NAME first (`.env`, `.env.local` — the text after a leading dot
 * is not an extension in the `suffix` sense), then by a suffix if one follows the leading
 * dot (`eslintrc.json`). An unknown path returns undefined rather than a guess.
 */
export function languageForPath(path: string): string | undefined {
  if (!path) return undefined;
  const normalised = path.replace(/\\/g, "/");
  const name = normalised.slice(normalised.lastIndexOf("/") + 1).toLowerCase();
  if (!name) return undefined;
  if (name === ".env" || name.startsWith(".env.")) return "ini";
  const byName = LANGUAGE_BY_NAME[name];
  if (byName) return byName;
  const isDotfile = name.startsWith(".");
  const rest = isDotfile ? name.slice(1) : name;
  if (!rest.includes(".")) return undefined;
  const extension = rest.slice(rest.lastIndexOf(".") + 1);
  return resolveLanguageId(extension);
}

// -- the highlighter ------------------------------------------------------------

/**
 * The loader for a canonical id, taken from shiki's manifest.
 *
 * The manifest's values ARE the dynamic imports (`() => import("@shikijs/langs/rust")`),
 * so handing one to `loadLanguage` keeps the grammar in its own lazy chunk — nothing above
 * ever imports a grammar statically, and the app's entry chunk only carries this table.
 */
function grammarLoader(id: string): (() => Promise<unknown>) | undefined {
  const loader = (bundledLanguages as unknown as Record<string, unknown>)[id];
  return typeof loader === "function" ? (loader as () => Promise<unknown>) : undefined;
}

/** Whether the app knows how to load a grammar for `id` at all. */
export function isHighlightable(id: string | undefined): id is string {
  return resolveLanguageId(id) !== undefined;
}

let instance: HighlighterCore | null = null;
let starting: Promise<HighlighterCore | null> | null = null;
const loaded = new Set<string>();
/** In-flight loads, so a second ask joins the first instead of returning empty-handed. */
const pending = new Map<string, Promise<void>>();
const listeners = new Set<() => void>();
let version = 0;

/**
 * The theme: scope lists in a fixed order, each colour a `--syntax-*` custom property.
 * Colours with no palette entry (`--syntax-unknown`) or that follow the prose colour
 * (`var(--text-base)`, `var(--text-strong)`) map to `--syntax-unknown` and
 * `--shiki-foreground`/`--syntax-strong`, which styles.css pins to the same values.
 */
const SYNTAX_THEME = {
  name: THEME,
  fg: "var(--shiki-foreground)",
  bg: "var(--shiki-background)",
  colors: {
    "editor.background": "var(--shiki-background)",
    "editor.foreground": "var(--shiki-foreground)",
    "gitDecoration.addedResourceForeground": "var(--syntax-diff-add)",
    "gitDecoration.deletedResourceForeground": "var(--syntax-diff-delete)",
    "gitDecoration.modifiedResourceForeground": "var(--syntax-diff-unknown)",
  },
  tokenColors: [
    {
      scope: ["comment", "punctuation.definition.comment", "string.comment"],
      settings: { foreground: "var(--syntax-comment)" },
    },
    {
      scope: ["entity.other.attribute-name"],
      settings: { foreground: "var(--syntax-property)" },
    },
    {
      scope: ["constant", "entity.name.constant", "variable.other.constant", "variable.language", "entity"],
      settings: { foreground: "var(--syntax-constant)" },
    },
    {
      scope: ["entity.name", "meta.export.default", "meta.definition.variable"],
      settings: { foreground: "var(--syntax-type)" },
    },
    {
      scope: ["meta.object.member"],
      settings: { foreground: "var(--syntax-primitive)" },
    },
    {
      scope: [
        "variable.parameter.function",
        "meta.jsx.children",
        "meta.block",
        "meta.tag.attributes",
        "entity.name.constant",
        "meta.embedded.expression",
        "meta.template.expression",
        "string.other.begin.yaml",
        "string.other.end.yaml",
      ],
      settings: { foreground: "var(--syntax-punctuation)" },
    },
    {
      scope: ["entity.name.function", "support.type.primitive"],
      settings: { foreground: "var(--syntax-primitive)" },
    },
    {
      scope: ["support.class.component"],
      settings: { foreground: "var(--syntax-type)" },
    },
    {
      scope: "keyword",
      settings: { foreground: "var(--syntax-keyword)" },
    },
    {
      scope: [
        "keyword.operator",
        "storage.type.function.arrow",
        "punctuation.separator.key-value.css",
        "entity.name.tag.yaml",
        "punctuation.separator.key-value.mapping.yaml",
      ],
      settings: { foreground: "var(--syntax-operator)" },
    },
    {
      scope: ["storage", "storage.type"],
      settings: { foreground: "var(--syntax-keyword)" },
    },
    {
      scope: ["storage.modifier.package", "storage.modifier.import", "storage.type.java"],
      settings: { foreground: "var(--syntax-primitive)" },
    },
    {
      scope: [
        "string",
        "punctuation.definition.string",
        "string punctuation.section.embedded source",
        "entity.name.tag",
      ],
      settings: { foreground: "var(--syntax-string)" },
    },
    {
      scope: "support",
      settings: { foreground: "var(--syntax-primitive)" },
    },
    {
      scope: ["support.type.object.module", "variable.other.object", "support.type.property-name.css"],
      settings: { foreground: "var(--syntax-object)" },
    },
    {
      scope: "meta.property-name",
      settings: { foreground: "var(--syntax-property)" },
    },
    {
      scope: "variable",
      settings: { foreground: "var(--syntax-variable)" },
    },
    {
      scope: "variable.other",
      settings: { foreground: "var(--syntax-variable)" },
    },
    {
      scope: [
        "invalid.broken",
        "invalid.illegal",
        "invalid.unimplemented",
        "invalid.deprecated",
        "message.error",
        "markup.deleted",
        "meta.diff.header.from-file",
        "punctuation.definition.deleted",
        "brackethighlighter.unmatched",
        "token.error-token",
      ],
      settings: { foreground: "var(--syntax-critical)" },
    },
    {
      scope: "carriage-return",
      settings: { foreground: "var(--syntax-keyword)" },
    },
    {
      scope: "string source",
      settings: { foreground: "var(--syntax-variable)" },
    },
    {
      scope: "string variable",
      settings: { foreground: "var(--syntax-constant)" },
    },
    {
      scope: [
        "source.regexp",
        "string.regexp",
        "string.regexp.character-class",
        "string.regexp constant.character.escape",
        "string.regexp source.ruby.embedded",
        "string.regexp string.regexp.arbitrary-repitition",
        "string.regexp constant.character.escape",
      ],
      settings: { foreground: "var(--syntax-regexp)" },
    },
    {
      scope: "support.constant",
      settings: { foreground: "var(--syntax-primitive)" },
    },
    {
      scope: "support.variable",
      settings: { foreground: "var(--syntax-variable)" },
    },
    {
      scope: "meta.module-reference",
      settings: { foreground: "var(--syntax-info)" },
    },
    {
      scope: "punctuation.definition.list.begin.markdown",
      settings: { foreground: "var(--syntax-punctuation)" },
    },
    {
      scope: ["markup.heading", "markup.heading entity.name"],
      settings: { fontStyle: "bold", foreground: "var(--syntax-info)" },
    },
    {
      scope: "markup.quote",
      settings: { foreground: "var(--syntax-info)" },
    },
    {
      scope: "markup.italic",
      settings: { fontStyle: "italic" },
    },
    {
      scope: "markup.bold",
      settings: { fontStyle: "bold", foreground: "var(--syntax-strong)" },
    },
    {
      scope: [
        "markup.raw",
        "markup.inserted",
        "meta.diff.header.to-file",
        "punctuation.definition.inserted",
        "markup.changed",
        "punctuation.definition.changed",
        "markup.ignored",
        "markup.untracked",
      ],
      settings: { foreground: "var(--shiki-foreground)" },
    },
    {
      scope: "meta.diff.range",
      settings: { fontStyle: "bold", foreground: "var(--syntax-unknown)" },
    },
    {
      scope: "meta.diff.header",
      settings: { foreground: "var(--syntax-unknown)" },
    },
    {
      scope: "meta.separator",
      settings: { fontStyle: "bold", foreground: "var(--syntax-unknown)" },
    },
    {
      scope: "meta.output",
      settings: { foreground: "var(--syntax-unknown)" },
    },
    {
      scope: "meta.export.default",
      settings: { foreground: "var(--syntax-unknown)" },
    },
    {
      scope: [
        "brackethighlighter.tag",
        "brackethighlighter.curly",
        "brackethighlighter.round",
        "brackethighlighter.square",
        "brackethighlighter.angle",
        "brackethighlighter.quote",
      ],
      settings: { foreground: "var(--syntax-unknown)" },
    },
    {
      scope: ["constant.other.reference.link", "string.other.link"],
      settings: { fontStyle: "underline", foreground: "var(--syntax-unknown)" },
    },
    {
      scope: "token.info-token",
      settings: { foreground: "var(--syntax-info)" },
    },
    {
      scope: "token.warn-token",
      settings: { foreground: "var(--syntax-warning)" },
    },
    {
      scope: "token.debug-token",
      settings: { foreground: "var(--syntax-info)" },
    },
  ],
  semanticTokenColors: {
    comment: "var(--syntax-comment)",
    string: "var(--syntax-string)",
    number: "var(--syntax-constant)",
    regexp: "var(--syntax-regexp)",
    keyword: "var(--syntax-keyword)",
    variable: "var(--syntax-variable)",
    parameter: "var(--syntax-variable)",
    property: "var(--syntax-property)",
    function: "var(--syntax-primitive)",
    method: "var(--syntax-primitive)",
    type: "var(--syntax-type)",
    class: "var(--syntax-type)",
    namespace: "var(--syntax-type)",
    enumMember: "var(--syntax-primitive)",
    "variable.constant": "var(--syntax-constant)",
    "variable.defaultLibrary": "var(--syntax-unknown)",
  },
} as unknown as ThemeRegistration;

// shiki's `FontStyle` is a bitmask, and `codeToTokens` hands back the mask rather than a
// style object (the `htmlStyle` form belongs to the HTML renderer). Decoded here so the
// React renderer paints italics and bold without a second pass over the theme.
const FONT_STYLE_BITS: ReadonlyArray<readonly [number, CSSProperties]> = [
  [1, { fontStyle: "italic" }],
  [2, { fontWeight: 600 }],
  [4, { textDecoration: "underline" }],
  [8, { textDecoration: "line-through" }],
];

function tokenStyle(token: {
  color?: string;
  bgColor?: string;
  fontStyle?: number;
}): CSSProperties {
  const style: CSSProperties = {};
  if (token.color) style.color = token.color;
  if (token.bgColor) style.background = token.bgColor;
  for (const [bit, decoration] of FONT_STYLE_BITS) {
    if ((token.fontStyle ?? 0) & bit) Object.assign(style, decoration);
  }
  return style;
}

async function highlighter(): Promise<HighlighterCore | null> {
  if (instance) return instance;
  if (!starting) {
    starting = (async () => {
      try {
        instance = await createHighlighterCore({
          themes: [SYNTAX_THEME],
          langs: [],
          engine: createJavaScriptRegexEngine(),
        });
        return instance;
      } catch {
        // A highlighter that cannot start must not take the transcript down with it: every
        // card falls back to plain text, which is what it would have shown anyway.
        return null;
      }
    })();
  }
  return starting;
}

/**
 * Subscribe to grammar arrivals.
 *
 * `useSyncExternalStore` needs a snapshot that changes when the store does, so this
 * exposes a counter that only ever increases. Anything that highlights reads it, and that
 * is what turns "the grammar finished loading" into a re-render instead of a card stuck at
 * plain text for the rest of its life.
 */
export function subscribeGrammarLoad(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function grammarLoadCount(): number {
  return version;
}

/**
 * Load a grammar on demand and announce it. Safe to call repeatedly and concurrently, and
 * it AWAITS: two callers asking for the same language at once both resolve when that one
 * load lands, rather than the second returning immediately against a grammar that is not
 * there yet.
 */
export function ensureLanguage(id: string): Promise<void> {
  const language = resolveLanguageId(id);
  if (!language) return Promise.resolve();
  if (loaded.has(language)) return Promise.resolve();
  const flying = pending.get(language);
  if (flying) return flying;
  const load = (async () => {
    const core = await highlighter();
    if (!core) return;
    try {
      const loader = grammarLoader(language);
      if (!loader) return;
      // shiki's core takes the dynamic import itself and unwraps `default` for us.
      await core.loadLanguage(loader as never);
      loaded.add(language);
    } catch {
      // Not added to `loaded`, so a grammar that failed on a transient import error can be
      // retried; the counter still moves, so a waiting card re-renders and finds plain
      // text rather than waiting forever.
    }
    version += 1;
    for (const listener of listeners) listener();
  })().finally(() => {
    pending.delete(language);
  });
  pending.set(language, load);
  return load;
}

// -- public surface -------------------------------------------------------------

/**
 * Highlight `code` as `language`, returning one array of runs per line.
 *
 * The WHOLE text is tokenized in one call rather than line by line, so grammar context
 * carries across lines — a docstring that opens on line 1 and closes on line 9 is one
 * string, and tokenizing each line alone would colour its body as code.
 *
 * `undefined` means "render the plain text": the grammar is still loading, the language is
 * unknown, or tokenizing failed. Call `useGrammars` alongside this or the result will never
 * be re-read when the grammar arrives.
 */
export function highlightLines(
  code: string,
  language: string | undefined,
): HighlightSpan[][] | undefined {
  const id = resolveLanguageId(language);
  if (!id) return undefined;
  if (!instance || !loaded.has(id)) {
    // Requesting during render is a side effect, but an idempotent one — and the
    // alternative is every component having to know to ask before it can draw.
    void ensureLanguage(id);
    return undefined;
  }
  try {
    const result = instance.codeToTokens(code, {
      lang: id as never,
      theme: THEME,
    });
    return result.tokens.map((line) =>
      line.map((token) => ({ text: token.content, style: tokenStyle(token) })),
    );
  } catch {
    return undefined;
  }
}

/**
 * Re-render when the grammar for `language` finishes loading, and return the counter that
 * says a load happened.
 *
 * The return value matters: callers memoize `highlightLines` on their inputs, and without
 * the counter in that dependency list the memo would keep the `undefined` it computed
 * while the grammar was still in flight — the component re-renders and still paints plain
 * text, for the rest of its life. Include it in the deps of any memoized highlight call.
 */
export function useGrammars(language: string | undefined): number {
  const version = useSyncExternalStore(subscribeGrammarLoad, grammarLoadCount, grammarLoadCount);
  if (isHighlightable(language)) void ensureLanguage(language);
  return version;
}
