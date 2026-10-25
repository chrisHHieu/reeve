// The branch picker: the folder's branches, searchable, local first with how far
// each is ahead of / behind its upstream, then the remote ones not checked out yet. Pick one
// to switch to it; type a new name to create it from where HEAD is. Before a session's first
// message it can instead make a new worktree for the branch, so the session works in its
// own checkout and the folder stays on its branch.
//
// Switching is the person's git write. When git refuses (changes the other branch would
// overwrite) its own sentence is shown and nothing is forced or stashed.

import { useContext, useEffect, useMemo, useRef, useState } from "react";
import { getBranches, newWorktree, switchBranch, type Branches } from "../api/branches";
import { Icon } from "./Icon";
import { FolderActivityContext } from "./folderActivity";

/** Said on `window` after a switch, with `{ branch }`: the composer's chip and Review both
 *  show the folder's branch, and either can change it. */
export const BRANCH_SWITCHED_EVENT = "reeve:branch-switched";

/** Names git would misread (an option, a range, a reflog) are not offered for creating. */
export function validBranchName(name: string): boolean {
  return /^(?!-)(?!.*\.\.)(?!.*@\{)(?!.*\.lock$)(?!.*\/$)[A-Za-z0-9._/-]{1,120}$/.test(name);
}

export function BranchPicker({
  path,
  allowWorktree = false,
  onSwitched,
  onWorktree,
  onClose,
}: {
  /** The folder whose branch this is. */
  path: string;
  /** Offer "in a new worktree" (a session that has not started yet). */
  allowWorktree?: boolean;
  onSwitched: (branch: string) => void;
  onWorktree?: (path: string, branch: string) => void;
  onClose: () => void;
}) {
  const [data, setData] = useState<Branches | null>(null);
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Another session working in this folder: switching the branch moves the files under it, and
  // a session not started yet is better off in its own checkout.
  const busyWith = useContext(FolderActivityContext)(path);
  const offerWorktree = allowWorktree && !!onWorktree;
  const [asWorktree, setAsWorktree] = useState(() => offerWorktree && !!busyWith);
  const input = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    let alive = true;
    getBranches(path)
      .then((value) => alive && setData(value))
      .catch(() => alive && setData({ is_git: false, local: [], remote: [] }));
    input.current?.focus();
    return () => {
      alive = false;
    };
  }, [path]);

  const needle = query.trim().toLowerCase();
  const local = useMemo(
    () => (data?.local ?? []).filter((b) => !needle || b.name.toLowerCase().includes(needle)),
    [data, needle],
  );
  const remote = useMemo(
    () => (data?.remote ?? []).filter((b) => !needle || b.name.toLowerCase().includes(needle)),
    [data, needle],
  );
  const exists = (data?.local ?? []).some((b) => b.name === query.trim());
  const canCreate = !!query.trim() && !exists && validBranchName(query.trim());

  const act = async (branch: string, create: boolean) => {
    if (busy) return;
    setBusy(branch);
    setError(null);
    if (asWorktree && onWorktree) {
      const made = await newWorktree(path, branch, create).catch((exc: unknown) => ({
        ok: false,
        error: exc instanceof Error ? exc.message : String(exc),
        path: undefined,
        branch: undefined,
      }));
      setBusy(null);
      if (!made.ok || !made.path) {
        setError(made.error || "the worktree could not be made");
        return;
      }
      onWorktree(made.path, made.branch || branch);
      onClose();
      return;
    }
    const result = await switchBranch(path, branch, create).catch((exc: unknown) => ({
      ok: false,
      error: exc instanceof Error ? exc.message : String(exc),
      branch: undefined,
    }));
    setBusy(null);
    if (!result.ok) {
      setError(result.error || "git refused");
      return;
    }
    const now = result.branch || branch;
    window.dispatchEvent(new CustomEvent(BRANCH_SWITCHED_EVENT, { detail: { branch: now, path } }));
    onSwitched(now);
    onClose();
  };

  if (data && !data.is_git) {
    return (
      <div className="branch-picker" data-testid="branch-picker">
        <div className="branch-note">This folder is not a git repository.</div>
      </div>
    );
  }

  return (
    <div className="branch-picker" data-testid="branch-picker" role="dialog" aria-label="Switch branch">
      <label className="branch-search">
        <Icon name="branch" size={13} />
        <input
          ref={input}
          value={query}
          placeholder="Find or create a branch"
          aria-label="Find or create a branch"
          data-testid="branch-search"
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Escape") onClose();
            if (event.key === "Enter" && canCreate) void act(query.trim(), true);
          }}
        />
      </label>
      {busyWith && (
        <div className="branch-note" data-testid="branch-busy">
          “{busyWith}” is working in this folder.{" "}
          {asWorktree
            ? "A new worktree keeps this session's changes apart from it."
            : "Switching the branch changes the files under it too."}
        </div>
      )}
      {data?.dirty && !asWorktree && (
        <div className="branch-note" data-testid="branch-dirty">
          You have uncommitted changes. They come along to the other branch, unless they would be
          overwritten there: then git says so and nothing moves.
        </div>
      )}
      {error && (
        <div className="scm-error branch-error" role="alert" data-testid="branch-error">
          {error}
        </div>
      )}
      <div className="branch-list" role="listbox" aria-label="Branches">
        {!data && <div className="branch-note">Loading…</div>}
        {canCreate && (
          <button
            type="button"
            className="branch-row create"
            data-testid="branch-create"
            disabled={!!busy}
            onClick={() => void act(query.trim(), true)}
          >
            <Icon name="plus" size={13} />
            <span className="branch-name">
              Create <b>{query.trim()}</b>
            </span>
            <span className="branch-meta">from {data?.current || "HEAD"}</span>
          </button>
        )}
        {local.length > 0 && <div className="branch-group">This computer</div>}
        {local.map((b) => (
          <button
            key={b.name}
            type="button"
            role="option"
            aria-selected={b.current}
            className={"branch-row" + (b.current ? " on" : "")}
            data-testid={`branch-${b.name}`}
            disabled={!!busy || (b.current && !asWorktree)}
            title={b.subject ? `${b.name} · ${b.subject}` : b.name}
            onClick={() => void act(b.name, false)}
          >
            {b.current ? <Icon name="check" size={13} /> : <span className="branch-gap" />}
            <span className="branch-name">{b.name}</span>
            {b.ahead > 0 && <span className="branch-count" title="Commits not pushed">↑{b.ahead}</span>}
            {b.behind > 0 && <span className="branch-count" title="Commits to pull">↓{b.behind}</span>}
            {b.gone && <span className="branch-meta">remote deleted</span>}
            <span className="branch-meta">{busy === b.name ? "Switching…" : b.when}</span>
          </button>
        ))}
        {remote.length > 0 && <div className="branch-group">On the remote</div>}
        {remote.map((b) => (
          <button
            key={b.name}
            type="button"
            role="option"
            aria-selected={false}
            className="branch-row"
            data-testid={`branch-${b.name}`}
            disabled={!!busy}
            onClick={() => void act(b.name, false)}
          >
            <Icon name="globe" size={13} />
            <span className="branch-name">{b.name}</span>
            <span className="branch-meta">{busy === b.name ? "Switching…" : "check out"}</span>
          </button>
        ))}
        {data && !local.length && !remote.length && !canCreate && (
          <div className="branch-note">No branch matches. A new name creates one.</div>
        )}
      </div>
      {offerWorktree && (
        <label className="branch-worktree" title="The session works in its own checkout; this folder stays on its branch">
          <input
            type="checkbox"
            checked={asWorktree}
            data-testid="branch-as-worktree"
            onChange={(event) => setAsWorktree(event.target.checked)}
          />
          In a new worktree, so this folder stays on {data?.current || "its branch"}
        </label>
      )}
    </div>
  );
}
