import {
  Fragment,
  createContext,
  isValidElement,
  memo,
  useContext,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
} from "react";
import ReactMarkdown, { defaultUrlTransform } from "react-markdown";
import { canEmbedBrowser } from "../desktop";
import { openLinkInPanel } from "./rail/browserLinks";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import remarkParse from "remark-parse";
import remarkCjkFriendly from "remark-cjk-friendly";
import remarkCjkFriendlyGfmStrikethrough from "remark-cjk-friendly-gfm-strikethrough";
import rehypeKatex from "rehype-katex";
import { unified } from "unified";
// rehype-katex emits KaTeX's markup but ships no stylesheet, so it is on us.
import "katex/dist/katex.min.css";
import { FileIcon } from "./FileIcon";
import { Icon, type IconName } from "./Icon";
import { remarkAlerts, type AlertKind } from "./markdown/alerts";
import { remarkHtmlBreaks } from "./markdown/htmlBreaks";
import { joinFactRuns, remarkFacts } from "./markdown/facts";
import { isProse, isUnnamedFence, parseTextTree } from "./markdown/fenceKinds";
import { MarkdownDraft, MarkdownTree } from "./markdown/FenceViews";
import { AnswerContents, contentsOf, headingsOf } from "./markdown/Contents";
import { fileWithLine, resolveInFolder, useFolderFiles } from "./markdown/folderFiles";
import { useSessionShell } from "../sessionShell";
import { MarkdownBoundary } from "./markdown/MarkdownBoundary";
import { MarkdownImage, MarkdownImageGallery } from "./markdown/MarkdownImage";
import { MarkdownTable } from "./markdown/MarkdownTable";
import { MermaidDiagram } from "./markdown/MermaidDiagram";
import { MarkdownFileContext, useFileImages } from "./markdown/fileSource";
import { hideOpenTail, parseDirectives } from "./markdown/directives";
import { parseFileLink } from "./markdown/file-link";
import { repairStreamingMarkdown } from "./markdown/streamRepair";
import { prepareMath } from "./mathText";
import { CopyButton } from "./primitives/CopyButton";
import { highlightLines, useGrammars } from "./primitives/highlight";

// A file the agent made is named in plain markdown, [Title](artifact:relative/path). In the
// sentence it reads as a light mention, the file's mark and name; the card that opens it is under
// the turn, with the turn's other files. Opening goes by a window event, since this renders deep in
// the transcript and the viewer is RightRail's: the rail finds the path among the session's files,
// and App shows the rail.
export const OPEN_ARTIFACT_EVENT = "reeve-open-artifact";

// Show a cited or delivered file in the OS file manager. The chip lives deep in the
// transcript with no session id, so it asks the session view (SessionScroll) to do it.
export const REVEAL_ARTIFACT_EVENT = "reeve-reveal-artifact";

/** Open a file in the session's artifact viewer (RightRail owns the panel); with a line, the
 *  viewer scrolls to it and marks it. */
export function openArtifact(path: string, line?: number) {
  window.dispatchEvent(new CustomEvent(OPEN_ARTIFACT_EVENT, { detail: line ? { path, line } : { path } }));
}

// Show the session's folder in the panel's Files tab, as a tree, with no file opened: the top
// bar's "where it works" crumb asks for it. A folder's path through OPEN_ARTIFACT_EVENT would be
// opened as a file.
export const OPEN_FILES_EVENT = "reeve-open-files";

export function openFilesTab() {
  window.dispatchEvent(new CustomEvent(OPEN_FILES_EVENT));
}

/**
 * Ask the OS to show a file, or hand it to its own application.
 *
 * `mode` is the difference between two intentions a reader has about the same file: put me
 * in front of it (`open`), or put me in front of where it lives (`reveal`). Same event as
 * the reveal-only version this replaced, with the mode in the detail, because the listener
 * that can actually act (SessionScroll — it holds the session id) already exists and a
 * second event would only mean a second listener to keep in step.
 */
export function revealArtifact(path: string, mode: "reveal" | "open" = "reveal") {
  window.dispatchEvent(new CustomEvent(REVEAL_ARTIFACT_EVENT, { detail: { path, mode } }));
}

/**
 * The turn's own file vocabulary, for inline code: `hello.md` written in backticks opens the
 * file the turn produced (the reference design resolves the same way — exact path, or a basename that is
 * unique among this turn's files, and nothing when the token is ambiguous).
 *
 * It exists because the extension heuristic below cannot tell a file the turn WROTE from a
 * word that merely looks like a path, and it cannot resolve a bare `hello.md` to the
 * `docs/hello.md` it actually means. A resolver supplied by the transcript knows both, so
 * the guess is only the fallback.
 */
