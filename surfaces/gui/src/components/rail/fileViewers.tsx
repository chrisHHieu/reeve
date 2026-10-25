// What a file looks like once it is open: the viewers the file view picks between (workspace
// panel redesign, 2026-10-11). Moved out of RightRail.tsx unchanged; the view that chooses
// among them is rail/FilePreview.tsx.
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
// Emits the asset URL only; the worker itself loads lazily with the pdfjs chunk.
import pdfWorkerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import type { ReviewComment } from "../../types";
import { Icon } from "../Icon";
import { highlightLines, useGrammars } from "../primitives/highlight";
import { LineCommentCard, LineCommentEditor } from "./DiffView";
import { Disclosure } from "../../ui/Disclosure";
import { activeGuide, codeShape, foldRange } from "./codeBlocks";
import { IndentGuides } from "./IndentGuides";

// A small collapsible JSON tree (PI's Tree mode), capped so a huge document stays a list.
function JsonNode({ name, value, depth }: { name: string | null; value: unknown; depth: number }) {
  const [open, setOpen] = useState(depth < 1);
  if (value === null || typeof value !== "object") {
    return (
      <div className="json-row" style={{ paddingLeft: 8 + depth * 14 }}>
        {name !== null && <span className="json-key">{name}:</span>}
        <span className={"json-value is-" + (value === null ? "null" : typeof value)}>
          {JSON.stringify(value)}
        </span>
      </div>
    );
  }
  const entries: [string, unknown][] = Array.isArray(value)
    ? value.map((item, index) => [String(index), item])
    : Object.entries(value as Record<string, unknown>);
  const shown = entries.slice(0, 100);
  return (
    <>
      <button
        type="button"
        className="json-row json-toggle"
        style={{ paddingLeft: 8 + depth * 14 }}
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        <Icon name={open ? "chevronDown" : "chevronRight"} size={11} />
        {name !== null && <span className="json-key">{name}:</span>}
        <span className="json-brace">
          {Array.isArray(value) ? `Array(${entries.length})` : `Object(${entries.length})`}
        </span>
      </button>
      {open &&
        shown.map(([key, item]) => <JsonNode key={key} name={key} value={item} depth={depth + 1} />)}
      {open && entries.length > shown.length && (
        <div className="json-row json-more" style={{ paddingLeft: 8 + (depth + 1) * 14 }}>
          … {entries.length - shown.length} more
        </div>
      )}
    </>
  );
}

export function JsonTree({ text }: { text: string }) {
  const parsed = useMemo(() => {
    try {
      return { ok: true as const, value: JSON.parse(text) as unknown };
    } catch (error) {
      return { ok: false as const, error: String((error as Error)?.message || error) };
    }
  }, [text]);
  if (!parsed.ok) {
    return <div className="rail-error">Could not parse this JSON: {parsed.error}</div>;
  }
  return (
    <div className="json-tree" data-testid="json-tree">
      <JsonNode name={null} value={parsed.value} depth={0} />
    </div>
  );
}

