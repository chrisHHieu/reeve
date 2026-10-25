// The review comments a model left in its answer (`:code-comment{…}`), as one collapsible
// card list under it.
//
// Under the answer, not inside it: a comment is about the code that was just written, so it
// belongs after the prose a reader finishes, and a card per comment keeps the file, the line
// range and the priority readable at a glance. Collapsed state is local and starts open while
// the comments are few (the reader was just handed them), closed past three.

import { useState } from "react";
import { FileIcon } from "../FileIcon";
import { Icon } from "../Icon";
import type { CodeComment } from "./directives";

function lineLabel(comment: CodeComment): string {
  if (comment.start === undefined) return "";
  if (comment.end !== undefined && comment.end !== comment.start) {
    return `dòng ${comment.start}–${comment.end}`;
  }
  return `dòng ${comment.start}`;
}

export function CodeCommentCards({ comments }: { comments: CodeComment[] }) {
  const [open, setOpen] = useState(comments.length <= 3);
  if (comments.length === 0) return null;
  return (
    <div className="code-comments" data-open={open || undefined} data-testid="code-comments">
      <button
        type="button"
        className="code-comments-head"
        aria-expanded={open}
        data-testid="code-comments-toggle"
        onClick={() => setOpen((value) => !value)}
      >
        <Icon name="inspect" size={13} />
        <b>
          {comments.length} review comment{comments.length === 1 ? "" : "s"}
        </b>
        <span className="code-comments-hint">từ lượt này</span>
        <span className="code-comments-chev" data-open={open || undefined}>
          ›
        </span>
      </button>
      {open && (
        <div className="code-comments-body">
          {comments.map((comment, index) => (
            <div className="code-comment" key={index} data-priority={comment.priority ?? "low"}>
              <span className="code-comment-prio" aria-hidden />
              <div className="code-comment-main">
                {comment.title && <h4>{comment.title}</h4>}
                {comment.file && (
                  <div className="code-comment-where">
                    <span className="code-comment-file">
                      <FileIcon path={comment.file} size={12} />
                      {comment.file.split(/[\\/]/).pop() || comment.file}
                    </span>
                    {lineLabel(comment) && <span>{lineLabel(comment)}</span>}
                  </div>
                )}
                {comment.body && <p>{comment.body}</p>}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
