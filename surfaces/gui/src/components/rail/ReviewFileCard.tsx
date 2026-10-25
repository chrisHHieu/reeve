// One changed file in Review: a header (status, path, who changed it, Revert / Stage / Viewed)
// over its diff. The diff is fetched when the card is open, so a long list costs what is read.

import { useEffect, useState } from "react";
import { getChangeDiff, type GitScope, type ReviewFinding } from "../../api";
import type { ReviewComment } from "../../types";
import { FileIcon } from "../FileIcon";
import { Icon } from "../Icon";
import { statusType } from "./changeStatus";
import { DiffView, type DiffStyle, type ExpandMode } from "./DiffView";
import { formatSize, type ReviewFile } from "./reviewModel";

type Loaded = {
  patch?: string;
  content?: string;
  untracked?: boolean;
  /** The server sent facts, not bytes (a binary file). */
  binary?: boolean;
  size?: number;
  /** `content` is the start of a long file. */
  truncated?: boolean;
};

/** Which of a hunk's buttons apply: the working tree's diff can stage or revert a hunk, the
 *  index's can unstage one, and history offers none. */
export type HunkMode = "none" | "unstaged" | "staged";

export type Finding = ReviewFinding & { id: string };

const SEVERITY_LABEL: Record<ReviewFinding["severity"], string> = {
  bug: "Likely bug",
  risk: "Worth a look",
  nit: "Minor",
};

function FindingNote({
  finding,
  onFix,
  onDismiss,
}: {
  finding: Finding;
  onFix: () => void;
  onDismiss: () => void;
}) {
  return (
    <div className="rvw-note" data-severity={finding.severity} data-testid={`rvw-finding-${finding.id}`}>
      <div className="rvw-note-head">
        <span className="rvw-note-avatar" aria-hidden>
          S
        </span>
        <b>Reeve's review</b>
        <span className="rvw-note-severity">{SEVERITY_LABEL[finding.severity]}</span>
      </div>
      <div className="rvw-note-body">{finding.body}</div>
      <div className="rvw-note-actions">
        <button type="button" className="rvw-note-fix" data-testid={`rvw-finding-fix-${finding.id}`} onClick={onFix}>
          Fix this
        </button>
        <button type="button" className="rvw-btn ghost-static" data-testid={`rvw-finding-dismiss-${finding.id}`} onClick={onDismiss}>
          Dismiss
        </button>
      </div>
    </div>
  );
}

