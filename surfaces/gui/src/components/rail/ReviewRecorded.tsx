import { useEffect, useMemo, useRef, useState } from "react";
import { revealPath, undoToolCall } from "../../api";
import {
  diffRowsForChange,
  recordedTurnsFromChanges,
  summarizeRecordedChanges,
  type RecordedChange,
  type RecordedFile,
} from "../../reviewChanges";
import { FileIcon } from "../FileIcon";
import { ReviewDiff } from "./ReviewDiff";
import { Icon } from "../Icon";

const STATUS_MARK: Record<RecordedChange["status"], string> = {
  added: "A",
  modified: "M",
  deleted: "D",
};

function RecordedFileCard({
  sessionId,
  file,
  undone,
  busy,
  onUndo,
  focused,
  defaultOpen,
}: {
  sessionId: string;
  file: RecordedFile & { blockedBy?: number };
  undone: boolean;
  busy: boolean;
  onUndo: () => void;
  /** Files asked Review to show this file ("See what changed"): open it and bring it in. */
  focused?: boolean;
  /** A short list opens its diffs without a click, as Uncommitted does. */
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(!!focused || !!defaultOpen);
  const cardRef = useRef<HTMLElement | null>(null);
  useEffect(() => {
    if (!focused) return;
    setOpen(true);
    cardRef.current?.scrollIntoView?.({ block: "nearest" });
  }, [focused]);
  const [menuOpen, setMenuOpen] = useState(false);
  // A click anywhere else (or Escape) puts the menu away; it is a two-item list, not a mode.
  useEffect(() => {
    if (!menuOpen) return;
    const close = () => setMenuOpen(false);
    window.addEventListener("mousedown", close);
    window.addEventListener("keydown", close);
    return () => {
      window.removeEventListener("mousedown", close);
      window.removeEventListener("keydown", close);
    };
  }, [menuOpen]);
  // Each change keeps its own hunks: a file edited twice shows both edits, in order.
  const parts = useMemo(
    () => (open ? file.changes.map((c) => ({ change: c, rows: diffRowsForChange(c) })) : []),
    [open, file],
  );
  const dir = file.displayPath.split("/").slice(0, -1).join("/");
  const name = file.displayPath.split("/").pop() || file.displayPath;
  return (
    <section
      ref={cardRef}
      className={"rev-change-card" + (open ? " open" : "") + (undone ? " is-undone" : "") + (focused ? " is-focused" : "")}
      data-state={undone ? "undone" : "active"}
      data-status={file.status}
      data-testid={`recorded-change-${file.path}`}
    >
      <div className="rev-change-row">
        <button
          type="button"
          className="rev-change-head"
          aria-expanded={open}
          // The tools that made the change are a developer's detail: the tooltip keeps them.
          title={`${file.path} \u00b7 ${file.tools}`}
          onClick={() => setOpen((value) => !value)}
          onContextMenu={(event) => {
            // The change-header menu: the file is the thing, so the menu is about the file,
            // not about the diff. "Open file" replaces their reveal-in-tree (our viewer is it).
            event.preventDefault();
            setMenuOpen(true);
          }}
        >
          {/* One chevron that rotates, not an icon swap. */}
          <span className={"rev-change-caret" + (open ? " open" : "")} aria-hidden>
            <Icon name="chevronRight" size={14} />
          </span>
          <span className="rev-change-mark" data-type={file.status} aria-hidden>
            {STATUS_MARK[file.status]}
          </span>
          <FileIcon path={file.path} size={14} />
          <span className="rev-change-name">{name}</span>
          {dir && <span className="rev-change-dir">{dir}</span>}
          <span className="rev-change-counts">
            {file.added > 0 && <span className="add">+{file.added}</span>}
            {file.removed > 0 && <span className="del">−{file.removed}</span>}
          </span>
        </button>
        <span className="rev-change-actions">
          {undone ? (
            <span className="rev-change-state">
              <Icon name="check" size={13} /> Undone
            </span>
          ) : file.undoable && file.blockedBy ? (
            <span
              className="rev-change-state blocked"
              data-testid={`recorded-blocked-${file.path}`}
              title={`Changed again in Turn ${file.blockedBy}: undo that first`}
            >
              Undo Turn {file.blockedBy} first
            </span>
          ) : file.undoable ? (
            <button
              type="button"
              className="rev-change-undo"
              data-testid={`recorded-undo-${file.path}`}
              disabled={busy}
              title={file.changes.length > 1 ? "Undo these changes" : "Undo this change"}
              onClick={onUndo}
            >
              <Icon name="undo" size={13} />
              {busy ? "Undoing…" : "Undo"}
            </button>
          ) : null}
        </span>
      </div>
      {open && (
        <div className="rev-change-body" data-testid={`recorded-diff-${file.path}`}>
          {parts.map(({ change: part, rows }, index) => (
            <div key={part.id} className="rev-change-part">
              {parts.length > 1 && (
                <div className="rev-change-part-head">
                  Edit {index + 1} of {parts.length}
                </div>
              )}
              <ReviewDiff
                path={file.path}
                rows={rows.map((row) => ({
                  // A unified view: a removed line keeps its OLD number, everything else the
                  // new one — the same gutter rule the transcript's diff cards use.
                  kind: row.kind,
                  no: row.kind === "del" ? row.old : row.new,
                  text: row.text,
                }))}
              />
            </div>
          ))}
        </div>
      )}
      {menuOpen && (
        <div className="rev-change-menu" role="menu" data-testid={`recorded-menu-${file.path}`}>
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              void navigator.clipboard?.writeText(file.path);
              setMenuOpen(false);
            }}
          >
            Copy path
          </button>
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              window.dispatchEvent(
                new CustomEvent("reeve-open-artifact", { detail: { path: file.path } }),
              );
              setMenuOpen(false);
            }}
          >
            Open file
          </button>
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              // The "Reveal in Explorer": the server holds the workspace root and the
              // boundary check, and the surface only asks for it by path.
              void revealPath(sessionId, file.path);
              setMenuOpen(false);
            }}
          >
            Reveal in file manager
          </button>
        </div>
      )}
    </section>
  );
}