export type FileMentionResolver = (token: string) => string | undefined;

const FileMentions = createContext<FileMentionResolver | undefined>(undefined);

const FILE_TYPES: Record<string, string> = {
  md: "Markdown", markdown: "Markdown", txt: "Text", pdf: "PDF", doc: "Word", docx: "Word",
  xls: "Excel", xlsx: "Excel", csv: "CSV", ppt: "PowerPoint", pptx: "PowerPoint",
  html: "HTML", htm: "HTML", json: "JSON", yaml: "YAML", yml: "YAML", xml: "XML",
  png: "Image", jpg: "Image", jpeg: "Image", gif: "Image", webp: "Image", svg: "SVG",
  py: "Python", ts: "TypeScript", tsx: "TypeScript", js: "JavaScript", jsx: "JavaScript",
  sql: "SQL", sh: "Shell", ps1: "PowerShell", zip: "Archive",
};

/** "Markdown", "PDF", … — what kind of file this is, in the words a non-developer uses.
 *  An unknown extension reads as itself in capitals; no extension, nothing. */
export function fileTypeLabel(name: string): string {
  const dot = name.lastIndexOf(".");
  if (dot <= 0 || dot === name.length - 1) return "";
  const ext = name.slice(dot + 1).toLowerCase();
  return FILE_TYPES[ext] ?? ext.toUpperCase();
}

/** A file the answer cites (`:file-citation{path purpose}` → `[leaf](file:path)`), drawn as
 *  an inline pill (design C, owner-approved 2026-09-28): the file's own logo and its name,
 *  sitting in the sentence without breaking it. The folder and the purpose ride the tooltip
 *  (and the accessible name) — in the line they were noise. */
function CitationChip({ path, purpose }: { path: string; purpose?: string }) {
  const normalised = path.replace(/\\/g, "/");
  const leaf = normalised.split("/").pop() || path;
  return (
    <button
      type="button"
      className="md-cite"
      data-testid="file-citation"
      title={purpose ? `${path} — ${purpose}` : path}
      onClick={() => openArtifact(path)}
    >
      <FileIcon path={path} size={13} />
      <span className="md-cite-leaf">{leaf}</span>
    </button>
  );
}

/** A file the turn produced, named IN the sentence (`[Title](artifact:path)`) or linked the
 *  way the reference design links one (`[name](relative/path#L24)`): a light mention — the file's glyph, its
 *  name in the accent — that opens the file. The full file card lives stacked under the turn
 *  (see `Deliverables.tsx`), so a paragraph that names three files stays a paragraph. `title`
 *  is what the model wrote in the link; a model that left it empty gets the filename instead
 *  of a blank chip.
 *
 *  Both spellings land here on purpose: the reader sees ONE affordance for "this is a file",
 *  and which scheme the model happened to write is not their problem. `file-link` is the
 *  testid for the plain-path form, because a test that wants the artifact contract must be
 *  able to say which one it got. */
function DeliveredMention({
  path,
  title,
  testid = "artifact-mention",
  onOpen,
}: {
  path: string;
  title: string;
  testid?: string;
  onOpen?: () => void;
}) {
  const file = path.split(/[\\/]/).pop() || path;
  return (
    <button
      type="button"
      className="md-cite"
      data-testid={testid}
      title={`Open ${path}`}
      aria-label={`Open ${path} in sidebar`}
      onClick={onOpen ?? (() => openArtifact(path))}
    >
      <FileIcon path={path} size={13} />
      <span className="md-cite-leaf">{title || file}</span>
    </button>
  );
}

/**
 * A link to a file the model wrote the ordinary way. The model writes the path from where its
 * shell stood, which need not be the conversation's folder: `app/domain/goals.py` for the
 * folder's `backend/app/domain/goals.py` opened "This isn't in the conversation's folder"
 * (owner, 2026-10-13). So the path is looked up in the folder's own file list the way
 * `file.py:42` is (markdown/folderFiles.ts), and opened as written when the list has no single
 * answer.
 */
function FileLink({ path, line, title }: { path: string; line?: number; title: string }) {
  const shell = useSessionShell();
  const mentions = useContext(FileMentions);
  const files = useFolderFiles(shell.environment ? undefined : shell.sessionId, true);
  const open = () => openArtifact((files && resolveInFolder(path, files, mentions)) || path, line);
  return <DeliveredMention path={path} title={title} testid="file-link" onOpen={open} />;
}

