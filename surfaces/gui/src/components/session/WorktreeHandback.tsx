// A child's work that is waiting for the person.
//
// A child that worked in its own copy of the repository (a git worktree) leaves its changes
// THERE. The model is told they are not in the workspace; nothing else told the person, and
// "merge the branch" is not an answer for someone who does not use git. This is the answer
// in two buttons, under the child's own row:
//
//   · Apply puts all of the child's changes into the session's folder, or none of them: when
//     the folder changed under the same lines since the copy was made, nothing is applied and
//     the row says so (the copy stays). Applied changes are ordinary uncommitted changes —
//     Review shows them like any edit.
//   · Discard removes the copy and what is in it. Two presses: it is the one that loses work.
//
// The list of files is the copy's own difference from where it started, read from the server
// when the row appears: a person recognises "these three files", they do not predict a merge.

import { useCallback, useEffect, useRef, useState } from "react";
import {
  applyChildWorktree,
  discardChildWorktree,
  getChildWorktree,
  type ChildWorktree,
} from "../../api/worktree";

/** Fired when a copy was applied or discarded, so the roster asks which children still have one. */
export const WORKTREE_CHANGED = "reeve:worktree-changed";
/** How long an armed Discard waits for its confirming press. */
export const DISCARD_ARM_MS = 3000;

/** `3 files changed` — or what the copy says when it holds nothing. */
export function handbackSummary(view: ChildWorktree): string {
  const count = view.files.length;
  if (count === 0) return "Its own copy holds no changes";
  return `${count} file${count === 1 ? "" : "s"} changed in its own copy — not in your folder yet`;
}

export function WorktreeHandback({ childId }: { childId: string }) {
  const [view, setView] = useState<ChildWorktree | null>(null);
  const [working, setWorking] = useState<"" | "apply" | "discard">("");
  const [armed, setArmed] = useState(false);
  const [error, setError] = useState("");
  const [listed, setListed] = useState(false);
  const timer = useRef<number | null>(null);

  useEffect(() => {
    let alive = true;
    getChildWorktree(childId)
      .then((next) => alive && setView(next))
      .catch(() => alive && setView(null));
    return () => {
      alive = false;
      if (timer.current !== null) window.clearTimeout(timer.current);
    };
  }, [childId]);

  const settle = useCallback(async (verb: "apply" | "discard") => {
    setWorking(verb);
    setError("");
    const call = verb === "apply" ? applyChildWorktree : discardChildWorktree;
    const result = await call(childId).catch(() => ({ ok: false, error: "the server did not answer" }));
    setWorking("");
    setArmed(false);
    if (!result.ok) {
      setError(result.error || "That did not work.");
      return;
    }
    setView(null);
    window.dispatchEvent(new Event(WORKTREE_CHANGED));
  }, [childId]);

  if (!view) return null;
  const empty = view.files.length === 0;
  const blocked = working !== "" || view.busy;
  return (
    <div className="handback" data-testid="worktree-handback">
      <div className="handback-line">
        <span className="handback-text" data-testid="handback-summary">
          {handbackSummary(view)}
        </span>
        {!empty && (
          <button
            type="button"
            className="handback-files-toggle"
            aria-expanded={listed}
            data-testid="handback-files-toggle"
            onClick={() => setListed((value) => !value)}
          >
            {listed ? "Hide files" : "Show files"}
          </button>
        )}
        <span className="handback-spacer" />
        {!empty && (
          <button
            type="button"
            className="handback-button accent"
            disabled={blocked}
            title={view.busy ? "This subagent is still working" : "Put these changes into your folder"}
            data-testid="handback-apply"
            onClick={() => void settle("apply")}
          >
            {working === "apply" ? "Applying…" : "Apply"}
          </button>
        )}
        <button
          type="button"
          className={"handback-button" + (armed ? " armed" : "")}
          disabled={blocked}
          title="Remove the copy and the changes in it"
          data-testid="handback-discard"
          onClick={() => {
            if (armed || empty) {
              void settle("discard");
              return;
            }
            // The one answer that loses work asks twice, and forgets it was asked.
            setArmed(true);
            if (timer.current !== null) window.clearTimeout(timer.current);
            timer.current = window.setTimeout(() => setArmed(false), DISCARD_ARM_MS);
          }}
        >
          {working === "discard" ? "Discarding…" : armed ? "Discard for good?" : "Discard"}
        </button>
      </div>
      {listed && (
        <ul className="handback-files" data-testid="handback-files">
          {view.files.map((file) => (
            <li key={file.path} data-change={file.change}>
              <span className="handback-file-path" title={file.path}>
                {file.path}
              </span>
              <span className="handback-file-change">{file.change}</span>
              {(file.added > 0 || file.removed > 0) && (
                <span className="handback-file-counts">
                  {file.added > 0 && <span className="handback-added">+{file.added}</span>}
                  {file.removed > 0 && <span className="handback-removed">−{file.removed}</span>}
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
      {(error || view.error) && (
        <p className="handback-error" data-testid="handback-error">
          {error || view.error}
        </p>
      )}
    </div>
  );
}
