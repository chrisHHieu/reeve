// One open file (workspace panel redesign, 2026-10-11).
//
// The head says what the file is and what can be done to it; the body shows it. A file with a
// rendered form (Markdown, HTML, SVG, CSV, JSON) has two sides, the drawing and the text. A
// code or text file is read as itself and has one Edit button. Nothing typed is dropped without
// asking: leaving a file with unsaved edits asks first, and the draft outlives the tab.
import { forwardRef, useEffect, useImperativeHandle, useMemo, useRef, useState, type ReactNode } from "react";
import { revealArtifact, writeSessionEntry, type ArtifactContent, type ArtifactInfo } from "../../api";
import type { ReviewComment } from "../../types";
import { FileIcon } from "../FileIcon";
import { Icon, type IconName } from "../Icon";
import { Markdown } from "../Markdown";
import { MarkdownFileContext } from "../markdown/fileSource";
import { AnchoredPopover } from "../primitives/AnchoredPopover";
import { languageForPath } from "../primitives/highlight";
import {
  encodingLabel,
  fileView,
  hasRenderedForm,
  isUtf8,
  isTextKind,
  readDraft,
  readFileMode,
  renderedLabel,
  revealLabel,
  writeDraft,
  writeFileMode,
} from "./fileKinds";
import { CodeView, CsvTable, formatBytes, JsonTree, PdfViewer } from "./fileViewers";
import { MediaView } from "./MediaView";
import { NotebookView } from "./NotebookView";
import { OfficeView } from "./OfficeView";
import { SheetView } from "./SheetView";
import { PageFrame } from "./PageFrame";
import "./fileview.css";

/** What the file's own menu can ask of the folder it lives in. */
export type FileMenuAction = "rename" | "move" | "delete";

export interface FilePreviewHandle {
  /** Run `next` once it is safe to leave this file: at once, or after the person has saved or
   *  discarded what they typed. */
  requestLeave: (next: () => void) => void;
}

interface Props {
  sessionId: string;
  info: ArtifactInfo;
  content: ArtifactContent | null;
  /** Read the file again; with an encoding, as that (the person named it). */
  onReload: (encoding?: string) => Promise<void>;
  onClose: () => void;
  onOpenEntry: (path: string) => void;
  comments?: ReviewComment[];
  onAddComment?: (c: ReviewComment) => void;
  onDeleteComment?: (id: string) => void;
  onEditComment?: (id: string, text: string) => void;
  /** No writable session folder: the file opens as a viewer, never an editor. */
  readOnly?: boolean;
  /** Rename, move or delete this file (the same actions as its row in the tree). */
  onAction?: (kind: FileMenuAction) => void;
  /** A quiet line under the head: what the agent did to this file. */
  note?: ReactNode;
  /** The line an answer opened this file at (`file.py:42`): scrolled to and marked. `n` makes
   *  a second click on the same line scroll again. */
  focusLine?: { line: number; n: number };
}

/** The name of the folder a file sits in, or "" at a root. */
const parentName = (path: string): string => {
  const parts = path.split(/[\\/]/).filter(Boolean);
  return parts.length > 1 ? parts[parts.length - 2] : "";
};

const svgDataUrl = (text: string) => `data:image/svg+xml;charset=utf-8,${encodeURIComponent(text)}`;