export function ReviewFileCard({
  sessionId,
  scope,
  version,
  file,
  turn,
  open,
  onToggle,
  viewed,
  onViewed,
  busy,
  showStage,
  showUnstage,
  showRevert,
  onStage,
  onUnstage,
  onRevert,
  hunkMode,
  onHunk,
  onOpenFile,
  diffStyle,
  expandMode,
  comments,
  onAddComment,
  onDeleteComment,
  onEditComment,
  cardRef,
  findings = [],
  onFixFinding,
  onDismissFinding,
  bare = false,
  current = false,
  noteCount = 0,
}: {
  sessionId: string;
  /** What this card's diff is a diff of. */
  scope: GitScope;
  /** Bumps when the list is read again, so an open card reads its diff again. */
  version: number;
  file: ReviewFile;
  /** The newest turn in which Reeve changed this path, if it did. */
  turn?: number;
  open: boolean;
  onToggle: () => void;
  viewed: boolean;
  onViewed: (viewed: boolean) => void;
  busy: boolean;
  showStage: boolean;
  showUnstage: boolean;
  showRevert: boolean;
  onStage: () => void;
  onUnstage: () => void;
  onRevert: () => void;
  hunkMode: HunkMode;
  onHunk: (action: "stage" | "unstage" | "revert", index: number, header: string) => void;
  onOpenFile: () => void;
  diffStyle: DiffStyle;
  expandMode: ExpandMode;
  comments: ReviewComment[];
  onAddComment?: (c: ReviewComment) => void;
  onDeleteComment?: (id: string) => void;
  onEditComment?: (id: string, text: string) => void;
  cardRef?: (el: HTMLElement | null) => void;
  /** Reeve's review findings on this file, drawn under their lines. */
  findings?: Finding[];
  onFixFinding?: (finding: Finding) => void;
  onDismissFinding?: (finding: Finding) => void;
  /** The one file on show (Review's list picks it): no fold toggle, and Viewed is the list's
   *  and the footer's, so the header does not say it twice. */
  bare?: boolean;
  /** The card J / K / V act on. */
  current?: boolean;
  /** The reader's comments and Reeve's findings on this file: said on the header, so a closed
   *  card still shows it has something to read. */
  noteCount?: number;
}) {
  const [diff, setDiff] = useState<Loaded | "error" | null>(null);
  // A closed card reads nothing, and neither does a binary one (its facts are in the list). An open
  // one reads again when ITS FILE moved, and keeps showing the old diff meanwhile rather than
  // flashing "Loading". `version` bumps whenever the list was read, changed or not: with it as the
  // trigger every open card read its diff again each time (65 requests for 4 files in 20 minutes,
  // 585-813 KB each). The working tree's cards follow the file's `revision`; a scope that
  // moves with commits, or a server that sent no revision, still follows the list.
  const stamp = scope === "uncommitted" && file.revision ? file.revision : version;
  useEffect(() => {
    if (!open || file.binary) return;
    let alive = true;
    getChangeDiff(sessionId, file.path, scope)
      .then((d) =>
        alive &&
        setDiff(
          d.ok
            ? { patch: d.patch, content: d.content, untracked: d.untracked, binary: d.binary, size: d.size, truncated: d.truncated }
            : "error",
        ),
      )
      .catch(() => alive && setDiff("error"));
    return () => {
      alive = false;
    };
  }, [open, sessionId, scope, file.path, file.signature, file.binary, stamp]);

  const name = file.path.split("/").pop() || file.path;
  const dir = file.path.split("/").slice(0, -1).join("/");
  const type = statusType(file.status);
  const letter = file.status === "A" && file.staged === "none" ? "U" : file.status;
  const empty = diff && diff !== "error" && !diff.untracked && !(diff.patch || "").trim();

  return (
    <section
      ref={cardRef}
      className={"rvw-card" + (open ? " open" : "") + (viewed ? " viewed" : "") + (bare ? " bare" : "") + (current ? " is-current" : "")}
      data-testid={`rvw-card-${file.path}`}
    >
      <header className="rvw-card-head">
        {!bare && (
        <button
          type="button"
          className="rvw-card-toggle"
          aria-expanded={open}
          aria-label={`${open ? "Collapse" : "Expand"} ${file.path}`}
          data-testid={`rvw-toggle-${file.path}`}
          onClick={onToggle}
        >
          <Icon name={open ? "chevronDown" : "chevronRight"} size={13} />
        </button>
        )}
        <span className="rev-status" data-type={type}>
          {letter}
        </span>
        <FileIcon path={file.path} size={15} />
        <button
          type="button"
          className="rvw-card-name"
          title="Open in Files"
          data-testid={`rvw-open-${file.path}`}
          onClick={onOpenFile}
        >
          {name}
        </button>
        {dir && <span className="rvw-card-dir">{dir}</span>}
        <span className="rvw-card-counts">
          {file.add > 0 && <span className="add">+{file.add}</span>}
          {file.del > 0 && <span className="del">−{file.del}</span>}
        </span>
        {noteCount > 0 && (
          <span className="rvw-chip notes" data-testid={`rvw-notes-${file.path}`}>
            {noteCount} note{noteCount === 1 ? "" : "s"}
          </span>
        )}
        {turn !== undefined && (
          <span className="rev-origin is-agent" data-testid={`rvw-turn-${file.path}`}>
            Reeve · Turn {turn}
          </span>
        )}
        {file.staged !== "none" && (
          <span className="rvw-chip staged">{file.staged === "all" ? "Staged" : "Partly staged"}</span>
        )}
        <span className="rvw-card-fill" />
        {file.generated && (
          <span className="rvw-generated" data-testid={`rvw-generated-${file.path}`}>
            Generated{open ? "" : " \u2014 collapsed"}
          </span>
        )}
        <span className="rvw-card-actions">
          {/* Shown on hover or focus, laid over the header's right end: appearing, they move
              nothing (in one column a header that wrapped under the pointer moved the toggle
              away from the click aimed at it). */}
          <span className="rvw-card-hover">
          {showRevert && (
            <button
              type="button"
              className="rvw-btn ghost"
              disabled={busy}
              data-testid={`rvw-revert-${file.path}`}
              onClick={onRevert}
            >
              <Icon name="undo" size={13} />
              Revert
            </button>
          )}
          {showUnstage && (
            <button
              type="button"
              className="rvw-btn ghost"
              disabled={busy}
              data-testid={`rvw-unstage-${file.path}`}
              onClick={onUnstage}
            >
              Unstage
            </button>
          )}
          {showStage && (
            <button
              type="button"
              className="rvw-btn ghost"
              disabled={busy}
              data-testid={`rvw-stage-${file.path}`}
              onClick={onStage}
            >
              <Icon name="plus" size={13} />
              Stage
            </button>
          )}
          </span>
          {!bare && (
            <label className="rvw-viewed">
              <input
                type="checkbox"
                checked={viewed}
                aria-label={`Viewed: ${name}`}
                data-testid={`rvw-viewed-${file.path}`}
                onChange={(event) => onViewed(event.target.checked)}
              />
              Viewed
            </label>
          )}
        </span>
      </header>
      {open && (
        <div className="rvw-card-body">
          {file.binary || (diff && diff !== "error" && diff.binary) ? (
            <div className="rev-empty-small" data-testid={`rvw-binary-${file.path}`}>
              Binary file
              {(file.size ?? (diff && diff !== "error" ? diff.size : undefined)) !== undefined
                ? ` · ${formatSize((file.size ?? (diff && diff !== "error" ? diff.size : 0)) as number)}`
                : ""}
              {" "}— its bytes are not shown here.
            </div>
          ) : diff === null ? (
            <div className="rev-empty-small">Loading…</div>
          ) : diff === "error" ? (
            <div className="rev-empty-small">This file's changes could not be read.</div>
          ) : empty ? (
            <div className="rev-empty-small">No line changes to show (a binary file, or only its mode changed).</div>
          ) : (
            <>
              <DiffView
                patch={diff.patch}
                content={diff.content}
                untracked={diff.untracked}
                path={file.path}
                style={diffStyle}
                expandMode={expandMode}
                comments={comments}
                onAddComment={onAddComment}
                onDeleteComment={onDeleteComment}
                onEditComment={onEditComment}
                notes={findings}
                renderNote={(id) => {
                  const finding = findings.find((f) => f.id === id);
                  return finding ? (
                    <FindingNote
                      finding={finding}
                      onFix={() => onFixFinding?.(finding)}
                      onDismiss={() => onDismissFinding?.(finding)}
                    />
                  ) : null;
                }}
                renderHunkActions={
                  hunkMode === "none"
                    ? undefined
                    : (index, header) => (
                        <>
                          {hunkMode === "unstaged" && (
                            <>
                              <button
                                type="button"
                                className="dl-hunk-btn danger"
                                disabled={busy}
                                data-testid={`rvw-hunk-revert-${file.path}-${index}`}
                                onClick={() => onHunk("revert", index, header)}
                              >
                                Revert hunk
                              </button>
                              <button
                                type="button"
                                className="dl-hunk-btn primary"
                                disabled={busy}
                                data-testid={`rvw-hunk-stage-${file.path}-${index}`}
                                onClick={() => onHunk("stage", index, header)}
                              >
                                Stage hunk
                              </button>
                            </>
                          )}
                          {hunkMode === "staged" && (
                            <button
                              type="button"
                              className="dl-hunk-btn primary"
                              disabled={busy}
                              data-testid={`rvw-hunk-unstage-${file.path}-${index}`}
                              onClick={() => onHunk("unstage", index, header)}
                            >
                              Unstage hunk
                            </button>
                          )}
                        </>
                      )
                }
              />
              {diff.truncated && (
                <div className="rev-empty-small" data-testid={`rvw-truncated-${file.path}`}>
                  Showing the start of a long file{diff.size !== undefined ? ` (${formatSize(diff.size)} in all)` : ""}.
                </div>
              )}
            </>
          )}
        </div>
      )}
    </section>
  );
}
