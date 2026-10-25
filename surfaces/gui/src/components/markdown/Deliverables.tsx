// The files a turn handed over, stacked UNDER its closing answer.
//
// Why a tail and not a chip in the sentence: a paragraph that names three files has to stay
// a paragraph, so the sentence keeps only a light mention (see `DeliveredMention` in
// Markdown.tsx) and the thing a reader actually wants next — one row per file, with the
// model's own words about it and the actions a person takes on a file — sits below the
// prose where it cannot wreck the reading rhythm.
//
// The row is shaped by what the reference design learned: the WHOLE row is the preview button (one gesture,
// no nested buttons), the icon tile carries the file's colour, the second line is the
// model's description and turns into "Preview in sidebar" on hover — because a description
// you have already read is dead weight next to an action you have not taken — and the
// native actions live behind the chevron so the row does not become a toolbar.
//
// Data comes from the `present` tool's result card (`cards/types.ts`, `FilesResultCard`),
// never from prose scanning: a card exists only for a file the model explicitly declared.

import { useRef, useState } from "react";
import type { Presented } from "../../cards/types";
import { FileIcon } from "../FileIcon";
import { Icon } from "../Icon";
import { AnchoredPopover } from "../primitives/AnchoredPopover";
import { CopyButton } from "../primitives/CopyButton";
import { fileTypeLabel, openArtifact, revealArtifact } from "../Markdown";

/** One delivered file, as the tail renders it. */
export interface DeliveredFile {
  path: string;
  /** The model's own line about the file; absent when it declared one without a reason. */
  description?: string;
}

/** How many rows are shown before the tail folds the rest behind one control (the reference design's
 *  `COLLAPSED_PRESENTED_COUNT`). Four fills the two-column grid twice. */
const COLLAPSED_COUNT = 4;

const leafOf = (path: string) => path.split(/[\\/]/).pop() || path;

/** The shape `declaredFiles` reads: a transcript item that may carry a tool result's
 *  presentation. `kind` is not used — it is declared because a type whose properties are all
 *  optional is a "weak type", and TypeScript refuses to match it against anything that
 *  shares no property with it, which every real item would. */
interface PresentationCarrier {
  kind?: string;
  presentation?: Presented;
}

/**
 * Every file the turn declared with `present`, in declaration order.
 *
 * A path declared twice is ONE row: the later declaration's description wins (the model is
 * allowed to correct itself), but the row keeps the place it first took, so the list does
 * not reshuffle under the reader. Malformed entries are skipped rather than rendered — a
 * card for nothing is worse than no card.
 */
export function declaredFiles(items: readonly PresentationCarrier[] | undefined): DeliveredFile[] {
  const out = new Map<string, DeliveredFile>();
  for (const item of items ?? []) {
    const card = item?.presentation?.card;
    if (card?.card !== "files") continue;
    for (const file of card.files ?? []) {
      const path = typeof file?.path === "string" ? file.path.trim() : "";
      if (!path) continue;
      const description = typeof file.description === "string" && file.description.trim() ? file.description.trim() : undefined;
      out.set(path, { path, description: description ?? out.get(path)?.description });
    }
  }
  return [...out.values()];
}

/** The three things a person does with a file the model just handed over. `reveal` and
 *  `open` are one thing to the model and two to the reader: showing the file in its folder
 *  and launching it in its own app are different intentions, and guessing wrong is a
 *  window stealing focus. Both go through the session that owns the file (SessionScroll
 *  is the only component that knows the session id). */