/**
 * A fenced code block, rendered with the chrome and token colours.
 *
 * The spans are shiki runs from the shared highlighter (see primitives/highlight.ts — a
 * port of the theme), joined with explicit "\n" so the `<pre>` reproduces the
 * source exactly and `code.textContent` is what the copy button writes. `useGrammars`
 * re-renders the block when its grammar finishes loading; until then, and for languages
 * the app cannot load, the plain text reads as code rather than as nothing.
 *
 * The chrome (background, border, radius, 13px/22.4px type, hover-revealed copy) lives in
 * styles.css under `.md-codeblock*`; it is the `.shiki` treatment. Shell fences are
 * tagged `data-code-kind="shell"` so the
 * stylesheet can flatten their tokens to one colour, as upstream does.
 */
const SHELL_LANGUAGES = new Set(["bash", "sh", "shell", "zsh", "fish", "console", "terminal"]);

/** Past this many lines a block numbers them: a two-line snippet does not need numbers, and a
 *  long one is read and talked about by line (owner, 2026-10-13). */
export const NUMBER_LINES_PAST = 5;

/**
 * A fenced code block: one card on the page's own white, with a bar that says what it is (the
 * language's mark, the file's name when the fence gives one, the language) and holds wrap and
 * copy, then the code. Past five lines each line has its number, drawn by CSS from an attribute
 * so neither the copy button nor a selection carries it.
 *
 * It was a grey slab with the language in small letters above it (owner, 2026-10-13: "mấy cái
 * xám xám đồ ấy không thích").
 */
function MarkdownCode({ code, lang, file }: { code: string; lang?: string; file?: string }) {
  const trimmed = code.endsWith("\n") ? code.slice(0, -1) : code;
  const grammars = useGrammars(lang);
  const spans = useMemo(() => highlightLines(trimmed, lang), [trimmed, lang, grammars]);
  const [wrapped, setWrapped] = useState(false);
  const shell = lang ? SHELL_LANGUAGES.has(lang.toLowerCase()) : false;
  const lines = spans ? spans.length : trimmed.split("\n").length;
  const numbered = !shell && lines > NUMBER_LINES_PAST;
  const number = (index: number) =>
    numbered ? <span className="md-ln" data-n={index + 1} aria-hidden /> : null;
  const body = (
    <pre
      className="md-codeblock-pre"
      data-wrap={wrapped || undefined}
      data-numbered={numbered || undefined}
      style={numbered ? ({ "--md-ln-w": `${String(lines).length}ch` } as CSSProperties) : undefined}
    >
      <code className={lang ? `language-${lang}` : undefined}>
        {spans
          ? spans.map((line, lineIndex) => (
              <Fragment key={lineIndex}>
                {number(lineIndex)}
                {line.map((span, spanIndex) => (
                  <span key={spanIndex} style={span.style}>
                    {span.text}
                  </span>
                ))}
                {lineIndex < spans.length - 1 ? "\n" : null}
              </Fragment>
            ))
          : numbered
            ? trimmed.split("\n").map((line, lineIndex, all) => (
                <Fragment key={lineIndex}>
                  {number(lineIndex)}
                  {line}
                  {lineIndex < all.length - 1 ? "\n" : null}
                </Fragment>
              ))
            : trimmed}
      </code>
    </pre>
  );
  return (
    <div className="md-codeblock" data-language={lang} data-code-kind={shell ? "shell" : undefined}>
      <div className="md-codeblock-head">
        {shell ? (
          <Icon name="terminal" size={13} className="md-codeblock-mark" />
        ) : (
          <FileIcon path={file} lang={file ? undefined : lang || "text"} size={14} className="md-codeblock-mark" />
        )}
        {file && (
          <span className="md-codeblock-file" data-testid="code-file" title={file}>
            {file}
          </span>
        )}
        <span className="md-codeblock-lang" data-testid="code-language">
          {lang ? lang.toLowerCase() : "code"}
        </span>
        <span className="md-codeblock-spacer" />
        <button
          type="button"
          className="md-codeblock-wrap"
          data-testid="code-wrap"
          aria-pressed={wrapped}
          title={wrapped ? "Stop wrapping long lines" : "Wrap long lines"}
          onClick={() => setWrapped((value) => !value)}
        >
          ↵
        </button>
        <CopyButton text={trimmed} iconOnly className="md-codeblock-copy" />
      </div>
      {body}
    </div>
  );
}