export const FilePreview = forwardRef<FilePreviewHandle, Props>(function FilePreview(
  {
    sessionId,
    info,
    content,
    onReload,
    onClose,
    onOpenEntry,
    comments,
    onAddComment,
    onDeleteComment,
    onEditComment,
    readOnly,
    onAction,
    note,
    focusLine,
  },
  ref,
) {
  const view = fileView(info.path);
  const rendered = hasRenderedForm(view);
  const [reloadKey, setReloadKey] = useState(0);
  const [menuOpen, setMenuOpen] = useState(false);
  const [draft, setDraft] = useState<string | null>(() => readDraft(sessionId, info.path));
  const [editing, setEditing] = useState(() => draft !== null || (rendered && readFileMode(view) === "edit"));
  const [saving, setSaving] = useState(false);
  const [savedFlash, setSavedFlash] = useState(false);
  const [conflict, setConflict] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [leaving, setLeaving] = useState<(() => void) | null>(null);
  const [svgSize, setSvgSize] = useState("");
  const [encodingOpen, setEncodingOpen] = useState(false);
  // The person named this file's encoding here: the note stays, so they can name another.
  const [named, setNamed] = useState(false);
  const encodingRef = useRef<HTMLButtonElement | null>(null);
  const moreRef = useRef<HTMLButtonElement | null>(null);
  const menuRef = useRef<HTMLDivElement | null>(null);

  const text = !content?.error && isTextKind(content?.kind) && typeof content?.content === "string";
  // A file cut at the read ceiling is never saved from here: the save would be the cut.
  const editable = text && !readOnly && !content?.truncated;
  const saved = content?.content ?? "";
  const dirty = editable && draft !== null && draft !== saved;
  const shown = draft ?? saved;
  const isHtml = text && view === "html";
  const isMedia = content?.kind === "video" || content?.kind === "audio";
  const isApp = isMedia || content?.kind === "sheet" || content?.kind === "pdf" || content?.kind === "office";
  const inEditor = editable && editing;
  // A code file's editor is a place you go into and come out of; a rendered file's is a side.
  const codeEditing = inEditor && !rendered;

  useEffect(() => {
    const kept = readDraft(sessionId, info.path);
    setDraft(kept);
    setEditing(kept !== null || (hasRenderedForm(fileView(info.path)) && readFileMode(fileView(info.path)) === "edit"));
    setConflict(false);
    setSaveError(null);
    setLeaving(null);
    setMenuOpen(false);
    setEncodingOpen(false);
    setNamed(false);
    setSvgSize("");
  }, [sessionId, info.path]);

  const type = (value: string) => {
    setDraft(value);
    writeDraft(sessionId, info.path, value === saved ? null : value);
  };
  const dropDraft = () => {
    setDraft(null);
    writeDraft(sessionId, info.path, null);
  };

  const save = async (force = false): Promise<boolean> => {
    if (!editable || saving) return false;
    setSaving(true);
    setSaveError(null);
    const result = await writeSessionEntry(
      sessionId,
      info.path,
      draft ?? saved,
      force ? undefined : content?.mtime_ms,
      content?.encoding,
    ).catch(() => null);
    setSaving(false);
    if (!result) {
      setSaveError("Could not save this file.");
      return false;
    }
    if (result.conflict) {
      setConflict(true);
      setSaveError("This file changed on disk since you opened it.");
      return false;
    }
    if (!result.ok) {
      setSaveError(result.error || "Could not save this file.");
      return false;
    }
    setConflict(false);
    dropDraft();
    await onReload();
    setSavedFlash(true);
    window.setTimeout(() => setSavedFlash(false), 2500);
    return true;
  };

  const requestLeave = (next: () => void) => {
    if (dirty) setLeaving(() => next);
    else next();
  };
  useImperativeHandle(ref, () => ({ requestLeave }));

  const pickSide = (edit: boolean) => {
    setEditing(edit);
    writeFileMode(view, edit ? "edit" : "rendered");
  };
  const reload = async (encoding?: string) => {
    dropDraft();
    setConflict(false);
    setSaveError(null);
    // The encoding the person named stays named across a reload.
    await onReload(encoding ?? (isUtf8(content?.encoding) ? undefined : content?.encoding));
    setReloadKey((k) => k + 1);
  };

  const closeMenu = (refocus = true) => {
    setMenuOpen(false);
    if (refocus) moreRef.current?.focus();
  };
  // The first action takes focus, so the menu is usable from the keyboard as it opens.
  useEffect(() => {
    if (!menuOpen) return;
    const frame = requestAnimationFrame(() => menuRef.current?.querySelector("button")?.focus());
    return () => cancelAnimationFrame(frame);
  }, [menuOpen]);

  useEffect(() => {
    if (!leaving) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setLeaving(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [leaving]);

  const item = (testid: string, icon: IconName, label: string, run: () => void, danger = false) => (
    <button
      type="button"
      role="menuitem"
      className={danger ? "danger" : undefined}
      data-testid={testid}
      onClick={() => {
        closeMenu(false);
        run();
      }}
    >
      <Icon name={icon} size={14} />
      <span>{label}</span>
    </button>
  );

  const sideLabel = renderedLabel(view);
  const markdownFile = useMemo(
    () => ({ sessionId, path: info.path, diagrams: true }),
    [sessionId, info.path],
  );
  const body = () => {
    if (!content) return <FileSkeleton />;
    if (content.error) {
      return (
        <div className="artifact-open-prompt" data-testid="fv-unreadable">
          <p>{content.error}</p>
          <button type="button" className="btn sm" onClick={() => revealArtifact(sessionId, info.path, "reveal")}>
            {revealLabel()}
          </button>
        </div>
      );
    }
    if (inEditor) {
      return (
        <FileEditor
          name={info.name}
          value={shown}
          // Prose wraps; anything line-shaped keeps its lines, with their numbers.
          lined={view !== "markdown"}
          autoFocus={codeEditing}
          onChange={type}
          onSave={() => void save()}
        />
      );
    }
    if (content.kind === "image") return <img className="artifact-image" src={content.data_url} alt={info.name} />;
    if (content.kind === "pdf") return <PdfViewer dataUrl={content.data_url || ""} />;
    if (content.kind === "sheet") return <SheetView sessionId={sessionId} path={info.path} name={info.name} dataUrl={content.data_url || ""} />;
    if (content.kind === "video" || content.kind === "audio") {
      return <MediaView sessionId={sessionId} path={info.path} name={info.name} kind={content.kind} size={content.size} />;
    }
    if (content.kind === "folder") {
      return (
        <div className="rev-folders" data-testid="artifact-folder">
          {(content.entries || []).map((e) => (
            <button
              key={e.name}
              className="rev-folder-row"
              onClick={() => onOpenEntry(`${info.path.replace(/\/+$/, "")}/${e.name}`)}
            >
              <FileIcon path={e.name} type={e.dir ? "directory" : "file"} size={14} />
              <span className="rev-folder-name">{e.name}</span>
              {!e.dir && <span className="rev-folder-size">{formatBytes(e.size)}</span>}
            </button>
          ))}
          {!content.entries?.length && <div className="rev-empty-small">This folder is empty.</div>}
        </div>
      );
    }
    if (content.kind === "office") return <OfficeView sessionId={sessionId} path={info.path} name={info.name} />;
    if (view === "html") {
      // The page itself, served from its folder (PageFrame): the saved file, so
      // what is typed and not saved is said rather than shown.
      return <PageFrame key={reloadKey} sessionId={sessionId} path={info.path} name={info.name} unsaved={dirty} />;
    }
    if (view === "notebook") {
      return (
        <MarkdownFileContext.Provider value={markdownFile}>
          <NotebookView text={shown} />
        </MarkdownFileContext.Provider>
      );
    }
    if (view === "markdown") {
      return (
        <div className="artifact-md">
          {/* A file has a folder: its pictures are read from there, and its diagrams are drawn. */}
          <MarkdownFileContext.Provider value={markdownFile}>
            <Markdown text={shown} />
          </MarkdownFileContext.Provider>
        </div>
      );
    }
    if (view === "svg") {
      return (
        <div className="fv-svg" data-testid="fv-svg">
          <img
            src={svgDataUrl(shown)}
            alt={`The drawing in ${info.name}`}
            onLoad={(event) => {
              const img = event.currentTarget;
              setSvgSize(img.naturalWidth ? `${img.naturalWidth} × ${img.naturalHeight}` : "");
            }}
          />
          {svgSize && <span className="fv-svg-size">{svgSize} · vector</span>}
        </div>
      );
    }
    if (view === "table") return <CsvTable text={shown} />;
    if (view === "tree") return <JsonTree text={shown} />;
    return (
      <CodeView
        text={shown}
        lang={languageForPath(info.path)}
        path={info.path}
        focusLine={focusLine}
        comments={comments}
        onAddComment={onAddComment}
        onDeleteComment={onDeleteComment}
        onEditComment={onEditComment}
      />
    );
  };

  return (
    <>
      <div className="rev-file-head is-compact fv-head" data-editing={codeEditing || undefined}>
        <button
          type="button"
          className="fm-back"
          data-testid="fm-back"
          onClick={() => requestLeave(onClose)}
          aria-label="Back to the folders"
        >
          <Icon name="arrowLeft" size={13} />
          Files
        </button>
        {/* The open file: its own coloured logo. */}
        <FileIcon path={info.path} size={15} />
        {/* The folder it is in, so the tree can be scrolled away and the name still says where. */}
        <span className="rev-file-name" title={info.path}>
          {parentName(info.path) && <span className="fv-crumb">{parentName(info.path)} / </span>}
          {info.name}
        </span>
        {(text || isMedia) && <span className="rev-file-size">{formatBytes(content?.size ?? info.size)}</span>}
        {editable && rendered && (
          <div className="fv-seg" role="group" aria-label="How to show this file">
            <button
              type="button"
              className={inEditor ? "" : "on"}
              aria-pressed={!inEditor}
              data-testid={`fm-mode-${sideLabel.toLowerCase()}`}
              onClick={() => pickSide(false)}
            >
              {sideLabel}
            </button>
            <button
              type="button"
              className={inEditor ? "on" : ""}
              aria-pressed={inEditor}
              data-testid="fm-mode-edit"
              onClick={() => pickSide(true)}
            >
              Edit
            </button>
          </div>
        )}
        <span className="fv-fill" />
        {/* Said only when there is something to say: unsaved, saving, or just saved. */}
        {editable && (dirty || saving || savedFlash) && (
          <span
            className={"rev-file-state" + (dirty ? " is-dirty" : savedFlash ? " is-saved" : "")}
            data-testid="fm-file-state"
            role="status"
          >
            <span className="long">{saving ? "Saving…" : dirty ? "Unsaved changes" : "Saved"}</span>
            <span className="short">{saving ? "Saving…" : dirty ? "Unsaved" : "Saved"}</span>
          </span>
        )}
        {editable && !rendered && !inEditor && (
          <button type="button" className="btn sm fv-edit" data-testid="fm-edit" onClick={() => setEditing(true)}>
            <Icon name="pencil" size={13} />
            Edit
          </button>
        )}
        {codeEditing && (
          <button
            type="button"
            className="btn sm"
            data-testid="fm-cancel"
            onClick={() => requestLeave(() => setEditing(false))}
          >
            Cancel
          </button>
        )}
        {editable && (dirty || saving || codeEditing) && (
          <button
            type="button"
            className="fm-save"
            data-testid="fm-save"
            title="Save (Ctrl+S)"
            disabled={saving}
            onClick={async () => {
              // Nothing typed: Save in a code file's editor is just the way out.
              if (!dirty) setEditing(false);
              else if ((await save()) && !rendered) setEditing(false);
            }}
          >
            Save
          </button>
        )}
        <button
          ref={moreRef}
          type="button"
          className="artifact-icon-btn fv-more"
          data-testid="artifact-more"
          aria-label={`More actions for ${info.name}`}
          aria-haspopup="menu"
          aria-expanded={menuOpen}
          onClick={() => setMenuOpen((open) => !open)}
        >
          <Icon name="moreHorizontal" size={16} />
        </button>
        <button
          type="button"
          className="artifact-icon-btn fv-close"
          data-testid="artifact-close"
          onClick={() => requestLeave(onClose)}
          aria-label="Close the file"
        >
          <Icon name="x" size={16} />
        </button>
      </div>
      {note && <div className="fv-note-row">{note}</div>}

      {/* Portaled: the head clips its own overflow so a long name can never push the buttons
          out, and a menu placed inside it was clipped with the name (owner-hit, 2026-10-11). */}
      <AnchoredPopover
        anchorRef={moreRef}
        open={menuOpen}
        onClose={() => closeMenu()}
        width={220}
        label={`Actions for ${info.name}`}
        testId="artifact-menu"
        className="fm-menu fv-menu"
      >
        <div ref={menuRef} role="menu">
          {content && !content.error && item("artifact-reload", "refresh", "Reload from disk", () => requestLeave(() => void reload()))}
          {text && item("artifact-copy-contents", "copy", "Copy contents", () => void navigator.clipboard?.writeText(shown))}
          {item("artifact-copy-path", "file", "Copy path", () => void navigator.clipboard?.writeText(info.abs_path || info.path))}
          {onAction && !readOnly && (
            <>
              <div className="tree-menu-sep" />
              {item("fv-rename", "pencil", "Rename…", () => requestLeave(() => onAction("rename")))}
              {item("fv-move", "folder", "Move to…", () => requestLeave(() => onAction("move")))}
              {item("fv-delete", "trash", "Delete…", () => onAction("delete"), true)}
            </>
          )}
          <div className="tree-menu-sep" />
          {isHtml && item("artifact-open-browser", "externalLink", "Open in browser", () => revealArtifact(sessionId, info.path, "open"))}
          {isApp && item("artifact-open-app", "externalLink", "Open in default app", () => revealArtifact(sessionId, info.path, "open"))}
          {item("artifact-reveal", "folder", revealLabel(), () => revealArtifact(sessionId, info.path, "reveal"))}
        </div>
      </AnchoredPopover>

      {conflict ? (
        <div className="fm-conflict" role="alert" data-testid="fm-conflict">
          <span className="fm-conflict-text">This file changed on disk since you opened it.</span>
          <button type="button" className="btn sm" data-testid="fm-conflict-reload" onClick={() => void reload()}>
            Reload from disk
          </button>
          <button type="button" className="btn sm primary" data-testid="fm-conflict-overwrite" onClick={() => void save(true)}>
            Overwrite
          </button>
        </div>
      ) : saveError ? (
        <div className="fm-save-error" role="alert" data-testid="fm-save-error">
          {saveError}
        </div>
      ) : null}
      {text && (named || !isUtf8(content?.encoding)) && (
        <div className="fv-note fv-encoding" data-testid="fv-encoding">
          <span>Read as {encodingLabel(content?.encoding || "")}. Saving keeps it.</span>
          <button
            ref={encodingRef}
            type="button"
            className="fv-note-btn"
            data-testid="fv-encoding-change"
            aria-haspopup="menu"
            aria-expanded={encodingOpen}
            onClick={() => setEncodingOpen((open) => !open)}
          >
            Change…
          </button>
          <AnchoredPopover
            anchorRef={encodingRef}
            open={encodingOpen}
            onClose={() => {
              setEncodingOpen(false);
              encodingRef.current?.focus();
            }}
            width={264}
            label="What this file is saved as"
            testId="fv-encoding-menu"
            className="fm-menu fv-menu"
          >
            <div role="menu">
              {(content?.encodings || []).map((name) => (
                <button
                  key={name}
                  type="button"
                  role="menuitemradio"
                  aria-checked={name === content?.encoding}
                  data-testid={`fv-encoding-${name}`}
                  onClick={() => {
                    setEncodingOpen(false);
                    // Reading the bytes another way throws away what was typed over them.
                    requestLeave(() => {
                      setNamed(true);
                      void reload(name);
                    });
                  }}
                >
                  <span>{encodingLabel(name)}</span>
                  {name === content?.encoding && <Icon name="check" size={14} className="fm-menu-check" />}
                </button>
              ))}
            </div>
          </AnchoredPopover>
        </div>
      )}
      {content?.truncated && !content.error && (
        <div className="fv-note" data-testid="fv-truncated">
          Only the start of this file is shown here, and it cannot be edited here. {revealLabel()} to open all of it.
        </div>
      )}

      <div
        key={inEditor ? "edit" : "read"}
        className={"rev-file-body fv-body" + (inEditor ? " is-editing" : "")}
        data-testid="artifact-body"
        aria-busy={!content || undefined}
      >
        {body()}
      </div>

      {leaving && (
        <div className="fm-dialog-backdrop" onClick={() => setLeaving(null)}>
          <div
            className="fm-dialog"
            role="alertdialog"
            aria-modal="true"
            aria-labelledby="fv-leave-title"
            aria-describedby="fv-leave-hint"
            data-testid="fm-leave-dialog"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="fm-dialog-title" id="fv-leave-title">
              Save your changes to {info.name}?
            </div>
            <div className="fm-dialog-hint" id="fv-leave-hint">
              You edited this file and have not saved it.
            </div>
            <div className="fm-dialog-actions">
              <button type="button" className="btn sm" autoFocus data-testid="fm-leave-keep" onClick={() => setLeaving(null)}>
                Keep editing
              </button>
              <button
                type="button"
                className="btn sm danger-btn"
                data-testid="fm-leave-discard"
                onClick={() => {
                  const next = leaving;
                  setLeaving(null);
                  dropDraft();
                  next();
                }}
              >
                Discard
              </button>
              <button
                type="button"
                className="btn sm primary"
                data-testid="fm-leave-save"
                disabled={saving}
                onClick={async () => {
                  const next = leaving;
                  if (!(await save())) return setLeaving(null);
                  setLeaving(null);
                  next();
                }}
              >
                Save
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
});

/**
 * The editor: a plain text box. A line-shaped file keeps the look it has when read, numbers on
 * the left and no wrapping, so going into the editor does not move the text (measured on the
 * dev app, 2026-10-11: the bare box wrapped long lines, dropped the numbers and joined `>=`
 * into one glyph).
 */
function FileEditor({
  name,
  value,
  lined,
  autoFocus,
  onChange,
  onSave,
}: {
  name: string;
  value: string;
  lined: boolean;
  autoFocus: boolean;
  onChange: (value: string) => void;
  onSave: () => void;
}) {
  const numbers = useRef<HTMLDivElement | null>(null);
  const count = useMemo(() => value.split("\n").length, [value]);
  const column = useMemo(() => Array.from({ length: count }, (_, i) => i + 1).join("\n"), [count]);
  const box = (
    <textarea
      className={"fm-editor" + (lined ? " is-lined" : "")}
      data-testid="fm-editor"
      aria-label={`Contents of ${name}`}
      value={value}
      wrap={lined ? "off" : undefined}
      spellCheck={false}
      autoFocus={autoFocus}
      onChange={(event) => onChange(event.target.value)}
      onScroll={(event) => {
        if (numbers.current) numbers.current.scrollTop = event.currentTarget.scrollTop;
      }}
      onKeyDown={(event) => {
        if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "s") {
          event.preventDefault();
          onSave();
        }
      }}
    />
  );
  if (!lined) return box;
  return (
    <div className="fv-editor">
      <div className="fv-editor-nos" ref={numbers} aria-hidden="true">
        {column}
      </div>
      {box}
    </div>
  );
}

/** The shape of a page while its file is read: lines, not a spinner and not a shimmer. */
function FileSkeleton() {
  return (
    <div className="fv-skeleton" data-testid="fv-skeleton" role="status" aria-label="Opening the file">
      <span style={{ width: "42%", height: 12 }} />
      <span style={{ width: "92%" }} />
      <span style={{ width: "86%" }} />
      <span style={{ width: "90%" }} />
      <span style={{ width: "58%" }} />
      <span style={{ width: "88%", marginTop: 10 }} />
      <span style={{ width: "72%" }} />
    </div>
  );
}
