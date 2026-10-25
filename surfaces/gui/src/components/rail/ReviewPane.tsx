// The Review tab (the workspace panel redesign of 2026-10-11; redrawn 2026-10-14, the
// owner's pick B): one bar with the scope menu, the totals, Review and the options; a line saying
// what the branch owes its remote; a line saying whether the work was checked after its last
// edit; then the changed files as cards in one column, each opening in place to its diff. The
// working tree's files are grouped by the person's request that changed each last (with Ask for
// a change and Undo per request), and what changed outside this conversation is a group of its
// own. J / K move between files, V marks one viewed, and a viewed file folds and the next one
// opens. The commit box sits at the foot. The reader's line comments go to Reeve from a bar.
//
// Review answers ONE question — what changed, and do I keep it? Two of its scopes are
// Reeve's own work (what its tools changed, by turn, with Undo: they work outside git);
// the rest are git's: the working tree, its staged and unstaged halves, the branch against
// its base, or one commit picked from the history. History is looked at; the working tree's
// files, and even their single hunks, can be staged, unstaged and reverted. What is in the
// folders NOW is the Files tab's job, and a file name here opens there.
//
// The git writes behind Revert / Stage / Commit are the PERSON's: the agent has no route.

import { useEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";
import {
  getPullRequest,
  getScmLog,
  getSessionChanges,
  scmDiscard,
  scmHunk,
  scmPull,
  scmPush,
  scmReview,
  scmStage,
  scmUnstage,
  undoToolCall,
  type ChangeFile,
  type GitScope,
  type PullRequest,
  type ScmLog,
  type ScmResult,
  type SessionChanges,
} from "../../api";
import type { RecordedChange } from "../../reviewChanges";
import type { Item, ReviewComment } from "../../types";
import { Icon } from "../Icon";
import { taskFacts } from "../session/TaskFacts";
import { askForChange, groupByAsk, turnChecks, turnTouches, type AskCheck, type AskGroup } from "./reviewAsks";
import type { DiffStyle, ExpandMode } from "./DiffView";
import { ReviewFileCard, type Finding, type HunkMode } from "./ReviewFileCard";
import { PopoverShell, ReviewCommit, ReviewScopeMenu } from "./ReviewMenus";
import { CreatePullRequestDialog, PullRequestBadge } from "./PullRequestBadge";
import { openExternal } from "../../desktop";
import { ReviewRecorded } from "./ReviewRecorded";
import {
  defaultScope,
  isCommitScope,
  isGitScope,
  isReadOnlyScope,
  isViewed,
  isWorkingScope,
  lastTurnChanges,
  mergeChanges,
  readViewed,
  viewPrefixFor,
  writeViewed,
  type ReviewScope,
} from "./reviewModel";

const MinusIcon = () => <Icon name="minus" size={13} />;

const LOG_PAGE = 50;

type Confirm = {
  label: string;
  /** A whole-file revert names paths; a hunk revert names the hunk; an undo names a request. */
  paths?: string[];
  hunk?: { path: string; index: number; header: string };
  ask?: AskGroup;
};

/** A check, said the way the bar and a request's line say it. */
function checkWords(check: AskCheck): string {
  return check.summary || (check.ok === false ? "check failed" : check.ok ? "check passed" : "check ran");
}

function CheckMark({ check }: { check: AskCheck }) {
  return (
    <span
      className={"rvw-check" + (check.ok === false ? " bad" : check.ok ? " ok" : "")}
      title={check.command ? `${check.command}${check.summary ? ` · ${check.summary}` : ""}` : undefined}
    >
      <Icon name={check.ok === false ? "x" : "check"} size={12} />
      {checkWords(check)}
    </span>
  );
}

export function ReviewPane({
  sessionId,
  isGit,
  changes,
  changesLoading = false,
  recorded,
  items = [],
  roots,
  workspacePath,
  agentTurn,
  onRefresh,
  onOpenFile,
  focusPath,
  focusKey,
  comments,
  onAddComment,
  onDeleteComment,
  onEditComment,
  onSendComments,
  showCommentBar = true,
  diffStyle,
  onDiffStyle,
  onDiffOpen,
}: {
  sessionId: string;
  isGit: boolean;
  /** The working tree against HEAD: the Uncommitted scope, and what Commit is counted from. */
  changes: ChangeFile[];
  /** This conversation's working tree has not been read yet: `changes` is not its answer. */
  changesLoading?: boolean;
  recorded: RecordedChange[];
  /** The conversation: each turn's receipt says the check it ran. */
  items?: readonly Item[];
  roots: string[];
  /** The session's primary folder: the one the branch picker switches. */
  workspacePath?: string;
  /** The newest turn in which Reeve changed a path, if it did. */
  agentTurn: (path: string) => number | undefined;
  onRefresh: () => void;
  onOpenFile: (path: string) => void;
  /** A file Files asked to show, and a counter that changes per ask. */
  focusPath?: string | null;
  focusKey?: number;
  comments?: ReviewComment[];
  onAddComment?: (c: ReviewComment) => void;
  onDeleteComment?: (id: string) => void;
  onEditComment?: (id: string, text: string) => void;
  onSendComments?: () => void;
  /** The composer already lists the comments as chips and sends them; the bar is only for
   *  when it is out of sight (the panel has the whole window). */
  showCommentBar?: boolean;
  diffStyle: DiffStyle;
  onDiffStyle: (style: DiffStyle) => void;
  /** Whether the pane is showing diffs, so the app can give them room. */
  onDiffOpen?: (open: boolean) => void;
}) {
  const [picked, setPicked] = useState<ReviewScope | null>(null);
  const auto = defaultScope(isGit);
  let scope: ReviewScope = picked ?? auto;
  if (isGitScope(scope) && !isGit) scope = "session";
  const viaGit = isGitScope(scope);

  // Context lines show by default (review audit V9): the two lines above a change are how a
  // reader knows where it is.
  const [expandMode, setExpandMode] = useState<ExpandMode>("expand");
  const [viewed, setViewed] = useState<Record<string, string>>(() => readViewed(sessionId));
  // The file J / K / V act on (the last one opened or moved to), and the cards the person opened
  // or closed; a card nobody touched is open when it is the first readable file.
  const [focused, setFocused] = useState<string | null>(null);
  const [opened, setOpened] = useState<Record<string, boolean>>({});
  const cardEls = useRef<Map<string, HTMLElement>>(new Map());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ text: string } | null>(null);
  // Reeve's review of what is on show: its findings, kept with the scope they were made on.
  const [review, setReview] = useState<{ scope: ReviewScope; findings: Finding[]; truncated: boolean } | null>(null);
  const [reviewing, setReviewing] = useState(false);
  const [pushing, setPushing] = useState(false);
  const [pulling, setPulling] = useState(false);
  // The branch's pull request, through the person's GitHub CLI.
  const [prState, setPrState] = useState<{ available: boolean; reason?: string; pr?: PullRequest | null } | null>(null);
  const [prDialog, setPrDialog] = useState(false);
  const [confirm, setConfirm] = useState<Confirm | null>(null);
  const [optionsOpen, setOptionsOpen] = useState(false);
  const [log, setLog] = useState<ScmLog | null>(null);
  const [logLimit, setLogLimit] = useState(LOG_PAGE);
  // A bump each time the working tree is read again: open cards read their diffs again too.
  const [version, setVersion] = useState(0);
  // The files of a git scope that is not the working tree, read from the server.
  const [scopeData, setScopeData] = useState<(SessionChanges & { loading?: boolean }) | null>(null);
  const rootRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => setViewed(readViewed(sessionId)), [sessionId]);
  useEffect(() => setVersion((v) => v + 1), [changes]);
  // A file Files asked for, while Review moves to the scope that holds it.
  const pendingFocus = useRef<string | null>(null);
  const [scrollTo, setScrollTo] = useState<string | null>(null);
  useEffect(() => {
    const keep = pendingFocus.current;
    pendingFocus.current = null;
    setFocused(keep);
    setOpened(keep ? { [keep]: true } : {});
    setScrollTo(keep);
  }, [scope]);
  useEffect(() => {
    if (!scrollTo) return;
    cardEls.current.get(scrollTo)?.scrollIntoView?.({ block: "nearest" });
    setScrollTo(null);
  }, [scrollTo]);

  // Uncommitted comes with the rail; the other git scopes are read here, again whenever the
  // working tree is.
  const fromServer = viaGit && scope !== "uncommitted";
  useEffect(() => {
    if (!fromServer) {
      setScopeData(null);
      return;
    }
    let alive = true;
    setScopeData((prev) => (prev && prev.scope === scope ? prev : { is_git: true, files: [], workspace: null, loading: true, scope }));
    getSessionChanges(sessionId, scope as GitScope)
      .then((value) => alive && setScopeData({ ...value, scope }))
      .catch(() => alive && setScopeData({ is_git: true, files: [], workspace: null, error: "could not read the changes", scope }));
    return () => {
      alive = false;
    };
  }, [fromServer, scope, sessionId, version]);
  const shownChanges = scope === "uncommitted" ? changes : scopeData?.scope === scope ? scopeData.files : [];
  // Nothing on show yet because nothing was read yet: said as loading, never as a clean tree.
  const loading = fromServer ? !!scopeData?.loading : scope === "uncommitted" && changesLoading;

  const files = useMemo(() => mergeChanges(shownChanges, viewPrefixFor(scope)), [shownChanges, scope]);
  // Commit is counted from the working tree, whichever scope is on show.
  const workFiles = useMemo(() => mergeChanges(changes), [changes]);
  const totals = useMemo(
    () => ({
      add: files.reduce((sum, f) => sum + f.add, 0),
      del: files.reduce((sum, f) => sum + f.del, 0),
    }),
    [files],
  );
  const work = useMemo(
    () => ({
      add: workFiles.reduce((sum, f) => sum + f.add, 0),
      del: workFiles.reduce((sum, f) => sum + f.del, 0),
      staged: workFiles.filter((f) => f.staged !== "none").length,
    }),
    [workFiles],
  );
  const lastTurn = useMemo(() => lastTurnChanges(recorded), [recorded]);
  const counts: Partial<Record<ReviewScope, number>> = {
    "last-turn": new Set(lastTurn.map((c) => c.path)).size,
    session: new Set(recorded.map((c) => c.path)).size,
    uncommitted: workFiles.length,
    staged: work.staged,
    unstaged: new Set(changes.filter((c) => !c.staged).map((c) => c.path)).size,
  };
  const findings = review && review.scope === scope ? review.findings : [];
  const commentCounts = useMemo(() => {
    const byFile: Record<string, number> = {};
    for (const c of comments ?? []) byFile[c.file] = (byFile[c.file] ?? 0) + 1;
    for (const f of findings) byFile[f.path] = (byFile[f.path] ?? 0) + 1;
    return byFile;
  }, [comments, findings]);

  // The branch, what is unpushed, and the commits to pick from.
  useEffect(() => {
    if (!isGit) {
      setLog(null);
      return;
    }
    let alive = true;
    // An older server has no log route (a 404 body has no `commits`): read that as "no log".
    getScmLog(sessionId, logLimit)
      .then((value) => alive && setLog(value && Array.isArray(value.commits) ? value : null))
      .catch(() => alive && setLog(null));
    return () => {
      alive = false;
    };
  }, [sessionId, isGit, changes, logLimit]);

  // The pull request for a branch that is not the base: read with the log, so a push or a
  // check that finished shows up on the next refresh.
  const onFeatureBranch = !!log?.branch && log.branch !== "HEAD" && log.branch !== log.base;
  useEffect(() => {
    if (!isGit || !onFeatureBranch) {
      setPrState(null);
      return;
    }
    let alive = true;
    getPullRequest(sessionId)
      .then((value) => alive && setPrState(value && typeof value.available === "boolean" ? value : null))
      .catch(() => alive && setPrState(null));
    return () => {
      alive = false;
    };
  }, [sessionId, isGit, onFeatureBranch, log]);

  const showsDiffs = viaGit && files.length > 0;
  useEffect(() => {
    onDiffOpen?.(showsDiffs);
    return () => onDiffOpen?.(false);
  }, [showsDiffs, onDiffOpen]);

  useEffect(() => {
    if (!notice) return;
    const timer = setTimeout(() => setNotice(null), 6000);
    return () => clearTimeout(timer);
  }, [notice]);

  const push = async () => {
    if (pushing) return;
    setPushing(true);
    setError(null);
    const result = await scmPush(sessionId).catch((exc: unknown) => ({
      ok: false,
      error: exc instanceof Error ? exc.message : String(exc),
      remote: undefined,
    }));
    setPushing(false);
    if (!result.ok) {
      setError(result.error || "the push failed");
      return;
    }
    setNotice({ text: `Pushed ${log?.branch ?? "the branch"} to ${result.remote ?? log?.remote ?? "the remote"}` });
    onRefresh();
  };

  const pull = async () => {
    if (pulling) return;
    setPulling(true);
    setError(null);
    const result = await scmPull(sessionId).catch((exc: unknown) => ({
      ok: false,
      error: exc instanceof Error ? exc.message : String(exc),
    }));
    setPulling(false);
    if (!result.ok) {
      setError(result.error || "the pull failed");
      return;
    }
    setNotice({ text: `${log?.branch ?? "The branch"} is up to date with ${log?.upstream ?? "its upstream"}` });
    onRefresh();
  };

  // One model call on the session's model over what is on show; its findings land under
  // their lines, for the person to act on or dismiss. Nothing changes on its own.
  const runReview = async () => {
    if (reviewing || !isGitScope(scope)) return;
    setReviewing(true);
    setError(null);
    const at = scope;
    const result = await scmReview(sessionId, at).catch((exc: unknown) => ({
      ok: false,
      error: exc instanceof Error ? exc.message : String(exc),
      findings: undefined,
      truncated: false,
    }));
    setReviewing(false);
    if (!result.ok) {
      setError(result.error || "the review did not come back");
      return;
    }
    const list = (result.findings ?? []).map((f, i) => ({ ...f, id: `f${Date.now()}-${i}` }));
    setReview({ scope: at, findings: list, truncated: !!result.truncated });
    const part = result.truncated ? " in the first part of the change" : "";
    setNotice({
      text: list.length
        ? `Reeve raised ${list.length} thing${list.length === 1 ? "" : "s"} to look at${part}`
        : `Reeve found nothing to raise${part}`,
    });
  };
  const dropFinding = (id: string) =>
    setReview((r) => (r ? { ...r, findings: r.findings.filter((f) => f.id !== id) } : r));


  // Files → Review: this file's change. Reeve's own edits sit in This session (with Undo);
  // anything else git knows of is in Uncommitted.
  useEffect(() => {
    if (!focusKey || !focusPath) return;
    if (agentTurn(focusPath) !== undefined) {
      setPicked("session");
      return;
    }
    const match = workFiles.find((f) => focusPath === f.path || focusPath.endsWith(`/${f.path}`));
    if (match && isGit) {
      pendingFocus.current = scope === "uncommitted" ? null : match.path;
      setPicked("uncommitted");
      // The working tree's marks and cards are filed under the bare path.
      setOpened((state) => ({ ...state, [match.path]: true }));
      setFocused(match.path);
      setScrollTo(match.path);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusKey]);

  const run = async (work: () => Promise<ScmResult>, done?: (result: ScmResult) => void) => {
    if (busy) return;
    setBusy(true);
    setError(null);
    const result = await work().catch((exc: unknown) => ({
      ok: false,
      error: exc instanceof Error ? exc.message : String(exc),
    }));
    setBusy(false);
    if (!result.ok) {
      setError(result.error || "git refused");
      return;
    }
    done?.(result);
    onRefresh();
  };

  const markViewed = (viewKey: string, signature: string, on: boolean) => {
    setViewed((current) => {
      const next = { ...current };
      if (on) next[viewKey] = signature;
      else delete next[viewKey];
      writeViewed(sessionId, next);
      return next;
    });
  };

  const working = isWorkingScope(scope);
  const readOnly = isReadOnlyScope(scope);
  const hunkModeFor = (file: { staged: "all" | "some" | "none" }): HunkMode => {
    if (scope === "unstaged") return "unstaged";
    if (scope === "staged") return "staged";
    // The working tree's diff is against HEAD, so its hunks are the index's or the edits'
    // only while nothing of the file is staged.
    if (scope === "uncommitted" && file.staged === "none") return "unstaged";
    return "none";
  };

  const commentList = comments ?? [];
  const commit = isCommitScope(scope) ? log?.commits.find((c) => c.hash === scope.slice("commit:".length)) : undefined;
  const stageable = files.filter((f) => f.staged !== "all");
  const unstageable = files.filter((f) => f.staged !== "none");
  const bulk = (action: () => void) => {
    setOptionsOpen(false);
    action();
  };

  const emptyText = (): { title: string; desc: string } => {
    if (scopeData?.error && fromServer) return { title: "Could not read these changes", desc: scopeData.error };
    if (scope === "staged") return { title: "Nothing is staged", desc: "Stage files to prepare the next commit." };
    if (scope === "unstaged") return { title: "No unstaged changes", desc: "Every edit is staged or committed." };
    if (scope === "branch")
      return {
        title: scopeData?.note ? "This is the base branch" : "No difference",
        desc: scopeData?.note ? "There is nothing to compare it with." : `This branch adds nothing to ${scopeData?.base ?? "its base"}.`,
      };
    if (isCommitScope(scope)) return { title: "This commit changed no files", desc: "It may only have changed the history." };
    const last = log?.commits[0];
    if (!last) return { title: "No uncommitted changes", desc: "Everything is committed." };
    const owed =
      log?.ahead && log.remote
        ? ` ${log.ahead} commit${log.ahead === 1 ? "" : "s"} on ${log.branch} ${log.ahead === 1 ? "is" : "are"} not on ${log.remote} yet.`
        : "";
    return { title: "No uncommitted changes", desc: `Last commit ${last.short}, ${last.when}.${owed}` };
  };

  // The working tree by request (B): Uncommitted only, since history has no turns to group by.
  const checks = useMemo(() => turnChecks(items), [items]);
  const touches = useMemo(() => turnTouches(items), [items]);
  const byAsk = useMemo(
    () => (scope === "uncommitted" ? groupByAsk(files, recorded, roots, checks, touches) : null),
    [scope, files, recorded, roots, checks, touches],
  );
  // The cards in the order they are drawn: what J / K walk.
  const ordered = useMemo(() => (byAsk ? [...byAsk.asks.flatMap((a) => a.files), ...byAsk.outside] : files), [byAsk, files]);
  // A card nobody opened or closed is open when it is the first readable file: never a binary or
  // a tool-generated one, which have nothing to read, and a screenshot is not fetched for nobody
  // to look at. The rest wait for a click, so a long list costs what is read.
  const firstReadable = ordered.find((f) => !f.binary && !f.generated) ?? ordered[0];
  const isOpen = (file: (typeof files)[number]) => opened[file.viewKey] ?? file === firstReadable;
  const current = focused && ordered.some((f) => f.path === focused) ? focused : (firstReadable?.path ?? null);
  const index = current ? ordered.findIndex((f) => f.path === current) : -1;
  const currentFile = index >= 0 ? ordered[index] : undefined;
  const openCard = (file: (typeof files)[number]) => {
    setOpened((state) => ({ ...state, [file.viewKey]: true }));
    setFocused(file.path);
    setScrollTo(file.path);
  };
  const step = (delta: 1 | -1) => {
    const next = ordered[index + delta];
    if (next) openCard(next);
  };
  // A file marked viewed folds, and the next one not viewed yet opens: reading down the list is
  // one key (or one click) per file.
  const setViewedAndMove = (file: (typeof files)[number], on: boolean) => {
    markViewed(file.viewKey, file.signature, on);
    if (!on) return;
    setOpened((state) => ({ ...state, [file.viewKey]: false }));
    const at = ordered.indexOf(file);
    const next = ordered.slice(at + 1).find((f) => !isViewed(viewed, f) && !f.binary);
    if (next) openCard(next);
  };
  // J / K and V, unless the person is typing.
  const onKeyDown = (event: ReactKeyboardEvent) => {
    if (!currentFile || event.altKey || event.ctrlKey || event.metaKey) return;
    if ((event.target as HTMLElement).closest("input, textarea, select, [contenteditable='true']")) return;
    if (event.key === "j") step(1);
    else if (event.key === "k") step(-1);
    else if (event.key === "v") setViewedAndMove(currentFile, !isViewed(viewed, currentFile));
    else return;
    event.preventDefault();
  };
  const viewedCount = ordered.filter((f) => isViewed(viewed, f)).length;

  // Undo one request: each of its changes goes back to the copy saved before it, newest first,
  // so a later edit gives way before an earlier one is restored over it.
  const undoAsk = async (ask: AskGroup) => {
    const list = recorded.filter((change) => (change.turn ?? 0) === ask.turn && change.callId);
    for (const change of [...list].reverse()) {
      const result = await undoToolCall(sessionId, change.callId as string).catch(() => null);
      if (!result || result.ok === false) return { ok: false, error: "a change could not be put back" };
    }
    return { ok: true };
  };

  // The check line: whether the work on show was checked after its last edit.
  const sessionFacts = useMemo(() => taskFacts(items), [items]);

  const lastCommit = log?.commits[0];
  const cleanPush = scope === "uncommitted" && files.length === 0 && !!log?.remote && !!log?.ahead;
  // The sync line says what the branch owes the remote, once: the empty Uncommitted page
  // offers its own Push, so the line does not offer it again there.
  const showPush = !!log?.remote && !!log?.ahead && !cleanPush;
  const showPull = !!log?.upstream && !!log?.behind;
  const showSync = viaGit && !!log?.branch && (!!log?.ahead || showPull || !!prState?.pr);

  const commitDock =
    working && workFiles.length > 0 ? (
      <ReviewCommit
        sessionId={sessionId}
        branch={log?.branch}
        stagedCount={work.staged}
        totalCount={workFiles.length}
        add={work.add}
        del={work.del}
        remote={log?.remote}
        onCommitted={(hash, pushed, pushError) => {
          const what = `Committed${hash ? ` ${hash}` : ""}${log?.branch ? ` to ${log.branch}` : ""}`;
          if (pushError) setError(`${what}, but the push failed: ${pushError}`);
          // A commit not pushed is waiting on the sync line's Push: the toast only says
          // what happened.
          setNotice({ text: pushed ? `${what} and pushed` : what });
          onRefresh();
        }}
      />
    ) : null;

  // One changed file: a card that opens in place to its diff. In a request's group it does not
  // name the turn again (the group's heading does).
  const card = (file: (typeof files)[number], grouped: boolean) => (
    <ReviewFileCard
      key={file.viewKey}
      cardRef={(el) => {
        if (el) cardEls.current.set(file.path, el);
        else cardEls.current.delete(file.path);
      }}
      sessionId={sessionId}
      scope={scope as GitScope}
      version={version}
      file={file}
      turn={grouped ? undefined : agentTurn(file.path)}
      open={isOpen(file)}
      onToggle={() => {
        const next = !isOpen(file);
        setOpened((state) => ({ ...state, [file.viewKey]: next }));
        if (next) setFocused(file.path);
      }}
      viewed={isViewed(viewed, file)}
      onViewed={(on) => setViewedAndMove(file, on)}
      current={file.path === current}
      busy={busy}
      showStage={working && scope !== "staged" && file.staged !== "all"}
      showUnstage={working && scope !== "unstaged" && file.staged === "all"}
      showRevert={working}
      onStage={() => void run(() => scmStage(sessionId, [file.path]))}
      onUnstage={() => void run(() => scmUnstage(sessionId, [file.path]))}
      onRevert={() => setConfirm({ paths: [file.path], label: file.path.split("/").pop() || file.path })}
      hunkMode={readOnly ? "none" : hunkModeFor(file)}
      onHunk={(action, at, header) => {
        if (action === "revert") {
          setConfirm({
            label: `this part of ${file.path.split("/").pop() || file.path}`,
            hunk: { path: file.path, index: at, header },
          });
          return;
        }
        void run(() => scmHunk(sessionId, { path: file.path, index: at, header, action }));
      }}
      onOpenFile={() => onOpenFile(file.path)}
      diffStyle={diffStyle}
      expandMode={expandMode}
      comments={commentList.filter((c) => c.file === file.path)}
      noteCount={commentCounts[file.path]}
      onAddComment={onAddComment}
      onDeleteComment={onDeleteComment}
      onEditComment={onEditComment}
      findings={findings.filter((f) => f.path === file.path)}
      onFixFinding={(f) => {
        // "Fix this" is the person's ask: it becomes their comment on that line,
        // sent with their next message like any other.
        onAddComment?.({
          id: `rc-${f.id}`,
          file: f.path,
          line: f.line,
          text: `Fix this (Reeve's review): ${f.body}`,
          origin: "review",
        });
        dropFinding(f.id);
      }}
      onDismissFinding={(f) => dropFinding(f.id)}
    />
  );

  // One request's group: the person's words, what it changed and how it was checked, the two
  // things to do with it, then its files.
  const askBlock = (ask: AskGroup) => {
    const first = ask.text.split("\n")[0].trim();
    return (
      <section key={ask.turn} className="rvw-ask" data-testid={`rvw-ask-${ask.turn}`}>
        <div className="rvw-ask-head">
          <span className="rvw-ask-mark" aria-hidden>
            <Icon name="chat" size={13} />
          </span>
          <div className="rvw-ask-text">
            <div className="rvw-ask-q" title={ask.text}>
              {first ? `“${first}”` : ask.turn > 0 ? `Turn ${ask.turn}` : "Before your first message"}
            </div>
            <div className="rvw-ask-meta">
              {ask.turn > 0 && <span>Turn {ask.turn}</span>}
              <span>
                {ask.files.length} file{ask.files.length === 1 ? "" : "s"}
              </span>
              {(ask.add > 0 || ask.del > 0) && (
                <span className="rvw-bar-totals">
                  {ask.add > 0 && <span className="add">+{ask.add}</span>}
                  {ask.add > 0 && ask.del > 0 && " "}
                  {ask.del > 0 && <span className="del">−{ask.del}</span>}
                </span>
              )}
              {ask.check ? <CheckMark check={ask.check} /> : <span className="rvw-unchecked">not checked</span>}
            </div>
          </div>
          <span className="rvw-ask-acts">
            {first && (
              <button
                type="button"
                className="rvw-btn ghost-static"
                data-testid={`rvw-ask-change-${ask.turn}`}
                title="Say in the conversation what to change about this"
                onClick={() => askForChange(ask.text)}
              >
                <Icon name="chat" size={13} />
                Ask for a change
              </button>
            )}
            {ask.undoable &&
              (ask.blockedBy ? (
                <span className="rvw-ask-blocked" title={`A file was changed again in Turn ${ask.blockedBy}: undo that first`}>
                  Undo Turn {ask.blockedBy} first
                </span>
              ) : (
                <button
                  type="button"
                  className="rvw-btn ghost-static"
                  data-testid={`rvw-ask-undo-${ask.turn}`}
                  disabled={busy}
                  onClick={() => setConfirm({ ask, label: first ? `what “${first}” changed` : `Turn ${ask.turn}` })}
                >
                  <Icon name="undo" size={13} />
                  Undo
                </button>
              ))}
          </span>
        </div>
        <div className="rvw-ask-files">{ask.files.map((file) => card(file, true))}</div>
      </section>
    );
  };

  const stack = (
    <div className="rvw-stack" data-testid="rvw-cards">
      {byAsk ? (
        <>
          {byAsk.asks.map(askBlock)}
          {byAsk.outside.length > 0 && (
            <section className="rvw-ask is-outside" data-testid="rvw-outside">
              <div className="rvw-ask-head">
                <span className="rvw-ask-mark" aria-hidden>
                  <Icon name="folder" size={13} />
                </span>
                <div className="rvw-ask-text">
                  <div className="rvw-ask-q">Not from this conversation</div>
                  <div className="rvw-ask-meta">
                    <span>Changed in the folder by you, a command or another program</span>
                  </div>
                </div>
              </div>
              <div className="rvw-ask-files">{byAsk.outside.map((file) => card(file, true))}</div>
            </section>
          )}
        </>
      ) : (
        files.map((file) => card(file, false))
      )}
    </div>
  );

  return (
    <div className="rvw" ref={rootRef} data-testid="review-pane" data-scope={scope} onKeyDown={onKeyDown}>
      <div className="rvw-bar">
        <ReviewScopeMenu
          scope={scope}
          isGit={isGit}
          counts={counts}
          branch={log?.branch}
          ahead={log?.ahead}
          base={scopeData?.base ?? log?.base}
          commits={log?.commits ?? []}
          hasMoreCommits={!!log?.more}
          onMoreCommits={() => setLogLimit((n) => n + LOG_PAGE)}
          onPick={(next) => {
            setPicked(next);
            setError(null);
          }}
          workspacePath={isGit ? workspacePath : undefined}
          onBranchSwitched={(name) => {
            setNotice({ text: `Switched to ${name}` });
            onRefresh();
          }}
        />
        {viaGit && !loading ? (
          <>
            <span className="rvw-bar-count" data-testid="rvw-count">
              {files.length} file{files.length === 1 ? "" : "s"}
            </span>
            {(totals.add > 0 || totals.del > 0) && (
              <span className="rvw-bar-totals">
                {totals.add > 0 && <span className="add">+{totals.add}</span>}
                {totals.add > 0 && totals.del > 0 && " "}
                {totals.del > 0 && <span className="del">−{totals.del}</span>}
              </span>
            )}
          </>
        ) : null}
        <span className="rvw-bar-fill" />
        {viaGit && files.length > 0 && (
          <button
            type="button"
            className="rvw-btn"
            data-testid="rvw-review-code"
            disabled={reviewing}
            aria-busy={reviewing}
            title="Reeve reads this diff and raises what is worth a look (one model call)"
            onClick={() => void runReview()}
          >
            <Icon name="sparkle" size={13} running={reviewing} />
            {reviewing ? "Reviewing…" : "Review"}
          </button>
        )}
        {viaGit && (
          <PopoverShell
            open={optionsOpen}
            onClose={() => setOptionsOpen(false)}
            align="right"
            trigger={
              <button
                type="button"
                className="rvw-icon"
                aria-label="More"
                aria-haspopup="menu"
                aria-expanded={optionsOpen}
                data-testid="rvw-options"
                onClick={() => setOptionsOpen((v) => !v)}
              >
                <Icon name="moreHorizontal" size={15} />
              </button>
            }
          >
            <div className="rvw-options" role="group" aria-label="View options">
              <div className="rvw-options-title">Show</div>
              <div className="seg seg-words" role="group" aria-label="Diff view">
                <button className={diffStyle === "unified" ? "on" : ""} onClick={() => onDiffStyle("unified")} title="One column">
                  Unified
                </button>
                <button className={diffStyle === "split" ? "on" : ""} onClick={() => onDiffStyle("split")} title="Old and new side by side">
                  Split
                </button>
              </div>
              <div className="seg seg-words" role="group" aria-label="Line display">
                <button className={expandMode === "expand" ? "on" : ""} onClick={() => setExpandMode("expand")} title="Show the lines around each change">
                  With context
                </button>
                <button className={expandMode === "collapse" ? "on" : ""} onClick={() => setExpandMode("collapse")} title="Show only the changed lines">
                  Changes only
                </button>
              </div>
              <div className="rvw-menu-sep" />
              {working && files.length > 0 && scope !== "staged" && stageable.length > 0 && (
                <button
                  type="button"
                  className="rvw-menu-item"
                  disabled={busy}
                  data-testid="rvw-stage-all"
                  onClick={() => bulk(() => void run(() => scmStage(sessionId, stageable.map((f) => f.path))))}
                >
                  <Icon name="plus" size={13} />
                  Stage all {stageable.length} file{stageable.length === 1 ? "" : "s"}
                </button>
              )}
              {working && files.length > 0 && scope !== "unstaged" && unstageable.length > 0 && (
                <button
                  type="button"
                  className="rvw-menu-item"
                  disabled={busy}
                  data-testid="rvw-unstage-all"
                  onClick={() => bulk(() => void run(() => scmUnstage(sessionId, unstageable.map((f) => f.path))))}
                >
                  <MinusIcon />
                  Unstage all {unstageable.length} file{unstageable.length === 1 ? "" : "s"}
                </button>
              )}
              <button type="button" className="rvw-menu-item" data-testid="rvw-refresh" onClick={() => bulk(onRefresh)}>
                <Icon name="refresh" size={13} />
                Refresh
              </button>
              {!!log?.upstream && (
                <button
                  type="button"
                  className="rvw-menu-item"
                  data-testid="rvw-pull-menu"
                  disabled={pulling}
                  onClick={() => bulk(() => void pull())}
                >
                  <Icon name="arrowDownToLine" size={13} />
                  Pull from {log.upstream}
                </button>
              )}
              {onFeatureBranch && prState?.available && !prState.pr && (
                <button type="button" className="rvw-menu-item" data-testid="rvw-open-pr" onClick={() => bulk(() => setPrDialog(true))}>
                  <Icon name="gitPullRequest" size={13} />
                  Open a pull request…
                </button>
              )}
              {onFeatureBranch && prState && !prState.available && prState.reason && (
                <div className="rvw-menu-note" data-testid="rvw-pr-unavailable">
                  Pull requests: {prState.reason}
                </div>
              )}
              {working && files.length > 0 && (
                <>
                  <div className="rvw-menu-sep" />
                  <button
                    type="button"
                    className="rvw-menu-item danger"
                    disabled={busy}
                    data-testid="rvw-revert-all"
                    onClick={() =>
                      bulk(() =>
                        setConfirm({
                          paths: files.map((f) => f.path),
                          label: `${files.length} file${files.length === 1 ? "" : "s"}`,
                        }),
                      )
                    }
                  >
                    <Icon name="undo" size={13} />
                    Revert all {files.length} file{files.length === 1 ? "" : "s"}…
                  </button>
                </>
              )}
            </div>
          </PopoverShell>
        )}
        {!viaGit && (
          <button type="button" className="rvw-icon" aria-label="Refresh" title="Refresh" onClick={onRefresh}>
            <Icon name="refresh" size={14} />
          </button>
        )}
      </div>

      {showSync && log && (
        <div className="rvw-sync" data-testid="rvw-sync">
          <span className="rvw-sync-branch">{log.branch}</span>
          {!!log.ahead && (
            <span>
              · {log.ahead} commit{log.ahead === 1 ? "" : "s"} not pushed
            </span>
          )}
          {showPull && <span>· {log.behind} to pull</span>}
          {showPull && (
            <button
              type="button"
              className="rvw-btn"
              data-testid="rvw-pull"
              disabled={pulling}
              title={`Bring ${log.branch ?? "the branch"} up to date with ${log.upstream}`}
              onClick={() => void pull()}
            >
              {pulling ? "Pulling…" : "Pull"}
            </button>
          )}
          {showPush && (
            <button
              type="button"
              className="rvw-btn"
              data-testid="rvw-push"
              disabled={pushing}
              title={`Push ${log.branch ?? "the branch"} to ${log.remote}`}
              onClick={() => void push()}
            >
              <svg width="12" height="12" viewBox="0 0 16 16" fill="none" aria-hidden="true">
                <path d="M8 13V3M4 7l4-4 4 4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
              {pushing ? "Pushing…" : "Push"}
            </button>
          )}
          {prState?.pr && <PullRequestBadge pr={prState.pr} />}
        </div>
      )}

      {commit && (
        <div className="rvw-scope-note" data-testid="rvw-scope-note">
          <b>{commit.subject}</b>
          <span>
            {commit.short} · {commit.author} · {commit.when}
          </span>
        </div>
      )}
      {scope === "branch" && scopeData?.base && files.length > 0 && (
        <div className="rvw-scope-note" data-testid="rvw-scope-note">
          <span>
            What this branch adds to <b>{scopeData.base}</b>, from where they diverged. Uncommitted work is not in it.
          </span>
        </div>
      )}

      {/* Whether the work on show was checked after its last edit, and how much of it was read:
          said in words, with no bar (owner, 2026-10-12). */}
      {viaGit && ordered.length > 0 && (
        <div className="rvw-checkline" data-testid="rvw-checkline">
          {byAsk && byAsk.asks.length > 0 ? (
            sessionFacts.check ? (
              <>
                <CheckMark check={sessionFacts.check} />
                <span className="faint">after the last edit</span>
              </>
            ) : (
              <span className="rvw-unchecked" data-testid="rvw-unchecked">
                <Icon name="warning" size={12} />
                Not checked since the last edit
              </span>
            )
          ) : null}
          <span className="rvw-bar-fill" />
          <span className="faint" data-testid="rvw-progress">
            Viewed {viewedCount} of {ordered.length}
          </span>
        </div>
      )}

      {error && (
        <div className="rvw-error scm-error" role="alert" data-testid="scm-error">
          {error}
        </div>
      )}

      <div className={"rvw-main" + (viaGit ? " stacked" : "")}>
        {viaGit ? (
          files.length === 0 ? (
            loading ? (
              <div className="rev-empty-small rvw-loading" data-testid="rvw-loading">
                Loading…
              </div>
            ) : (
              <div className="rev-empty" data-testid="rvw-clean">
                <Icon name="check" size={22} />
                <div className="rev-empty-title">{emptyText().title}</div>
                <div className="rev-empty-desc">
                  {emptyText().desc}
                  {scope === "uncommitted" && recorded.length > 0 && (
                    <>
                      {" "}
                      What Reeve changed earlier is in{" "}
                      <button type="button" className="rev-link" onClick={() => setPicked("session")}>
                        This session
                      </button>
                      .
                    </>
                  )}
                </div>
                {scope === "uncommitted" && (cleanPush || lastCommit) && (
                  <div className="rev-empty-actions">
                    {cleanPush && (
                      <button
                        type="button"
                        className="rvw-primary"
                        data-testid="rvw-clean-push"
                        disabled={pushing}
                        onClick={() => void push()}
                      >
                        {pushing ? "Pushing…" : `Push ${log!.ahead} commit${log!.ahead === 1 ? "" : "s"}`}
                      </button>
                    )}
                    {lastCommit && (
                      <button
                        type="button"
                        className="rvw-btn"
                        data-testid="rvw-clean-last"
                        onClick={() => setPicked(`commit:${lastCommit.hash}`)}
                      >
                        See the last commit
                      </button>
                    )}
                  </div>
                )}
              </div>
            )
          ) : (
            stack
          )
        ) : (
          <div className="rvw-recorded">
            <ReviewRecorded
              key={`${scope}:${focusKey ?? 0}`}
              sessionId={sessionId}
              changes={scope === "last-turn" ? lastTurn : recorded}
              roots={roots}
              onChanged={onRefresh}
              focusPath={focusPath}
              onShowUncommitted={isGit ? () => setPicked("uncommitted") : undefined}
            />
          </div>
        )}
      </div>

      {/* The commit box at the foot, the whole width: what the review ends with. */}
      {viaGit && files.length > 0 && commitDock && <div className="rvw-foot">{commitDock}</div>}

      {showCommentBar && commentList.length > 0 && (
        <div className="rev-comment-bar" data-testid="review-comment-bar">
          <Icon name="chat" size={14} />
          <span className="rvw-comment-text">
            <b>
              {commentList.length} comment{commentList.length === 1 ? "" : "s"}
            </b>{" "}
            on the diff, not sent yet
          </span>
          <button
            type="button"
            className="rvw-btn"
            data-testid="review-comments-clear"
            onClick={() => commentList.forEach((c) => onDeleteComment?.(c.id))}
          >
            Clear
          </button>
          {onSendComments && (
            <button type="button" className="rvw-primary" data-testid="review-comments-send" onClick={onSendComments}>
              Send to Reeve
            </button>
          )}
        </div>
      )}

      {notice && (
        <div className="rvw-toast" role="status" data-testid="rvw-toast">
          <Icon name="check" size={14} />
          <span>{notice.text}</span>
        </div>
      )}

      {prDialog && (
        <CreatePullRequestDialog
          sessionId={sessionId}
          branch={log?.branch}
          base={log?.base}
          defaultTitle={log?.commits[0]?.subject ?? ""}
          onClose={() => setPrDialog(false)}
          onDone={(url) => {
            setPrDialog(false);
            setNotice({ text: "Pull request opened" });
            if (url) openExternal(url);
            onRefresh();
          }}
        />
      )}

      {confirm && (
        <div className="fm-dialog-backdrop" onClick={() => !busy && setConfirm(null)}>
          <div
            className="fm-dialog"
            role="alertdialog"
            aria-modal="true"
            aria-labelledby="scm-discard-title"
            data-testid="scm-discard-dialog"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="fm-dialog-title" id="scm-discard-title">
              {confirm.ask ? `Undo ${confirm.label}?` : `Revert ${confirm.hunk ? "" : "changes to "}${confirm.label}?`}
            </div>
            <div className="fm-dialog-hint">
              {confirm.ask
                ? `${confirm.ask.files.length === 1 ? "Its file goes" : "Its files go"} back to how ${confirm.ask.files.length === 1 ? "it was" : "they were"} before Reeve changed ${confirm.ask.files.length === 1 ? "it" : "them"} for this request. Edits made to ${confirm.ask.files.length === 1 ? "it" : "them"} since are lost.`
                : confirm.hunk
                  ? "These lines go back to what the file had before. This cannot be undone."
                  : `The file${confirm.paths && confirm.paths.length === 1 ? " goes" : "s go"} back to the last commit; a new file is deleted. This cannot be undone.`}
            </div>
            <div className="fm-dialog-actions">
              <button type="button" className="btn sm" onClick={() => setConfirm(null)} disabled={busy}>
                Cancel
              </button>
              <button
                type="button"
                className="btn sm danger-btn"
                data-testid="scm-discard-confirm"
                disabled={busy}
                onClick={() => {
                  const target = confirm;
                  void run(
                    () =>
                      target.ask
                        ? undoAsk(target.ask)
                        : target.hunk
                          ? scmHunk(sessionId, { ...target.hunk, action: "revert" })
                          : scmDiscard(sessionId, target.paths ?? []),
                    () => setConfirm(null),
                  );
                }}
              >
                {confirm.ask ? "Undo" : "Revert"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