function FileActionsMenu({ path }: { path: string }) {
  const [open, setOpen] = useState(false);
  const anchor = useRef<HTMLButtonElement>(null);

  const run = (mode: "open" | "reveal") => {
    setOpen(false);
    revealArtifact(path, mode);
  };

  return (
    <span className="deliverable-menu">
      <button
        ref={anchor}
        type="button"
        className="deliverable-icon"
        aria-expanded={open}
        aria-label="More ways to open"
        title="More ways to open"
        data-testid="delivered-file-menu"
        onClick={() => setOpen((value) => !value)}
      >
        <Icon name="chevronDown" size={13} />
      </button>
      {/* Portaled (AnchoredPopover): drawn inside the turn, the panel was cut at the turn's
          bottom edge, whose `content-visibility: auto` clips what it holds, and the receipt
          row under the turn covered "Copy path" (owner-hit, 2026-10-13; on the dev app the
          last row's centre hit `.rc-line`). A group, not a `menu`: the rows are ordinary
          buttons, one of them the shared copy control with its own label and copied state,
          and menu semantics would promise arrow keys nothing here implements. */}
      <AnchoredPopover
        anchorRef={anchor}
        open={open}
        onClose={() => setOpen(false)}
        width={210}
        label="File actions"
        testId="delivered-file-actions"
        className="deliverable-popover"
      >
        <button type="button" className="deliverable-item" onClick={() => run("open")}>
          <Icon name="externalLink" size={13} />
          <span>Open in default app</span>
        </button>
        <button type="button" className="deliverable-item" onClick={() => run("reveal")}>
          <Icon name="folder" size={13} />
          <span>Show in file manager</span>
        </button>
        <span className="deliverable-item deliverable-item-static">
          <CopyButton text={path} label="Copy path" copiedLabel="Path copied" />
        </span>
      </AnchoredPopover>
    </span>
  );
}

/** One delivered file, shaped like the delivery row. The row is ONE button for the
 *  preview — the icon and the text sit above it with `pointer-events: none`, which is what
 *  keeps the action buttons out of a nested-button tree. */
function DeliveredFileCard({ file }: { file: DeliveredFile }) {
  const { path } = file;
  const name = leafOf(path);
  // What the second line says when the model explained nothing: the kind of file, which is
  // still more than the bare name repeats. `fileTypeLabel` knows the extensions the icons
  // know, so the two can never disagree about what a `.docx` is.
  const fallback = fileTypeLabel(name) || "File";
  const description = file.description || fallback;
  return (
    <div className="deliverable" data-testid="delivered-file-card">
      <button
        type="button"
        className="deliverable-open"
        title={`Open ${path}`}
        aria-label={`Preview ${path}`}
        data-testid="delivered-file-open-main"
        onClick={() => openArtifact(path)}
      />
      <span className="deliverable-tile" aria-hidden>
        <FileIcon path={path} size={26} />
      </span>
      <span className="deliverable-body">
        <span className="deliverable-details">
          <span className="deliverable-name">{name}</span>
          <span className="deliverable-sub" title={path}>
            <span className="deliverable-desc">{description}</span>
            <span className="deliverable-hint">Preview in sidebar</span>
          </span>
        </span>
        <span className="deliverable-actions">
          <button
            type="button"
            className="deliverable-icon"
            aria-label="Show in file manager"
            title="Show in file manager"
            data-testid="delivered-file-reveal"
            onClick={() => revealArtifact(path, "reveal")}
          >
            <Icon name="folder" size={15} />
          </button>
          <FileActionsMenu path={path} />
        </span>
      </span>
    </div>
  );
}

/**
 * The turn's delivered files.
 *
 * One row when there is one, two columns when there are more:
 * a single full-width row reads as "this is the thing", and a pair of rectangles reads as
 * a set. Past four the set folds behind one control rather than pushing the next turn off
 * the screen.
 */
export function DeliveredFiles({ files }: { files: DeliveredFile[] }) {
  const [expanded, setExpanded] = useState(false);
  if (files.length === 0) return null;
  const collapsible = files.length > COLLAPSED_COUNT;
  const shown = collapsible && !expanded ? files.slice(0, COLLAPSED_COUNT) : files;
  return (
    <div className="deliverables" data-testid="delivered-files">
      <div
        className="deliverable-grid"
        data-testid="delivered-files-row"
        data-single={files.length === 1 ? true : undefined}
      >
        {shown.map((file) => (
          <DeliveredFileCard key={file.path} file={file} />
        ))}
      </div>
      {collapsible && (
        <button
          type="button"
          className="deliverable-toggle"
          data-testid="delivered-files-toggle"
          aria-expanded={expanded}
          aria-label={expanded ? "Collapse delivered files" : `Show all ${files.length} delivered files`}
          onClick={() => setExpanded((value) => !value)}
        >
          <span>{expanded ? "Collapse" : `All ${files.length} files`}</span>
          <Icon name={expanded ? "chevronUp" : "chevronDown"} size={13} />
        </button>
      )}
    </div>
  );
}