// Line-numbered code/text, highlighted with the app's shiki theme.
// When `onAddComment` is given it doubles as a review surface: hovering a line reveals a
// + gutter button, and comments render under their line.
//
// It reads like an editor (owner, 2026-10-15: "like VS Code"): a thin guide under each level of
// indentation, stronger for the block of the line last clicked, and an arrow beside a block's
// first line that folds the block away (`codeBlocks.ts` reads both from the indentation). The
// arrows show while the pointer is over the code, and a folded block's arrow stays.
export function CodeView({
  text,
  lang,
  path,
  focusLine,
  comments,
  onAddComment,
  onDeleteComment,
  onEditComment,
}: {
  text: string;
  lang?: string;
  path?: string;
  /** The line an answer opened the file at: scrolled to the middle and marked. */
  focusLine?: { line: number; n: number };
  comments?: ReviewComment[];
  onAddComment?: (c: ReviewComment) => void;
  onDeleteComment?: (id: string) => void;
  onEditComment?: (id: string, text: string) => void;
}) {
  const grammars = useGrammars(lang);
  const spans = useMemo(() => highlightLines(text, lang), [text, lang, grammars]);
  const plain = useMemo(() => text.split("\n"), [text]);
  const shape = useMemo(() => codeShape(plain), [plain]);
  const folds = useMemo(() => plain.map((_, i) => foldRange(plain, shape, i)), [plain, shape]);
  const [folded, setFolded] = useState<ReadonlySet<number>>(() => new Set());
  const [picked, setPicked] = useState<number | null>(null);
  const guide = useMemo(() => (picked === null ? null : activeGuide(plain, shape, picked)), [plain, shape, picked]);
  const [editing, setEditing] = useState<number | null>(null);
  // A new text is a new file: its folds and its picked line belong to the old one.
  useEffect(() => {
    setFolded(new Set());
    setPicked(null);
  }, [text]);
  const submit = (n: number, t: string) => {
    if (!t || !onAddComment) return;
    onAddComment({ id: `fc-${Date.now()}-${n}`, file: path || "", line: n, text: t, origin: "file" });
    setEditing(null);
  };
  const toggleFold = (i: number) =>
    setFolded((current) => {
      const next = new Set(current);
      if (next.has(i)) next.delete(i);
      else next.add(i);
      return next;
    });
  const commentable = !!onAddComment;
  // A modified file's rows share numbers with their neighbours in the diff; here each file
  // line is unique, but keep the "render each comment once" guard for parity.
  const rendered = new Set<string>();
  const editorSeen = { done: false };
  const box = useRef<HTMLDivElement | null>(null);
  const marked = focusLine && focusLine.line <= plain.length ? focusLine.line : undefined;
  useEffect(() => {
    if (!marked) return;
    // A line an answer points at is never left inside a fold.
    setFolded((current) => {
      const open = [...current].filter((h) => {
        const range = folds[h];
        return !range || marked - 1 < range.from || marked - 1 > range.to;
      });
      return open.length === current.size ? current : new Set(open);
    });
    box.current?.querySelector<HTMLElement>(`[data-line="${marked}"]`)?.scrollIntoView?.({ block: "center" });
  }, [marked, focusLine?.n, folds]);
  const lines = spans ?? plain.map((l) => [{ text: l, style: {} }]);

  const row = (i: number) => {
    const line = lines[i] ?? [];
    const n = i + 1;
    const rowComments = commentable ? (comments || []).filter((c) => c.line === n && !rendered.has(c.id)) : [];
    rowComments.forEach((c) => rendered.add(c.id));
    const showEditor = editing === n && !editorSeen.done;
    if (showEditor) editorSeen.done = true;
    const range = folds[i];
    const isFolded = !!range && folded.has(i);
    const inGuide = guide && i >= guide.from && i <= guide.to ? guide.column : undefined;
    return (
      <div className="rev-code-row" key={`l${i}`}>
        <div
          className={"rev-code-line" + (rowComments.length ? " commented" : "")}
          data-line={n}
          data-focus={n === marked || undefined}
          onClick={() => {
            // A drag that selected text is a selection, not a pick.
            if (window.getSelection?.()?.isCollapsed === false) return;
            setPicked(i);
          }}
        >
          {commentable && (
            <span className="rev-code-gutter">
              <button
                className="dl-add"
                title={`Comment on line ${n}`}
                aria-label={`Comment on line ${n}`}
                onClick={() => setEditing(n)}
              >
                ＋
              </button>
            </span>
          )}
          <span className="rev-code-no">
            {n}
            {range && (
              <button
                type="button"
                className="rev-code-fold"
                data-testid="code-fold"
                data-folded={isFolded || undefined}
                aria-expanded={!isFolded}
                aria-label={isFolded ? `Unfold lines ${range.from + 1}–${range.to + 1}` : `Fold lines ${range.from + 1}–${range.to + 1}`}
                onClick={(event) => {
                  event.stopPropagation();
                  toggleFold(i);
                }}
              >
                <Icon name="chevronRight" size={11} />
              </button>
            )}
          </span>
          <span className="rev-code-text">
            <IndentGuides level={shape.levels[i]} unit={shape.unit} active={inGuide} />
            {line.length === 0 ? " " : line.map((s, j) => <span key={j} style={s.style}>{s.text}</span>)}
            {isFolded && range && (
              <button
                type="button"
                className="rev-code-more"
                data-testid="code-folded"
                title={`Show ${range.to - range.from + 1} hidden lines`}
                onClick={(event) => {
                  event.stopPropagation();
                  toggleFold(i);
                }}
              >
                ⋯
              </button>
            )}
          </span>
        </div>
        {(rowComments.length > 0 || showEditor) && (
          <div className="comment-band">
            {rowComments.map((c) => (
              <LineCommentCard key={c.id} comment={c} onDelete={onDeleteComment} onEdit={onEditComment} />
            ))}
            {showEditor && (
              <LineCommentEditor
                line={n}
                testid="file-comment-editor"
                onCancel={() => setEditing(null)}
                onSubmit={(t) => submit(n, t)}
              />
            )}
          </div>
        )}
      </div>
    );
  };

  // A block's body sits in its own disclosure, so a fold closes and opens as a motion (the
  // app's rule for every show and hide) and the rows after it move up with it.
  const block = (from: number, to: number): ReactNode[] => {
    const out: ReactNode[] = [];
    let i = from;
    while (i <= to) {
      out.push(row(i));
      const range = folds[i];
      if (range) {
        out.push(
          <Disclosure key={`b${i}`} open={!folded.has(i)} className="rev-code-block">
            {block(range.from, range.to)}
          </Disclosure>,
        );
        i = range.to + 1;
      } else {
        i++;
      }
    }
    return out;
  };

  return (
    <div ref={box} className={"rev-code" + (commentable ? " commentable" : "")} data-testid="code-view">
      {block(0, lines.length - 1)}
    </div>
  );
}