/**
 * `singleDollarTextMath` stays ON — remark-math's own default — and the currency damage it
 * would cause is handled before the parser instead. See mathText.ts for the measurement:
 * over real assistant paragraphs mined from this app's own session store, turning the
 * option OFF loses every formula, and leaving it ON without the preprocess eats money.
 *
 * `singleTilde: false` is GFM's own rule as marked implements it: `~~deleted~~`
 * is strikethrough, a lone `~0.1576` is a character. remark-gfm defaults the other way and
 * would cut every measurement in half.
 */
const remarkPlugins = [
  // CJK-aware emphasis/strikethrough: Chinese/Japanese text has
  // no spaces around punctuation, so GFM's flanking rules mis-read `**粗体**，句子` as literal
  // asterisks. Vietnamese is Latin and unaffected; the plugins cost nothing when they do not
  // apply, and the alternative is a renderer that quietly breaks for a whole script.
  remarkCjkFriendly,
  [remarkCjkFriendlyGfmStrikethrough, { singleTilde: false }],
  [remarkGfm, { singleTilde: false }],
  [remarkMath, { singleDollarTextMath: true }],
  // `> [!NOTE]` and its kin are callouts, as GitHub draws them (markdown/alerts.ts).
  remarkAlerts,
  // `<br>` is a line break, in a table's cell above all; no other HTML is drawn (markdown/htmlBreaks.ts).
  remarkHtmlBreaks,
  // Two or more `**Label:** value` lines are a list of facts (markdown/facts.ts). After the
  // breaks, so a `<br>` between two such lines cuts them like a line end.
  remarkFacts,
] as never;
const rehypePlugins = [rehypeKatex] as never;