export function ReviewRecorded({
  sessionId,
  changes,
  roots = [],
  onChanged,
  focusPath,
  onShowUncommitted,
}: {
  sessionId: string;
  changes: RecordedChange[];
  /** The session's folders, so a path reads relative to the one that holds it (V3). */
  roots?: string[];
  onChanged?: () => void;
  /** A file Files asked to show; its newest turn's card opens. */
  focusPath?: string | null;
  /** Switch Review to the Uncommitted scope (the note under the list links there). */
  onShowUncommitted?: () => void;
}) {
  const summary = useMemo(() => summarizeRecordedChanges(changes), [changes]);
  const turns = useMemo(() => recordedTurnsFromChanges(changes, roots), [changes, roots]);
  // Keyed by turn and file: one file can have a card in several turns.
  const [undone, setUndone] = useState<Record<string, boolean>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const key = (turn: number, path: string) => `${turn}:${path}`;

  // Newest first: each undo restores its call's pre-call content, so the later edit must give
  // way before the earlier one, or the earlier restore would be overwritten.
  const undoChanges = async (list: RecordedChange[]): Promise<boolean> => {
    for (const change of [...list].reverse()) {
      const result = await undoToolCall(sessionId, change.callId as string).catch(() => null);
      if (!result || result.ok === false) return false;
    }
    return true;
  };
  const undoFile = async (turn: number, file: RecordedFile & { blockedBy?: number }) => {
    if (!file.undoable || file.blockedBy || busy) return;
    setBusy(key(turn, file.path));
    const ok = await undoChanges(file.changes);
    setBusy(null);
    if (!ok) return;
    setUndone((state) => ({ ...state, [key(turn, file.path)]: true }));
    onChanged?.();
  };
  const undoTurn = async (turn: number, files: (RecordedFile & { blockedBy?: number })[]) => {
    if (busy) return;
    setBusy(`turn:${turn}`);
    const open = files.filter((file) => !undone[key(turn, file.path)]);
    const all = changes.filter(
      (change) => (change.turn ?? 0) === turn && open.some((file) => file.path === change.path),
    );
    const ok = await undoChanges(all);
    setBusy(null);
    if (!ok) return;
    setUndone((state) => ({
      ...state,
      ...Object.fromEntries(open.map((file) => [key(turn, file.path), true])),
    }));
    onChanged?.();
  };

  if (changes.length === 0) {
    return (
      <div className="rev-empty" data-testid="recorded-empty">
        <Icon name="file" size={22} />
        <div className="rev-empty-title">Reeve hasn&apos;t changed any files in this session</div>
        <div className="rev-empty-desc">
          Every file Reeve writes or edits appears here, grouped by your message, with a one-click undo.
        </div>
      </div>
    );
  }

  const matches = (file: RecordedFile) => file.path === focusPath || file.displayPath === focusPath;
  // A few files open their diffs on their own; a long list stays a list to scan.
  const openAll = summary.fileCount <= 4;
  const focusTurn = focusPath ? turns.find((t) => t.files.some(matches))?.turn : undefined;
  return (
    <div className="rev-recorded" data-testid="recorded-changes">
      <div className="rev-recorded-summary">
        Reeve changed {summary.fileCount} file{summary.fileCount === 1 ? "" : "s"} in {turns.length} turn
        {turns.length === 1 ? "" : "s"}
        <span className="rev-tot">
          {summary.additions > 0 && <span className="add">+{summary.additions}</span>}
          {summary.deletions > 0 && <span className="del">−{summary.deletions}</span>}
        </span>
      </div>
      {/* Said once for the list (V4): the same sentence on every card was noise. */}
      {turns.some((t) => t.files.some((file) => !file.undoable)) && (
        <div className="rev-recorded-note" data-testid="recorded-undo-note">
          {turns.every((t) => t.files.every((file) => !file.undoable))
            ? "Undo isn't available for these changes — they were made without a saved copy."
            : "Undo isn't available for some of these changes — they were made without a saved copy."}
        </div>
      )}
      {turns.map((t) => {
        const pending = t.files.filter((file) => !undone[key(t.turn, file.path)]);
        const canUndoTurn =
          pending.length > 0 && pending.every((file) => file.undoable && !file.blockedBy);
        return (
          <section className="rev-turn" key={t.turn} data-testid={`recorded-turn-${t.turn}`}>
            <div className="rev-turn-head">
              <span className="rev-turn-label">{t.turn > 0 ? `Turn ${t.turn}` : "Earlier"}</span>
              {t.text && (
                <span className="rev-turn-text" title={t.text}>
                  “{t.text.split("\n")[0]}”
                </span>
              )}
              {canUndoTurn && t.files.length > 1 && (
                <button
                  type="button"
                  className="rev-turn-undo"
                  data-testid={`recorded-undo-turn-${t.turn}`}
                  disabled={!!busy}
                  onClick={() => void undoTurn(t.turn, t.files)}
                >
                  {busy === `turn:${t.turn}` ? "Undoing…" : "Undo turn"}
                </button>
              )}
            </div>
            <div className="rev-recorded-list">
              {t.files.map((file) => (
                <RecordedFileCard
                  key={file.path}
                  sessionId={sessionId}
                  file={file}
                  undone={!!undone[key(t.turn, file.path)]}
                  busy={busy === key(t.turn, file.path)}
                  onUndo={() => void undoFile(t.turn, file)}
                  focused={focusTurn === t.turn && matches(file)}
                  defaultOpen={openAll}
                />
              ))}
            </div>
          </section>
        );
      })}
      {onShowUncommitted && (
        <div className="rev-recorded-foot" data-testid="recorded-foot">
          Changed by a command or another program?{" "}
          <button type="button" className="rev-link" onClick={onShowUncommitted}>
            See Uncommitted
          </button>
        </div>
      )}
    </div>
  );
}
