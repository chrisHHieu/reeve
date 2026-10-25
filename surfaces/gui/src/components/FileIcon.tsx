import { useMemo } from "react";
// Catppuccin's file icons (MIT, assets/file-icons/LICENSE), built by scripts/build-file-icons.py: one
// <symbol> per icon, line-drawn like the app's own icons, coloured by --fi-* variables that take
// Catppuccin Latte on a light theme and Mocha on a dark one (palette.css). An icon is chosen by file
// name, then the longest dotted suffix, then a neutral file. The Material sprite before it drew
// solid shapes beside the app's line icons, and 62 of its gradient ids collided, so Kotlin, Jenkins
// and Maven painted in another icon's colours (2026-10-21).
import sprite from "../assets/file-icons/sprite.svg";
import maps from "../assets/file-icons/maps.json";
import "../assets/file-icons/palette.css";

type IconMaps = {
  fileNames: Record<string, string>;
  fileExtensions: Record<string, string>;
  languageIds: Record<string, string>;
  folderNames: Record<string, string>;
  defaults: { file: string; folder: string; folderOpen: string };
};
const ICON_MAPS = maps as IconMaps;

/** What a code fence calls a language, where it is neither a VS Code language id nor an extension. */
const LANG_ALIASES: Record<string, string> = {
  shell: "sh", console: "sh", terminal: "sh", zsh: "sh", fish: "sh", bash: "sh",
  golang: "go", rust: "rs", csharp: "cs", "c#": "cs", "c++": "cpp", objectivec: "m",
  text: "txt", plaintext: "txt", plain: "txt", markdown: "md", yml: "yaml",
  docker: "dockerfile", make: "makefile", jsonc: "json", json5: "json", env: "env",
  powershell: "ps1", pwsh: "ps1", kotlin: "kt", ruby: "rb", python: "py", py3: "py",
  typescript: "ts", javascript: "js", react: "jsx", graphql: "graphql", proto: "proto",
};

export function chooseIconName(name: string): string {
  const base = basenameOf(name).toLowerCase();
  const byName = ICON_MAPS.fileNames[base];
  if (byName) return byName;
  for (const ext of dottedSuffixesDesc(base)) {
    const icon = ICON_MAPS.fileExtensions[ext];
    if (icon) return icon;
  }
  return ICON_MAPS.defaults.file;
}

function iconForLang(lang: string): string {
  const key = lang.trim().toLowerCase();
  if (!key) return ICON_MAPS.defaults.file;
  const alias = LANG_ALIASES[key];
  return (
    ICON_MAPS.languageIds[key] ??
    ICON_MAPS.fileExtensions[key] ??
    (alias ? ICON_MAPS.fileExtensions[alias] ?? ICON_MAPS.fileNames[alias] : undefined) ??
    ICON_MAPS.fileNames[key] ??
    ICON_MAPS.defaults.file
  );
}

/** Every folder icon has an open twin named `<icon>_open`. */
const toOpenVariant = (icon: string): string => (icon.endsWith("_open") ? icon : `${icon}_open`);

const folderNameVariants = (name: string) => {
  const n = name.toLowerCase();
  return [n, `.${n}`, `_${n}`, `__${n}__`];
};

export function chooseFolderIcon(name: string, expanded: boolean): string {
  const base = basenameOf(name).toLowerCase();
  for (const cand of folderNameVariants(base)) {
    const icon = ICON_MAPS.folderNames[cand];
    if (icon) return expanded ? toOpenVariant(icon) : icon;
  }
  return expanded ? ICON_MAPS.defaults.folderOpen : ICON_MAPS.defaults.folder;
}

export function FileIcon({
  path,
  lang,
  type = "file",
  expanded,
  size = 15,
  className,
}: {
  path?: string;
  lang?: string;
  type?: "file" | "directory";
  expanded?: boolean;
  size?: number;
  className?: string;
}) {
  const name = useMemo(
    () =>
      type === "directory"
        ? chooseFolderIcon(path || "", !!expanded)
        : path
          ? chooseIconName(path)
          : iconForLang(lang || ""),
    [path, lang, type, expanded],
  );
  return (
    <svg width={size} height={size} className={className} aria-hidden="true" data-component="file-icon" data-type={type}>
      <use href={`${sprite}#${name}`} />
    </svg>
  );
}

const basenameOf = (p: string) => p.split("\\").join("/").split("/").filter(Boolean).pop() ?? "";

const dottedSuffixesDesc = (name: string) => {
  const n = name.toLowerCase();
  const idxs: number[] = [];
  for (let i = 0; i < n.length; i++) if (n[i] === ".") idxs.push(i);
  const out = new Set<string>();
  out.add(n);
  for (const i of idxs) if (i + 1 < n.length) out.add(n.slice(i + 1));
  return Array.from(out).sort((a, b) => b.length - a.length);
};