// The reference design normalizes an inline-code URL to its href the same way ():
// trailing sentence punctuation is not part of the address, and only full http(s) URLs
// become links.
const urlPattern = /^https?:\/\/[^\s<>()`"']+$/;

function codeUrl(text: string) {
  const href = text.trim().replace(/[),.;!?]+$/, "");
  if (!urlPattern.test(href)) return;
  try {
    return new URL(href).toString();
  } catch {
    return;
  }
}

// An inline code span that is a URL becomes its own anchor; one already inside a markdown
// link must not nest a second anchor. The reference design checks the rendered parent (markdown.tsx:
// 239-266); React cannot, so the link renderer marks its subtree instead.
const InsideLink = createContext(false);

/** The words of rendered children, through any element in them. A link written
 *  [`goals.py`](app/goals.py) has a `<code>` element for its text, and reading only strings
 *  named it "[object Object]" (owner, 2026-10-13). */
function inlineText(children: ReactNode): string {
  if (Array.isArray(children)) return children.map(inlineText).join("");
  if (typeof children === "string" || typeof children === "number") return String(children);
  if (isValidElement<{ children?: ReactNode }>(children)) return inlineText(children.props.children);
  return "";
}

/**
 * Inline code: a URL opens externally, and a token that names a file THIS TURN produced or
 * declared (`FileMentions`) becomes a file mention. Everything else is ordinary code — even
 * when it spells a path.
 *
 * The last clause is the whole point, and it is the rule (`markdown/
 * render.tsx`, the `inlineCode` arm): the renderer never guesses that a token is a file from
 * its shape. An extension-in-a-dictionary test made `Transcript.tsx`, `node_modules/d3`,
 * `Makefile` and every version string in a sentence clickable, which is a transcript where
 * the reader cannot tell a fact from a guess — and a click on a guess lands on "this isn't in
 * the conversation's folder anymore". A file the model wants clickable it LINKS: `[name](path)`
 * (see `parseFileLink`), which is a fact about the sentence, not a property of its spelling.
 */
function MarkdownInlineCode({ node: _n, children, ...props }: any) {
  const text = inlineText(children);
  const inLink = useContext(InsideLink);
  const mentions = useContext(FileMentions);

  // A token that is entirely an absolute http(s) URL keeps its code chrome and gains the
  // same anchor a link gets. Inside a link the anchor cannot nest, so the token stays code —
  // still tagged, because "this span is a URL" is true either way.
  const href = codeUrl(text);
  if (href && !inLink)
    return (
      <a href={href} className="external-link" target="_blank" rel="noopener noreferrer">
        <code data-inline-code-kind="url">{children}</code>
      </a>
    );

  const known = inLink || href !== undefined ? undefined : mentions?.(text);
  // `file.py:42`: the folder's file at that line, when the folder has it (markdown/folderFiles.ts).
  // The list is asked for only by a span that names a line, and never on another machine.
  const named = inLink || href !== undefined || known !== undefined ? null : fileWithLine(text);
  const shell = useSessionShell();
  const files = useFolderFiles(shell.environment ? undefined : shell.sessionId, named !== null);
  const found = named && files ? resolveInFolder(named.path, files, mentions) : undefined;
  if (named && found) {
    const open = () => openArtifact(found, named.line);
    return (
      <code
        data-inline-code-kind="file"
        data-testid="file-line"
        role="button"
        tabIndex={0}
        title={`Open ${found} at line ${named.line}`}
        aria-label={`Open ${found} at line ${named.line}`}
        onClick={open}
        onKeyDown={(event) => {
          if (event.key !== "Enter" && event.key !== " ") return;
          event.preventDefault();
          open();
        }}
      >
        <FileIcon path={found} size={12} />
        <span className="md-path-text">{named.path}</span>
        <span className="md-path-line">{text.trim().slice(named.path.length)}</span>
      </code>
    );
  }
  if (known !== undefined) {
    return (
      <code
        data-inline-code-kind="file"
        role="button"
        tabIndex={0}
        title={`Open ${known}`}
        aria-label={`Open ${known} in sidebar`}
        onClick={() => openArtifact(known)}
        onKeyDown={(event) => {
          if (event.key !== "Enter" && event.key !== " ") return;
          event.preventDefault();
          openArtifact(known);
        }}
        {...props}
      >
        <FileIcon path={known} size={12} />
        <span className="md-path-text">{children}</span>
      </code>
    );
  }

  return (
    <code {...(href !== undefined ? { "data-inline-code-kind": "url" } : {})} {...props}>
      {children}
    </code>
  );
}

/** The `img` renderer as a real component, so it can read the anti-nesting context. */
function MarkdownImg({ node: _n, src, alt }: any) {
  const inLink = useContext(InsideLink);
  // In a file preview a local path is read from where the file lives (fileSource.tsx).
  const [image] = useFileImages(typeof src === "string" && src ? [{ src, alt }] : []);
  if (image?.state === "loading") return <span className="md-img-wait" role="status" aria-label="Loading the picture" />;
  if (image?.state === "missing") return <MissingImage written={image.written} alt={alt} />;
  return <MarkdownImage src={image?.src} alt={alt} inLink={inLink} />;
}

/** A picture the file names that is not there: said in its place, never a broken-image icon. */
function MissingImage({ written, alt }: { written: string; alt?: string }) {
  return (
    <span className="md-img-missing" data-testid="md-image-missing">
      {alt ? `${alt}: ` : ""}picture not found at <code>{written}</code>
    </span>
  );
}

/** A run of pictures in a file preview, each read from where the file lives. */
function FileImageGallery({ images }: { images: { src: string; alt?: string }[] }) {
  const resolved = useFileImages(images);
  const ready = resolved.filter((image) => image.state === "ready");
  return (
    <>
      {ready.length > 0 && <MarkdownImageGallery images={ready} />}
      {resolved
        .filter((image) => image.state === "missing")
        .map((image) => (
          <p key={image.written}>
            <MissingImage written={image.written} alt={image.alt} />
          </p>
        ))}
    </>
  );
}

/** The images inside a paragraph node, when that is ALL it holds. */
function imageOnly(node: any): { src: string; alt?: string }[] | null {
  const images: { src: string; alt?: string }[] = [];
  for (const child of node?.children ?? []) {
    if (child.type === "text") {
      if (String(child.value ?? "").trim() !== "") return null;
      continue;
    }
    if (child.tagName !== "img") return null;
    const src = child.properties?.src;
    if (typeof src !== "string" || !src) return null;
    images.push({ src, alt: typeof child.properties?.alt === "string" ? child.properties.alt : undefined });
  }
  return images.length > 0 ? images : null;
}

/**
 * A paragraph that is nothing but pictures. One image keeps the ordinary `img` renderer (its
 * own lightbox); two or more become a GALLERY, the `MarkdownImageParagraph` — four
 * screenshots are one block a reader scans, not four full-width pictures pushing the answer
 * off screen.
 */
function MarkdownParagraph({ node, children, ...props }: any) {
  const images = imageOnly(node);
  const file = useContext(MarkdownFileContext);
  if (images && images.length > 1) {
    return file ? <FileImageGallery images={images} /> : <MarkdownImageGallery images={images} />;
  }
  return <p {...props}>{children}</p>;
}

/**
 * What a fence's info string says: its language, and the file it is from when it names one,
 * in the three ways models write that (`python upload_client.py`, `python title="x.py"`,
 * `python:x.py`). A word that is not file-shaped is not a file.
 */
export function fenceInfo(info: string | undefined, meta: unknown): { lang?: string; file?: string } {
  let lang = info;
  let file: string | undefined;
  if (lang && lang.includes(":")) {
    const at = lang.indexOf(":");
    file = lang.slice(at + 1) || undefined;
    lang = lang.slice(0, at) || undefined;
  }
  if (!file && typeof meta === "string") {
    const quoted = /(?:title|file|filename)=["']?([^"'\s]+)["']?/i.exec(meta);
    const bare = meta.trim().split(/\s+/).find((word) => /^[\w./\\-]+\.[A-Za-z0-9]{1,8}$/.test(word));
    file = quoted?.[1] ?? bare;
  }
  return { lang, file: file && /\.[A-Za-z0-9]{1,8}$/.test(file) ? file : undefined };
}

/** A fenced block. In a file preview that asks for it, a ```mermaid fence is drawn. */
function MarkdownPre({ node, children, ...props }: any) {
  const file = useContext(MarkdownFileContext);
  const code = node?.children?.find((child: any) => child.tagName === "code");
  if (!code) return <pre {...props}>{children}</pre>;
  const className = Array.isArray(code.properties?.className)
    ? code.properties.className.join(" ")
    : "";
  const info = /language-([^\s]+)/.exec(className)?.[1];
  const text = (code.children ?? [])
    .map((child: any) => (child.type === "text" ? child.value : ""))
    .join("");
  const { lang, file: named } = fenceInfo(info, code.data?.meta);
  if (file?.diagrams && lang?.toLowerCase() === "mermaid" && text.trim()) return <MermaidDiagram code={text} />;
  // A fence that names no language may hold a folder tree or a draft, not code (fenceKinds.ts).
  if (!named && isUnnamedFence(lang)) {
    const tree = parseTextTree(text);
    if (tree) return <MarkdownTree tree={tree} source={text.replace(/\n$/, "")} />;
    if (isProse(text)) return <MarkdownDraft source={text} />;
  }
  return <MarkdownCode code={text} lang={lang} file={named} />;
}

const ALERTS: Record<AlertKind, { title: string; icon: IconName }> = {
  note: { title: "Note", icon: "info" },
  tip: { title: "Tip", icon: "lightbulb" },
  important: { title: "Important", icon: "messageWarning" },
  warning: { title: "Warning", icon: "warning" },
  caution: { title: "Caution", icon: "octagonAlert" },
};

/** A quote, or a callout when it opened with `[!NOTE]` and the like (markdown/alerts.ts). */
function MarkdownQuote({ node: _n, children, ...props }: any) {
  const kind = props["data-alert"] as AlertKind | undefined;
  const alert = kind ? ALERTS[kind] : undefined;
  if (!alert) return <blockquote {...props}>{children}</blockquote>;
  return (
    <div className="md-alert" data-alert={kind} data-testid="md-alert" role="note">
      <Icon name={alert.icon} size={15} className="md-alert-mark" />
      <div className="md-alert-body">
        <div className="md-alert-title">{alert.title}</div>
        {children}
      </div>
    </div>
  );
}

const components = {
  blockquote: MarkdownQuote,
  // The table's scroll, its edge fade and its copy/CSV toolbar live in MarkdownTable; the
  // `<table>` markup stays the plain table the stylesheet styles.
  table: MarkdownTable,
  // A picture the model drew or fetched, click-to-zoom (but a plain <img> inside a link, or
  // the zoom button would nest inside an anchor — invalid HTML).
  img: MarkdownImg,
  p: MarkdownParagraph,
  // A fenced block is one <pre><code class="language-x"> node; the hast tree is read
  // directly because highlighted output is built from the raw source, not the parsed
  // children (which would double-escape every entity in the code).
  pre: MarkdownPre,
  code: MarkdownInlineCode,
  a: ({ node: _n, href, title, children, ...props }: any) => {
    if (href?.startsWith("artifact:")) {
      return <DeliveredMention path={href.slice("artifact:".length)} title={inlineText(children)} />;
    }
    if (href?.startsWith("file:")) {
      // A citation the model wrote (`:file-citation{…}`, rewritten by directives.ts). The
      // link title carries the purpose, which is why it reads as a chip and not a sentence.
      return <CitationChip path={href.slice("file:".length)} purpose={typeof title === "string" ? title : undefined} />;
    }
    // A link to a FILE the model wrote the ordinary way — `[name](harness/tools/files.py)`,
    // `[name](C:/work/x.ts#L24)`. The reference design accepts this as a file link too, and without it the
    // anchor below would send a relative href to a browser tab that cannot resolve it. A line
    // fragment (`#L24`) opens the file at that line.
    const local = typeof href === "string" ? parseFileLink(href) : undefined;
    if (local !== undefined) return <FileLink path={local.path} line={local.line} title={inlineText(children)} />;
    // The link renderer: class, target, rel (). The provider
    // is what tells a nested inline-code-URL not to emit a second anchor.
    // A link that leaves the app ends in ↗ (owner, 2026-10-13); a mailto or an anchor does not.
    // In the desktop app a web link opens in the panel's Browser and stays in the app, so it
    // wears no ↗ there (owner, 2026-10-20); in a web page it still opens a tab of its own.
    const web = typeof href === "string" && /^https?:/i.test(href);
    return (
      <a href={href} className="external-link" {...props} target="_blank" rel="noopener noreferrer" onClick={web ? openLinkInPanel : undefined}>
        <InsideLink.Provider value={true}>{children}</InsideLink.Provider>
        {web && !canEmbedBrowser() && <Icon name="externalLink" size={11} className="md-link-out" />}
      </a>
    );
  },
};

// artifact:/file: are ours, and a local path is a file link — keep all three through
// the sanitizer (everything else gets the default http/https/mailto policy).
//
// The path case has to be handled BEFORE the default: `defaultUrlTransform` reads a colon
// before the first `/` as a protocol, so it empties every absolute Windows path
// (`C:\work\x.ts` → `""`) and the link would render as nothing at all.
//
// A picture may also be embedded (`![](data:image/png;base64,…)`): the default policy empties
// every `data:` URL, which is right for a link and wrong for an `<img>`, where an image type
// can only be drawn. Kept for `src` and for image types only.
const EMBEDDED_IMAGE_RE = /^data:image\/(?:png|jpe?g|gif|webp|avif|bmp|svg\+xml)[;,]/i;
const urlTransform = (url: string, key?: string) =>
  url.startsWith("artifact:") ||
  url.startsWith("file:") ||
  (key === "src" && EMBEDDED_IMAGE_RE.test(url)) ||
  parseFileLink(url) !== undefined
    ? url
    : defaultUrlTransform(url);

/**
 * Cut a message into its top-level markdown blocks, so each can be memoized separately.
 *
 * The segmentation uses the real parser rather than a blank-line split, because a blank
 * line is not a block boundary: it also appears INSIDE a loose list and inside a fenced
 * code block, and splitting there would turn one list into several (each with its own
 * margins) and cut a code block in half. mdast node positions ARE the boundaries.
 *
 * The known limit is that a block cannot see its neighbours, so a link written as a
 * reference — `[a][1]` in one paragraph, `[1]: https://…` in another — or a footnote would
 * render as literal text. Messages that contain either fall back to a single block below,
 * which costs the memo for that message and nothing else.
 */
const splitter = unified().use(remarkParse).use(remarkGfm).use(remarkMath);

export function splitBlocks(text: string): string[] {
  if (!text) return [];
  // Block splitting costs the message its cross-block references: `[a][1]` in one paragraph
  // with `[1]: https://…` in another, and `[^note]` footnotes, are resolved by the parser at
  // the document level. The reference design parses whole messages, so IT renders these; the honest thing
  // here is to give up the per-block memo for the messages that use them (measured: zero of
  // 1,171 real messages before this), not to render them wrong.
  if (/^\[[^\]]+\]:\s/m.test(text) || /\[\^[^\]]+\]/.test(text)) return [text];
  const root = splitter.parse(text) as { children?: any[] };
  const out: string[] = [];
  for (const node of root.children ?? []) {
    const start = node.position?.start?.offset;
    const end = node.position?.end?.offset;
    // No position means no safe boundary; render the message whole rather than guess.
    if (typeof start !== "number" || typeof end !== "number") return [text];
    out.push(text.slice(start, end));
  }
  return out.length ? out : [text];
}

