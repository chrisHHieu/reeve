// The branch's pull request on Review's bar: "#12" with its checks' verdict, and
// a pop-over with the title, the review decision, each check (a link to its run) and "Open
// on GitHub". Read through the person's own GitHub CLI; nothing shows without it.
//
// Opening one is a dialog: title (the last commit's subject), description, base branch and
// Draft. The branch is pushed first when it has no upstream.

import { useEffect, useState } from "react";
import { createPullRequest, type PullRequest } from "../../api";
import { openExternal } from "../../desktop";
import { Icon } from "../Icon";
import { PopoverShell } from "./ReviewMenus";

const VERDICT_LABEL: Record<PullRequest["checks"]["verdict"], string> = {
  passed: "Checks passed",
  failed: "Checks failed",
  running: "Checks running",
  none: "No checks",
};

const REVIEW_LABEL: Record<string, string> = {
  APPROVED: "Approved",
  CHANGES_REQUESTED: "Changes requested",
  REVIEW_REQUIRED: "Review required",
};

export function PullRequestBadge({ pr }: { pr: PullRequest }) {
  const [open, setOpen] = useState(false);
  const verdict = pr.checks.verdict;
  return (
    <PopoverShell
      open={open}
      onClose={() => setOpen(false)}
      align="right"
      trigger={
        <button
          type="button"
          className="rvw-btn pr-badge"
          data-verdict={verdict}
          data-testid="pr-badge"
          aria-expanded={open}
          title={`${pr.title} · ${VERDICT_LABEL[verdict]}`}
          onClick={() => setOpen((v) => !v)}
        >
          <span className="pr-dot" aria-hidden />#{pr.number}
          {pr.draft ? " · Draft" : ""}
        </button>
      }
    >
      <div className="pr-pop" data-testid="pr-pop">
        <div className="pr-title">
          {pr.title} <span className="pr-number">#{pr.number}</span>
        </div>
        <div className="pr-meta">
          {pr.head} → {pr.base}
          {pr.draft ? " · Draft" : ""}
          {pr.review && REVIEW_LABEL[pr.review] ? ` · ${REVIEW_LABEL[pr.review]}` : ""}
        </div>
        <div className="pr-verdict" data-verdict={verdict}>
          <span className="pr-dot" aria-hidden />
          {VERDICT_LABEL[verdict]}
          {pr.checks.items.length > 0 && (
            <span className="pr-counts">
              {pr.checks.passed} passed{pr.checks.failed ? ` · ${pr.checks.failed} failed` : ""}
              {pr.checks.running ? ` · ${pr.checks.running} running` : ""}
            </span>
          )}
        </div>
        {pr.checks.items.length > 0 && (
          <ul className="pr-checks">
            {pr.checks.items.map((check, index) => (
              <li key={`${check.name}-${index}`} data-state={check.state}>
                <span className="pr-dot" aria-hidden />
                {check.url ? (
                  <button type="button" className="pr-check-link" onClick={() => openExternal(check.url!)}>
                    {check.name}
                  </button>
                ) : (
                  <span>{check.name}</span>
                )}
              </li>
            ))}
          </ul>
        )}
        <button type="button" className="rvw-primary pr-open" onClick={() => openExternal(pr.url)}>
          <Icon name="externalLink" size={13} />
          Open on GitHub
        </button>
      </div>
    </PopoverShell>
  );
}

export function CreatePullRequestDialog({
  sessionId,
  branch,
  base,
  defaultTitle,
  onDone,
  onClose,
}: {
  sessionId: string;
  branch?: string;
  base?: string;
  defaultTitle: string;
  onDone: (url: string) => void;
  onClose: () => void;
}) {
  const [title, setTitle] = useState(defaultTitle);
  const [body, setBody] = useState("");
  const [into, setInto] = useState(base || "");
  const [draft, setDraft] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => setTitle(defaultTitle), [defaultTitle]);
  const submit = async () => {
    if (!title.trim() || busy) return;
    setBusy(true);
    setError(null);
    const result = await createPullRequest(sessionId, { title: title.trim(), body, base: into.trim(), draft }).catch(
      (exc: unknown) => ({ ok: false, error: exc instanceof Error ? exc.message : String(exc), url: undefined }),
    );
    setBusy(false);
    if (!result.ok) {
      setError(result.error || "the pull request was not opened");
      return;
    }
    onDone(result.url || "");
  };
  return (
    <div className="fm-dialog-backdrop" onClick={() => !busy && onClose()}>
      <div
        className="fm-dialog pr-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="pr-dialog-title"
        data-testid="pr-dialog"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="fm-dialog-title" id="pr-dialog-title">
          Open a pull request
        </div>
        <div className="fm-dialog-hint">
          {branch || "This branch"} → {into || "the default branch"}. It is pushed first if it has not been.
        </div>
        <label className="pr-field">
          <span>Title</span>
          <input data-testid="pr-title" value={title} onChange={(event) => setTitle(event.target.value)} />
        </label>
        <label className="pr-field">
          <span>Description</span>
          <textarea
            data-testid="pr-body"
            rows={5}
            value={body}
            placeholder="What changed, why, and how it was checked"
            onChange={(event) => setBody(event.target.value)}
          />
        </label>
        <label className="pr-field inline">
          <span>Into</span>
          <input data-testid="pr-base" value={into} onChange={(event) => setInto(event.target.value)} />
        </label>
        <label className="pr-field check">
          <input type="checkbox" checked={draft} data-testid="pr-draft" onChange={(event) => setDraft(event.target.checked)} />
          Open as a draft
        </label>
        {error && (
          <div className="scm-error" role="alert" data-testid="pr-error">
            {error}
          </div>
        )}
        <div className="fm-dialog-actions">
          <button type="button" className="btn sm" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button
            type="button"
            className="btn sm primary"
            data-testid="pr-create"
            disabled={busy || !title.trim()}
            onClick={() => void submit()}
          >
            {busy ? "Opening…" : "Open pull request"}
          </button>
        </div>
      </div>
    </div>
  );
}
