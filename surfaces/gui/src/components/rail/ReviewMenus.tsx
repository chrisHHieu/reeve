// Review's scope menu (what to compare), a pop-over that closes on Escape or a click elsewhere,
// and the commit box, docked under the file list wherever the working tree is on show
// (workspace panel redesign, 2026-10-11: it was a pop-over behind a Commit button that sat
// disabled, unexplained, whenever there was nothing to commit).

import { useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { scmCommit, scmPush, scmSuggestMessage, type ScmCommit } from "../../api";
import { useDismiss } from "../../useDismiss";
import { BranchPicker } from "../BranchPicker";
import { Icon } from "../Icon";
import {
  GIT_SCOPES,
  REEVE_SCOPES,
  isCommitScope,
  isGitScope,
  scopeLabel,
  type ReviewScope,
} from "./reviewModel";

export function PopoverShell({
  open,
  onClose,
  trigger,
  children,
  align = "left",
}: {
  open: boolean;
  onClose: () => void;
  trigger: ReactNode;
  children: ReactNode;
  align?: "left" | "right";
}) {
  const ref = useDismiss(open, onClose);
  // The panel ends above the window's foot and scrolls inside: on a laptop window (1093x520)
  // the scope menu ran 54px past it, hiding "A commit" and "Switch branch…".
  const panelRef = useRef<HTMLDivElement | null>(null);
  const [room, setRoom] = useState<number | null>(null);
  useLayoutEffect(() => {
    if (!open) return;
    const fit = () => {
      const top = panelRef.current?.getBoundingClientRect().top;
      if (top !== undefined) setRoom(Math.max(160, Math.floor(window.innerHeight - top - 12)));
    };
    fit();
    window.addEventListener("resize", fit);
    return () => window.removeEventListener("resize", fit);
  }, [open]);
  return (
    <div className="rvw-pop" ref={ref}>
      {trigger}
      {open && (
        <div ref={panelRef} className={"rvw-pop-panel " + align} style={room ? { maxHeight: room } : undefined}>
          {children}
        </div>
      )}
    </div>
  );
}

export function ReviewScopeMenu({
  scope,
  isGit,
  counts,
  branch,
  ahead,
  base,
  commits,
  hasMoreCommits,
  onMoreCommits,
  onPick,
  workspacePath,
  onBranchSwitched,
}: {
  scope: ReviewScope;
  isGit: boolean;
  counts: Partial<Record<ReviewScope, number>>;
  branch?: string;
  ahead?: number | null;
  /** The branch "Against …" compares with. */
  base?: string;
  commits: ScmCommit[];
  hasMoreCommits: boolean;
  onMoreCommits: () => void;
  onPick: (scope: ReviewScope) => void;
  /** The folder whose branch the picker switches; no picker without it. */
  workspacePath?: string;
  onBranchSwitched?: (branch: string) => void;
}) {
  const [open, setOpen] = useState(false);
  // The menu's pages: the scopes, the commits to pick one from, the branches to switch to.
  const [page, setPage] = useState<"scopes" | "commits" | "branches">("scopes");
  const close = () => {
    setOpen(false);
    setPage("scopes");
  };
  const pick = (next: ReviewScope) => {
    onPick(next);
    close();
  };
  const item = (id: ReviewScope, label: string, hint: string) => (
    <button
      key={id}
      type="button"
      role="option"
      aria-selected={id === scope}
      className={"rvw-scope-item" + (id === scope ? " on" : "")}
      data-testid={`rvw-scope-${id}`}
      onClick={() => pick(id)}
    >
      <span className="rvw-scope-text">
        <span className="rvw-scope-label">{label}</span>
        <span className="rvw-scope-hint">{hint}</span>
      </span>
      {counts[id] !== undefined && (
        <span className="rvw-scope-count">
          {counts[id] ? `${counts[id]} file${counts[id] === 1 ? "" : "s"}` : "—"}
        </span>
      )}
      <span className="rvw-scope-check" aria-hidden>
        {id === scope && <Icon name="check" size={13} />}
      </span>
    </button>
  );
  const selectedSha = isCommitScope(scope) ? scope.slice("commit:".length) : "";
  return (
    <PopoverShell
      open={open}
      onClose={close}
      trigger={
        <button
          type="button"
          className="rvw-scope-btn"
          aria-haspopup="listbox"
          aria-expanded={open}
          data-testid="rvw-scope"
          onClick={() => (open ? close() : setOpen(true))}
        >
          <Icon name={isGitScope(scope) ? "branch" : "chat"} size={14} />
          {isCommitScope(scope) && commits.find((c) => c.hash === scope.slice("commit:".length))
            ? `Commit ${commits.find((c) => c.hash === scope.slice("commit:".length))!.short}`
            : scopeLabel(scope, base)}
          <Icon name={open ? "chevronUp" : "chevronDown"} size={13} />
        </button>
      }
    >
      {page === "scopes" ? (
        <div role="listbox" aria-label="What to review" className="rvw-scope-menu">
          <div className="rvw-scope-group">
            <div className="rvw-scope-title">Reeve’s work</div>
            {REEVE_SCOPES.map((s) => item(s.id, s.label, s.hint))}
          </div>
          {isGit && (
            <div className="rvw-scope-group">
              <div className="rvw-scope-title">
                Git{branch ? " · " : ""}
                {branch && <span className="rvw-scope-branch-name">{branch}</span>}
              </div>
              {GIT_SCOPES.map((s) => item(s.id, s.label, s.hint))}
              {item("branch", `Against ${base || "the base branch"}`, "the whole branch, what a pull request would show")}
              <button
                type="button"
                className={"rvw-scope-item" + (isCommitScope(scope) ? " on" : "")}
                data-testid="rvw-scope-commits"
                onClick={() => setPage("commits")}
              >
                <span className="rvw-scope-text">
                  <span className="rvw-scope-label">A commit</span>
                  <span className="rvw-scope-hint">pick one from the history</span>
                </span>
                <Icon name="chevronRight" size={13} />
              </button>
              {workspacePath && (
                <button
                  type="button"
                  className="rvw-scope-item rvw-scope-action"
                  data-testid="rvw-switch-branch"
                  onClick={() => setPage("branches")}
                >
                  <Icon name="branch" size={13} />
                  <span className="rvw-scope-label">Switch branch…</span>
                </button>
              )}
            </div>
          )}
        </div>
      ) : page === "branches" && workspacePath ? (
        <div className="rvw-scope-menu" data-testid="rvw-branches">
          <button type="button" className="rvw-history-back" onClick={() => setPage("scopes")}>
            <Icon name="arrowLeft" size={13} />
            Switch branch
          </button>
          <BranchPicker
            path={workspacePath}
            onSwitched={(name) => onBranchSwitched?.(name)}
            onClose={close}
          />
        </div>
      ) : (
        <div className="rvw-scope-menu rvw-history-page" data-testid="rvw-history">
          <button type="button" className="rvw-history-back" data-testid="rvw-history-back" onClick={() => setPage("scopes")}>
            <Icon name="arrowLeft" size={13} />
            Pick a commit
          </button>
          {commits.length === 0 ? (
            <div className="rvw-scope-hint rvw-history-none">No commits yet.</div>
          ) : (
            <ol className="rvw-history">
              {commits.map((c, index) => (
                <li key={c.hash}>
                  {/* What is not on the remote yet is said once, as a divider, not on every row. */}
                  {!!ahead && index === 0 && (
                    <div className="rvw-history-div" data-testid="rvw-history-unpushed">
                      Not pushed · {ahead}
                    </div>
                  )}
                  {!!ahead && index === ahead && <div className="rvw-history-div">On the remote</div>}
                  <button
                    type="button"
                    className={"rvw-commit-row" + (c.hash.startsWith(selectedSha) && selectedSha ? " on" : "")}
                    data-testid={`rvw-commit-${c.short}`}
                    title={`${c.short} · ${c.author} · ${c.when}`}
                    onClick={() => pick(`commit:${c.hash}`)}
                  >
                    <span className={"rvw-node" + (index === 0 ? " head" : "")} aria-hidden />
                    <span className="rvw-commit-text">
                      <span className="rvw-commit-subject">{c.subject}</span>
                      <span className="rvw-commit-meta">
                        {c.short} · {c.when} · {c.author}
                      </span>
                    </span>
                    {c.refs.map((ref) => (
                      <span key={ref} className={"rvw-ref" + (ref.startsWith("HEAD") ? " head" : "")}>
                        {ref.replace("HEAD -> ", "")}
                      </span>
                    ))}
                  </button>
                </li>
              ))}
            </ol>
          )}
          {hasMoreCommits && (
            <button type="button" className="rvw-history-more" data-testid="rvw-history-more" onClick={onMoreCommits}>
              Show older commits…
            </button>
          )}
        </div>
      )}
    </PopoverShell>
  );
}

export function ReviewCommit({
  sessionId,
  branch,
  stagedCount,
  totalCount,
  add,
  del,
  remote,
  onCommitted,
}: {
  sessionId: string;
  branch?: string;
  /** Where a push goes; no "Push after" without one. */
  remote?: string | null;
  stagedCount: number;
  totalCount: number;
  add: number;
  del: number;
  /** After a commit: its hash, whether it was pushed too, and why a push failed. */
  onCommitted: (hash: string | undefined, pushed: boolean, pushError?: string) => void;
}) {
  const [message, setMessage] = useState("");
  const [all, setAll] = useState(stagedCount === 0);
  const [pushAfter, setPushAfter] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ids = useId();
  // What goes in follows what is staged until the person picks: staging a file flips the
  // default to "staged only", unstaging the last one back to everything.
  const [picked, setPicked] = useState(false);
  useEffect(() => {
    if (!picked) setAll(stagedCount === 0);
  }, [stagedCount, picked]);

  const count = all ? totalCount : stagedCount;
  const [writing, setWriting] = useState(false);
  // At rest the box is one line, so the diffs keep the panel: on a laptop window (1093x520) the
  // full box took 149px of the 480 under the bar. It opens while it is in use: focused, holding a
  // message, drafting, committing, or saying what went wrong.
  const boxRef = useRef<HTMLDivElement | null>(null);
  const [focused, setFocused] = useState(false);
  const open = focused || !!message.trim() || writing || busy || !!error;
  useEffect(() => {
    if (!focused) return;
    const onDown = (event: PointerEvent) => {
      if (!boxRef.current?.contains(event.target as Node)) setFocused(false);
    };
    window.addEventListener("pointerdown", onDown);
    return () => window.removeEventListener("pointerdown", onDown);
  }, [focused]);
  // One model call on the session's model: a message for what the commit would hold, put in
  // the box for the person to read and edit — never committed as is.
  const suggest = async () => {
    if (writing || count === 0) return;
    setWriting(true);
    setError(null);
    const result = await scmSuggestMessage(sessionId, all).catch((exc: unknown) => ({
      ok: false,
      error: exc instanceof Error ? exc.message : String(exc),
      message: undefined,
    }));
    setWriting(false);
    if (!result.ok || !result.message) {
      setError(result.error || "no message came back");
      return;
    }
    setMessage(result.message);
  };
  const submit = async () => {
    const text = message.trim();
    if (!text || busy || count === 0) return;
    const push = pushAfter && !!remote;
    setBusy(true);
    setError(null);
    const result = await scmCommit(sessionId, text, all).catch((exc: unknown) => ({
      ok: false,
      error: exc instanceof Error ? exc.message : String(exc),
      commit: undefined,
    }));
    if (!result.ok) {
      setBusy(false);
      setError(result.error || "git refused");
      return;
    }
    let pushed = false;
    let pushError: string | undefined;
    if (push) {
      const sent = await scmPush(sessionId).catch((exc: unknown) => ({
        ok: false,
        error: exc instanceof Error ? exc.message : String(exc),
      }));
      pushed = sent.ok;
      pushError = sent.ok ? undefined : sent.error || "the push failed";
    }
    setBusy(false);
    setMessage("");
    setPicked(false);
    onCommitted(result.commit, pushed, pushError);
  };

  // A disabled Commit says why, next to it.
  const why = count === 0 ? "Nothing staged" : !message.trim() ? "Write a message first" : "";
  const what = `${totalCount} file${totalCount === 1 ? "" : "s"}${add > 0 ? ` · +${add}` : ""}${del > 0 ? ` −${del}` : ""}`;

  return (
    <div
      ref={boxRef}
      className={"rvw-commit" + (open ? " open" : "")}
      role="group"
      aria-label="Commit"
      data-testid="rvw-commit"
      data-open={open}
      onFocus={() => setFocused(true)}
      onBlur={(event) => {
        // Tabbing out closes it; a click on the page around it is the pointer listener's.
        const next = event.relatedTarget as Node | null;
        if (next && !event.currentTarget.contains(next)) setFocused(false);
      }}
    >
      <div className="rvw-commit-line">
        <textarea
          id={`${ids}-msg`}
          data-testid="rvw-commit-message"
          aria-label="Commit message"
          rows={1}
          value={message}
          placeholder={`What changed${branch ? ` on ${branch}` : ""}, and why (Ctrl+Enter commits)`}
          onChange={(event) => setMessage(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
              event.preventDefault();
              void submit();
            }
          }}
        />
        <button
          type="button"
          className="rvw-write"
          data-testid="rvw-commit-write"
          disabled={writing || count === 0}
          aria-busy={writing}
          title="Reeve reads what this commit holds and drafts a message (one model call)"
          onClick={() => void suggest()}
        >
          <Icon name="sparkle" size={12} />
          {writing ? "Writing…" : "Write it for me"}
        </button>
      </div>
      {error && (
        <div className="scm-error" role="alert" data-testid="rvw-commit-error">
          {error}
        </div>
      )}
      <div className="rvw-commit-fold" data-open={open} {...(open ? {} : { inert: "" })}>
        <div className="rvw-commit-fold-in">
          <div className="rvw-commit-actions">
            {stagedCount > 0 ? (
              <div className="seg seg-words" role="group" aria-label="What goes in">
                <button
                  type="button"
                  className={!all ? "on" : ""}
                  aria-pressed={!all}
                  onClick={() => {
                    setPicked(true);
                    setAll(false);
                  }}
                >
                  Staged {stagedCount}
                </button>
                <button
                  type="button"
                  className={all ? "on" : ""}
                  aria-pressed={all}
                  onClick={() => {
                    setPicked(true);
                    setAll(true);
                  }}
                >
                  All {totalCount}
                </button>
              </div>
            ) : (
              <span className="rvw-commit-what">{what}</span>
            )}
            <span className="rvw-bar-fill" />
            {remote && (
              <label className="rvw-commit-pushafter" title={`Then push ${branch || "the branch"} to ${remote}`}>
                <input
                  type="checkbox"
                  data-testid="rvw-commit-push"
                  checked={pushAfter}
                  onChange={(event) => setPushAfter(event.target.checked)}
                />
                Push after
              </label>
            )}
            {why && (
              <span className="rvw-commit-why" id={`${ids}-why`}>
                {why}
              </span>
            )}
            <button
              type="button"
              className="rvw-primary"
              data-testid="rvw-commit-go"
              disabled={busy || !!why}
              aria-describedby={why ? `${ids}-why` : undefined}
              onClick={() => void submit()}
            >
              {busy ? "Working…" : pushAfter && remote ? "Commit & push" : "Commit"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