/**
 * One block, re-rendered only when its own text changes.
 *
 * This is the point of the split. Without it every arriving token re-ran the whole pipeline
 * over the whole answer — parse, mdast to hast, KaTeX over every formula, and a React diff
 * of every node — which is quadratic in the length of the answer, and worst for exactly the
 * long answers that stream longest. With it, a token at the end costs one parse of the
 * message plus a re-render of the final paragraph.
 *
 * Measured 2026-09-08 under jsdom, streaming a 6,278-character answer (40 sections, links,
 * bold, a formula and a price in each) in 157 steps: 7,677 ms without the memo, 960 ms with
 * it. The ratio is not the point — the shape is. The gap widens with the length of the
 * answer, because the work skipped is the part that grows.
 */
const Block = memo(function Block({ text }: { text: string }) {
  return (
    <MarkdownBoundary text={text}>
      <ReactMarkdown
        remarkPlugins={remarkPlugins}
        rehypePlugins={rehypePlugins}
        urlTransform={urlTransform}
        components={components}
      >
        {text}
      </ReactMarkdown>
    </MarkdownBoundary>
  );
});

/**
 * Consecutive image-only lines are ONE block even when blank lines separate them (the reference design's
 * `normalizeConsecutiveMarkdownImageBlocks`): markdown would make each its own paragraph, so
 * four screenshots arrived as four full-width pictures instead of one gallery.
 */