const MAX_TABLE_ROWS = 500;

function GridTable({ rows, note }: { rows: unknown[][]; note?: string }) {
  const [head, ...body] = rows;
  return (
    <div className="artifact-tablewrap">
      <table className="artifact-table">
        {head && (
          <thead>
            <tr>{head.map((c, i) => <th key={i}>{String(c ?? "")}</th>)}</tr>
          </thead>
        )}
        <tbody>
          {body.slice(0, MAX_TABLE_ROWS).map((r, i) => (
            <tr key={i}>{r.map((c, j) => <td key={j}>{String(c ?? "")}</td>)}</tr>
          ))}
        </tbody>
      </table>
      {(note || body.length > MAX_TABLE_ROWS) && (
        <div className="rail-muted artifact-table-note">
          {note}
          {body.length > MAX_TABLE_ROWS ? ` Showing first ${MAX_TABLE_ROWS} of ${body.length} rows.` : ""}
        </div>
      )}
    </div>
  );
}

function parseCsv(text: string): string[][] {
  const delim = text.includes("\t") && !text.split("\n")[0]?.includes(",") ? "\t" : ",";
  const rows: string[][] = [];
  let row: string[] = [];
  let cell = "";
  let quoted = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (quoted) {
      if (ch === '"') {
        if (text[i + 1] === '"') {
          cell += '"';
          i++;
        } else quoted = false;
      } else cell += ch;
    } else if (ch === '"') quoted = true;
    else if (ch === delim) {
      row.push(cell);
      cell = "";
    } else if (ch === "\n" || ch === "\r") {
      if (ch === "\r" && text[i + 1] === "\n") i++;
      row.push(cell);
      cell = "";
      rows.push(row);
      row = [];
    } else cell += ch;
  }
  if (cell !== "" || row.length) {
    row.push(cell);
    rows.push(row);
  }
  return rows.filter((r) => r.some((c) => c !== ""));
}

export function CsvTable({ text }: { text: string }) {
  const rows = parseCsv(text);
  if (!rows.length) return <div className="rail-muted artifact-table-note">Empty file.</div>;
  return <GridTable rows={rows} />;
}

export function PdfViewer({ dataUrl }: { dataUrl: string }) {
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const holder = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let cancelled = false;
    setError("");
    setLoading(true);
    const base64 = dataUrl.split(",")[1] || "";
    import("pdfjs-dist")
      .then(async (pdfjs) => {
        pdfjs.GlobalWorkerOptions.workerSrc = pdfWorkerUrl;
        const bytes = Uint8Array.from(atob(base64), (c) => c.charCodeAt(0));
        const doc = await pdfjs.getDocument({ data: bytes }).promise;
        const el = holder.current;
        if (cancelled || !el) return;
        el.innerHTML = "";
        const width = el.clientWidth || 640;
        const dpr = window.devicePixelRatio || 1;
        for (let i = 1; i <= doc.numPages; i++) {
          const page = await doc.getPage(i);
          const base = page.getViewport({ scale: 1 });
          const viewport = page.getViewport({ scale: (width / base.width) * dpr });
          const canvas = document.createElement("canvas");
          canvas.width = viewport.width;
          canvas.height = viewport.height;
          canvas.className = "artifact-pdf-page";
          await page.render({ canvasContext: canvas.getContext("2d")!, viewport }).promise;
          if (cancelled) return;
          el.appendChild(canvas);
        }
        setLoading(false);
      })
      .catch((e) => !cancelled && setError(String(e?.message || e)));
    return () => {
      cancelled = true;
    };
  }, [dataUrl]);

  if (error) return <div className="rail-error artifact-table-note">Could not render PDF: {error}</div>;
  return (
    <div className="artifact-pdfjs">
      {loading && <div className="rail-muted artifact-table-note">Rendering PDF…</div>}
      <div ref={holder} />
    </div>
  );
}

export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes)) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
