// The two ways a fence with no language is drawn when it is not code (fenceKinds.ts): a folder
// tree as a tree, and prose as a draft you can read and copy (owner, 2026-10-13). Both keep the
// code block's card and bar, and Copy gives the fence's text exactly as the model wrote it.

import { Fragment } from "react";
import { FileIcon } from "../FileIcon";
import { Icon } from "../Icon";
import { CopyButton } from "../primitives/CopyButton";
import { readDraft, type TextTree } from "./fenceKinds";

const plural = (n: number, one: string) => `${n} ${one}${n === 1 ? "" : "s"}`;

/** A folder tree: each entry with its own mark, folders open, a guide per level of depth. */
export function MarkdownTree({ tree, source }: { tree: TextTree; source: string }) {
  const folders = tree.entries.filter((entry) => entry.dir).length;
  const files = tree.entries.length - folders;
  // Each entry's path from the top, so a file's mark is read from its own name and folder.
  const paths: string[] = [];
  const trail: string[] = [];
  for (const entry of tree.entries) {
    trail.length = entry.depth;
    paths.push([...trail, entry.name].join("/"));
    trail[entry.depth] = entry.name;
  }
  return (
    <div className="md-codeblock md-tree" data-testid="md-tree">
      <div className="md-codeblock-head">
        <Icon name="folderOpen" size={14} className="md-codeblock-mark" />
        {tree.root && (
          <span className="md-codeblock-file" data-testid="md-tree-root">
            {tree.root}
          </span>
        )}
        <span className="md-codeblock-lang" data-testid="md-tree-count">
          {[folders && plural(folders, "folder"), files && plural(files, "file")].filter(Boolean).join(", ")}
        </span>
        <span className="md-codeblock-spacer" />
        <CopyButton text={source} iconOnly className="md-codeblock-copy" />
      </div>
      <div className="md-tree-body" role="tree" aria-label={tree.root ? `Files in ${tree.root}` : "Files"}>
        {tree.entries.map((entry, index) => (
          <div
            key={index}
            className="md-tree-row"
            role="treeitem"
            aria-level={entry.depth + 1}
            data-dir={entry.dir || undefined}
          >
            {Array.from({ length: entry.depth }, (_, level) => (
              <span key={level} className="md-tree-guide" aria-hidden />
            ))}
            {entry.dir ? (
              <Icon name="folderOpen" size={14} className="md-tree-folder" />
            ) : (
              <FileIcon path={paths[index]} size={14} />
            )}
            <span className="md-tree-name">{entry.name}</span>
            {entry.note && <span className="md-tree-note">{entry.note}</span>}
          </div>
        ))}
      </div>
    </div>
  );
}

/** Prose in a fence (a draft email, a message): the sentence font, wrapped, a Copy you can see. */
export function MarkdownDraft({ source }: { source: string }) {
  const draft = readDraft(source);
  return (
    <div className="md-draft" data-testid="md-draft">
      <div className="md-draft-head">
        <Icon name="pencil" size={13} className="md-codeblock-mark" />
        <span>Draft</span>
        <span className="md-codeblock-spacer" />
        <CopyButton text={source.replace(/\s+$/, "")} className="md-draft-copy" />
      </div>
      {draft.subject && (
        <div className="md-draft-subject" data-testid="md-draft-subject">
          {draft.subjectLabel}: <b>{draft.subject}</b>
        </div>
      )}
      <div className="md-draft-body">
        {draft.paragraphs.map((lines, index) => (
          <p key={index}>
            {lines.map((line, at) => (
              <Fragment key={at}>
                {at > 0 && <br />}
                {line}
              </Fragment>
            ))}
          </p>
        ))}
      </div>
    </div>
  );
}