export function mergeImageOnlyLines(text: string): string {
  const lines = text.split("\n");
  const out: string[] = [];
  let afterImage = false;
  for (const line of lines) {
    const isImage = /^\s*!\[[^\]]*\]\([^)]*\)\s*$/.test(line);
    if (isImage && afterImage && out[out.length - 1]?.trim() === "") out.pop();
    out.push(line);
    if (isImage) afterImage = true;
    else if (line.trim() !== "") afterImage = false;
  }
  return out.join("\n");
}

/**
 * Assistant messages rendered as GitHub-flavored markdown (headings, lists, tables, code,
 * links) plus TeX math. Links open externally — never navigate the app shell — except
 * artifact: links, which open the session's own panel.
 *
 * `streaming` marks text that is still arriving: the tail is repaired (`repairStreamingMarkdown`)
 * so a half-written fence or `**bold` does not flicker, and nothing else changes — a settled
 * message must NOT be repaired (the repair completes what only LOOKS open).
 */
function MarkdownInner({
  text,
  streaming = false,
  fileMentions,
}: {
  text: string;
  streaming?: boolean;
  /** The turn's own files, for inline-code mentions (see `FileMentionResolver`). Absent
   *  wherever there is no turn behind the text — a settings description, a skill body. */
  fileMentions?: FileMentionResolver;
}) {
  const { blocks, contents } = useMemo(() => {
    // Directives first: they are not markdown, and their quoted values must not be read as
    // prose. A half-written one is hidden while streaming (never printed mid-sentence).
    const { text: withoutDirectives } = parseDirectives(streaming ? hideOpenTail(text) : text);
    // A run of `**Label:** value` paragraphs is joined before the cut, so it is one block and
    // can be drawn as one list (markdown/facts.ts).
    const prepared = joinFactRuns(mergeImageOnlyLines(prepareMath(withoutDirectives)));
    return {
      blocks: splitBlocks(streaming ? repairStreamingMarkdown(prepared) : prepared),
      // Once the answer has settled: a line at the top that grew while it streamed would push
      // the text being read.
      contents: streaming ? null : contentsOf(headingsOf(prepared)),
    };
  }, [text, streaming]);
  const root = useRef<HTMLDivElement | null>(null);
  // The provider value is memoized on the resolver: a fresh object every render would
  // re-render every memoized block (a context change pierces `memo`) on each token.
  const mentions = useMemo(() => fileMentions, [fileMentions]);
  return (
    <div className="md" ref={root}>
      {contents && <AnswerContents entries={contents} container={root} />}
      <FileMentions.Provider value={mentions}>
        {blocks.map((block, i) => (
          // Index keys are right here and only here: while streaming, blocks are appended at
          // the end and never reordered, so an index is stable for the life of a block.
          <Block key={i} text={block} />
        ))}
      </FileMentions.Provider>
    </div>
  );
}

/**
 * Retired: a `requestAnimationFrame`-coalesced variant of `Markdown`. It was never mounted,
 * and wiring it up (2026-09-22) proved why: rAF does not fire in a hidden tab, so a streaming
 * answer froze until the reader came back. Streaming is handled by `Markdown`'s `streaming`
 * flag instead — repair of the incomplete tail, plus the per-block memo that makes each token
 * cheap — which is the same shape the reference design ships (no batching, `parseIncompleteMarkdown` only).
 */

// A message whose text did not change is not parsed or diffed again when something else on the page does.
export const Markdown = memo(MarkdownInner);
