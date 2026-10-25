// A file's diff as rows (unified or split), with the line comments that ride the next
// message. Shared by the Review tab's file cards and the Files tab's file preview.
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import type { ReviewComment } from "../../types";
import { Icon } from "../Icon";
import { highlightLines, languageForPath, useGrammars } from "../primitives/highlight";
import { additionRows, hunkCaption, parsePatch, withGaps, type DiffRow } from "./parsePatch";
import { codeShape } from "./codeBlocks";
import { IndentGuides } from "./IndentGuides";

export type DiffStyle = "unified" | "split";
export type ExpandMode = "expand" | "collapse";

export function DiffView({
  patch,
  content,
  untracked,
  path,
  style,
  expandMode,
  comments,
  onAddComment,
  onDeleteComment,
  onEditComment,
  renderHunkActions,
  notes,
  renderNote,
}: {
  patch?: string;
  content?: string;
  untracked?: boolean;
  path?: string;
  style: DiffStyle;
  expandMode: ExpandMode;
  comments?: ReviewComment[];
  onAddComment?: (c: ReviewComment) => void;
  onDeleteComment?: (id: string) => void;
  onEditComment?: (id: string, text: string) => void;
  /** Buttons at the right end of a hunk's header (Review: stage, unstage, revert this hunk).
   *  `index` counts the patch's hunks from 0; `header` is the `@@` line as drawn. */
  renderHunkActions?: (index: number, header: string) => ReactNode;
  /** Notes placed on NEW-side line numbers (Reeve's review findings), drawn by `renderNote`
   *  under their line. */
  notes?: { id: string; line: number }[];
  renderNote?: (id: string) => ReactNode;
}) {
  const rows = useMemo(
    () => (untracked ? additionRows(content || "") : withGaps(parsePatch((patch || "").split("\n")))),
    [patch, content, untracked],
  );
  const lang = languageForPath(path || "");
  const shown = expandMode === "collapse" ? rows.filter((r) => r.kind !== "ctx") : rows;
  // Indentation guides under the code rows, as in the file view (owner, 2026-10-15). A row that is
  // not code (a hunk's header, a gap) reads as a line at the left edge, so no guide runs across it.
  const shape = useMemo(
    () => codeShape(shown.map((r) => (r.kind === "add" || r.kind === "del" || r.kind === "ctx" ? r.text : "|"))),
    [shown],
  );
  // "Changes only" hides the context, so a count of unmodified lines would understate it.
  const withCount = expandMode !== "collapse";
  // A file with one hunk: its buttons would be the file's own Stage / Revert again.
  const hunkCount = rows.filter((r) => r.kind === "hunk").length;
  const hunkActions = hunkCount > 1 ? renderHunkActions : undefined;
  const [editing, setEditing] = useState<number | null>(null);
  const submit = (line: number, text: string) => {
    if (!text || !onAddComment) return;
    onAddComment({ id: `rc-${Date.now()}-${line}`, file: path || "", line, text, origin: "review" });
    setEditing(null);
  };
  if (style === "split")
    return (
      <SplitDiff
        rows={shown}
        lang={lang}
        comments={comments}
        editing={editing}
        onEdit={onAddComment ? setEditing : undefined}
        onSubmit={submit}
        onDeleteComment={onDeleteComment}
        onEditComment={onEditComment}
        renderHunkActions={hunkActions}
        withCount={withCount}
        notes={notes}
        renderNote={renderNote}
      />
    );
  // A comment is anchored by line number; in a unified view a modified line's old and new
  // rows share that number, so render each comment once (the first row that matches).
  const rendered = new Set<string>();
  const editorSeen = { done: false };
  let hunkNo = -1;
  // A new file is all additions: it reads as code with a green rule, not as one block of green,
  // and has no old line numbers to keep a column for (Review mockup, 2026-10-14).
  return (
    <div className={"rev-diff" + (untracked ? " all-new" : "")} data-testid="diffview">
      {shown.map((r, i) => {
        if (r.kind === "hunk") {
          hunkNo++;
          const caption = hunkCaption(r, withCount);
          // A first hunk at the top of the file, with nothing to say and nothing to do,
          // needs no row at all.
          if (!caption && !hunkActions) return null;
          return (
            <div className="diffline-group" key={i}>
              <div className="diffline hunk" title={r.text}>
                <span className="dl-gutter" />
                <span className="dl-old" />
                <span className="dl-new" />
                <span className={"dl-text dl-hunk" + (hunkActions ? " with-actions" : "")}>
                  <span className="dl-hunk-caption">{caption}</span>
                  {hunkActions && <span className="dl-hunk-actions">{hunkActions(hunkNo, r.text)}</span>}
                </span>
              </div>
            </div>
          );
        }
        const line = r.new ?? r.old ?? 0;
        const code = r.kind === "add" || r.kind === "del" || r.kind === "ctx";
        const rowComments = (comments || []).filter((c) => c.line === line && !rendered.has(c.id));
        rowComments.forEach((c) => rendered.add(c.id));
        const rowNotes = r.kind !== "del" && r.new !== undefined ? (notes || []).filter((n) => n.line === r.new && !rendered.has(n.id)) : [];
        rowNotes.forEach((n) => rendered.add(n.id));
        const showEditor = editing === line && !editorSeen.done;
        if (showEditor) editorSeen.done = true;
        return (
          <div className="diffline-group" key={i}>
            <div className={"diffline " + r.kind + (rowComments.length ? " commented" : "") + (rowNotes.length ? " noted" : "")}>
              <span className="dl-gutter">
                {code && (
                  <button
                    className="dl-add"
                    title={`Comment on line ${line}`}
                    aria-label={`Comment on line ${line}`}
                    onClick={() => {
                      setEditing(line);
                    }}
                  >
                    ＋
                  </button>
                )}
              </span>
              <span className="dl-old">{r.old ?? ""}</span>
              <span className="dl-new">{r.new ?? ""}</span>
              <span className="dl-text">
                {code && <IndentGuides level={shape.levels[i]} unit={shape.unit} />}
                {code ? <DiffText text={r.text} lang={lang} /> : r.text}
              </span>
            </div>
            {(rowComments.length > 0 || showEditor || rowNotes.length > 0) && (
              <div className="comment-band">
                {rowNotes.map((n) => (
                  <div key={n.id}>{renderNote?.(n.id)}</div>
                ))}
                {rowComments.map((c) => (
                  <LineCommentCard key={c.id} comment={c} onDelete={onDeleteComment} onEdit={onEditComment} />
                ))}
                {showEditor && (
                  <LineCommentEditor
                    line={line}
                    onCancel={() => setEditing(null)}
                    onSubmit={(text) => submit(line, text)}
                  />
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

// A line comment rendered under its line: the comment text with a
// `line N` meta, and a remove ✕. Clicking the card re-opens the editor to change it; because
// the comment lives in App state, editing here (or from the composer chip) updates both.
export function LineCommentCard({
  comment,
  onDelete,
  onEdit,
}: {
  comment: ReviewComment;
  onDelete?: (id: string) => void;
  onEdit?: (id: string, text: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!menuOpen) return;
    const close = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setMenuOpen(false);
    };
    window.addEventListener("mousedown", close);
    return () => window.removeEventListener("mousedown", close);
  }, [menuOpen]);
  if (editing) {
    return (
      <LineCommentEditor
        line={comment.line}
        initial={comment.text}
        submitLabel="Save"
        onCancel={() => setEditing(false)}
        onSubmit={(text) => {
          onEdit?.(comment.id, text);
          setEditing(false);
        }}
      />
    );
  }
  return (
    <div className="diff-comment" data-testid="diff-comment" ref={ref}>
      <div className="diff-comment-body">
        <span className="diff-comment-text">{comment.text}</span>
        <span className="diff-comment-meta">line {comment.line}</span>
      </div>
      {/* The overflow: a `…` that opens Edit / Delete (the card itself stays a display). */}
      <div className="diff-comment-menu-wrap">
        <button
          className="diff-comment-more"
          data-testid="diff-comment-more"
          aria-label="More options"
          aria-haspopup="menu"
          aria-expanded={menuOpen}
          title="More options"
          onClick={() => setMenuOpen((v) => !v)}
        >
          <Icon name="moreHorizontal" size={15} />
        </button>
        {menuOpen && (
          <div className="diff-comment-menu" role="menu" data-testid="diff-comment-menu">
            <button
              role="menuitem"
              data-testid="diff-comment-edit"
              onClick={() => {
                setMenuOpen(false);
                setEditing(true);
              }}
            >
              Edit
            </button>
            <button
              role="menuitem"
              data-testid="diff-comment-delete"
              onClick={() => {
                setMenuOpen(false);
                onDelete?.(comment.id);
              }}
            >
              Delete
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

// The comment composer: heading, textarea, then a footer with the anchor
// (`line N`) and Cancel / Comment. `initial` pre-fills it when editing an existing comment;
// Esc cancels and Cmd/Ctrl+Enter submits.
export function LineCommentEditor({
  line,
  initial = "",
  submitLabel = "Comment",
  onCancel,
  onSubmit,
  testid = "diff-comment-editor",
}: {
  line: number;
  initial?: string;
  submitLabel?: string;
  onCancel: () => void;
  onSubmit: (text: string) => void;
  testid?: string;
}) {
  const [draft, setDraft] = useState(initial);
  const canSubmit = !!draft.trim();
  return (
    <div className="diff-comment-editor" data-testid={testid}>
      <div className="diff-comment-heading">Comment</div>
      <textarea
        autoFocus
        value={draft}
        placeholder="Add context for this change"
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Escape") onCancel();
          if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && canSubmit) onSubmit(draft.trim());
        }}
      />
      <div className="diff-comment-actions">
        <span className="diff-comment-line">line {line}</span>
        <button className="btn sm" onClick={onCancel}>
          Cancel
        </button>
        <button className="btn sm primary" disabled={!canSubmit} onClick={() => onSubmit(draft.trim())}>
          {submitLabel}
        </button>
      </div>
    </div>
  );
}

// One diff line, highlighted as the file's language.
export function DiffText({ text, lang }: { text: string; lang?: string }) {
  const grammars = useGrammars(lang);
  const spans = useMemo(
    () => (lang ? highlightLines(text, lang)?.[0] : undefined),
    [text, lang, grammars],
  );
  if (!spans) return <>{text}</>;
  return (
    <>
      {spans.map((s, i) => (
        <span key={i} style={s.style}>
          {s.text}
        </span>
      ))}
    </>
  );
}

export function SplitDiff({
  rows,
  lang,
  comments,
  editing,
  onEdit,
  onSubmit,
  onDeleteComment,
  onEditComment,
  renderHunkActions,
  withCount = true,
  notes,
  renderNote,
}: {
  rows: DiffRow[];
  lang?: string;
  /** Whether a hunk's row counts the unchanged lines before it. */
  withCount?: boolean;
  notes?: { id: string; line: number }[];
  renderNote?: (id: string) => ReactNode;
  // Line comments, as the unified view takes them (review audit V15: Split dropped them).
  comments?: ReviewComment[];
  editing?: number | null;
  onEdit?: (line: number | null) => void;
  onSubmit?: (line: number, text: string) => void;
  onDeleteComment?: (id: string) => void;
  onEditComment?: (id: string, text: string) => void;
  renderHunkActions?: (index: number, header: string) => ReactNode;
}) {
  const paired: { old?: DiffRow; new?: DiffRow }[] = [];
  for (let i = 0; i < rows.length; i++) {
    const r = rows[i];
    if (r.kind === "add" || r.kind === "del") {
      const dels: DiffRow[] = [];
      const adds: DiffRow[] = [];
      while (i < rows.length && rows[i].kind === "del") dels.push(rows[i++]);
      while (i < rows.length && rows[i].kind === "add") adds.push(rows[i++]);
      i--;
      for (let k = 0; k < Math.max(dels.length, adds.length); k++) paired.push({ old: dels[k], new: adds[k] });
    } else {
      paired.push({ old: r, new: r });
    }
  }
  const render = (r?: DiffRow) => {
    if (!r) return null;
    return r.kind === "add" || r.kind === "del" || r.kind === "ctx" ? <DiffText text={r.text} lang={lang} /> : r.text;
  };
  // A comment anchors to the new side's line (the old side's for a pure deletion), shown once.
  const rendered = new Set<string>();
  const editorSeen = { done: false };
  let hunkNo = -1;
  return (
    <div className="rev-diff split" data-testid="diffview">
      {paired.map((p, i) => {
        const isHunk = p.new?.kind === "hunk";
        if (isHunk) {
          hunkNo++;
          const caption = hunkCaption(p.new!, withCount);
          if (!caption && !renderHunkActions) return null;
          return (
            <div className="diffline-group" key={i}>
              <div className="splitrow hunk" title={p.new!.text}>
                <span className="dl-old" />
                <span className="dl-text hunk" />
                <span className="dl-new" />
                <span className={"dl-text hunk dl-hunk" + (renderHunkActions ? " with-actions" : "")}>
                  <span className="dl-hunk-caption">{caption}</span>
                  {renderHunkActions && <span className="dl-hunk-actions">{renderHunkActions(hunkNo, p.new!.text)}</span>}
                </span>
              </div>
            </div>
          );
        }
        const line = p.new?.new ?? p.old?.old ?? 0;
        const code = [p.old?.kind, p.new?.kind].some((kind) => kind === "add" || kind === "del" || kind === "ctx");
        const rowComments = (comments || []).filter((c) => c.line === line && !rendered.has(c.id));
        rowComments.forEach((c) => rendered.add(c.id));
        const newLine = p.new && p.new.kind !== "del" ? p.new.new : undefined;
        const rowNotes = newLine !== undefined ? (notes || []).filter((n) => n.line === newLine && !rendered.has(n.id)) : [];
        rowNotes.forEach((n) => rendered.add(n.id));
        const showEditor = editing === line && !editorSeen.done;
        if (showEditor) editorSeen.done = true;
        return (
          <div className="diffline-group" key={i}>
            <div className={"splitrow" + (rowComments.length ? " commented" : "") + (rowNotes.length ? " noted" : "")}>
              <span className="dl-old">
                {code && onEdit && (
                  <button
                    className="dl-add"
                    title={`Comment on line ${line}`}
                    aria-label={`Comment on line ${line}`}
                    onClick={() => onEdit(line)}
                  >
                    ＋
                  </button>
                )}
                {p.old?.old ?? ""}
              </span>
              <span className={"dl-text " + (p.old?.kind ?? "")}>{render(p.old)}</span>
              <span className="dl-new">{p.new?.new ?? ""}</span>
              <span className={"dl-text " + (p.new?.kind ?? "")}>{render(p.new)}</span>
            </div>
            {(rowComments.length > 0 || showEditor || rowNotes.length > 0) && (
              <div className="comment-band">
                {rowNotes.map((n) => (
                  <div key={n.id}>{renderNote?.(n.id)}</div>
                ))}
                {rowComments.map((c) => (
                  <LineCommentCard key={c.id} comment={c} onDelete={onDeleteComment} onEdit={onEditComment} />
                ))}
                {showEditor && onSubmit && (
                  <LineCommentEditor
                    line={line}
                    onCancel={() => onEdit?.(null)}
                    onSubmit={(text) => onSubmit(line, text)}
                  />
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
