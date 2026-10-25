import { useState } from "react";
import type { ReviewComment } from "../../types";
import { FileIcon } from "../FileIcon";

/** A line comment waiting to go with the next message: the file's mark and name:line, the comment
 *  under it, and ✕. A press edits it in place; App holds it, so Review's card shows the edit too. */
export function CommentChip({ comment, onRemove, onEdit }: { comment: ReviewComment; onRemove?: (id: string) => void; onEdit?: (id: string, text: string) => void }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(comment.text);
  const name = comment.file.split(/[\\/]/).pop() || comment.file;
  const where = `${name}:${comment.line}`;
  const save = () => {
    const text = draft.trim();
    if (!text) return;
    onEdit?.(comment.id, text);
    setEditing(false);
  };
  const cancel = () => {
    setDraft(comment.text);
    setEditing(false);
  };

  if (editing)
    return (
      <div className="composer-comment-edit" data-testid={`composer-comment-edit-${comment.id}`}>
        <textarea
          autoFocus
          className="composer-comment-input"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Escape") cancel();
            if (e.key !== "Enter" || e.shiftKey) return;
            e.preventDefault();
            save();
          }}
        />
        <div className="composer-comment-actions">
          <span className="composer-comment-loc">{where}</span>
          <button className="btn sm" onClick={cancel}>
            Cancel
          </button>
          <button className="btn sm primary" disabled={!draft.trim()} onClick={save}>
            Save
          </button>
        </div>
      </div>
    );

  return (
    <div className="composer-comment" data-testid={`composer-comment-${comment.id}`} role="button" tabIndex={0} title="Edit comment" onClick={() => setEditing(true)} onKeyDown={(e) => e.key === "Enter" && setEditing(true)}>
      <div className="composer-comment-top">
        <FileIcon path={comment.file} size={14} />
        <span className="composer-comment-name">{name}</span>
        <span className="composer-comment-loc">:{comment.line}</span>
        <button
          className="composer-comment-x"
          aria-label={`Remove comment on ${where}`}
          title="Remove comment"
          onClick={(e) => {
            e.stopPropagation();
            onRemove?.(comment.id);
          }}
        >
          ✕
        </button>
      </div>
      <div className="composer-comment-text">{comment.text}</div>
    </div>
  );
}
