import { PRIMARY_ROOT } from "../data";
import { inSession, type On } from "../router";
import type { MockState } from "../state";

// The repository Review shows: its changes by scope, diffs, history, branches,
// pull and push, the pull request, and staging by file or by hunk. The folder is a repository
// only once a scenario makes it one (`scmState.git`).

const UPLOAD_CLIENT = { path: "upload_client.py", status: "A", add: 42, del: 0 };
const fromOrigin = (branch: unknown) => String(branch || "").replace(/^origin\//, "");

export function gitRoutes(on: On, s: MockState) {
  const scm = s.scmState;
  const onMain = () => scm.branch === "main";

  // One repository seen from several sides: the index, the edits, the branch against main, one commit.
  on("*", inSession("changes"), ({ query }) => {
    const scope = query.get("scope") || "uncommitted";
    const head = { is_git: scm.git, workspace: scm.git ? PRIMARY_ROOT.path : null, scope };
    if (scope === "staged") return { ...head, files: scm.files.filter((f) => f.staged) };
    if (scope === "unstaged") return { ...head, files: scm.files.filter((f) => !f.staged).map((f) => ({ ...f, staged: false })) };
    if (scope === "branch") return { ...head, base: "main", files: [UPLOAD_CLIENT] };
    if (scope.startsWith("commit:")) return { ...head, files: [UPLOAD_CLIENT, { path: "README.md", status: "M", add: 2, del: 1 }] };
    return { is_git: scm.git, files: scm.files, workspace: head.workspace };
  });
  // settings.toml changes in two places far apart, so its diff says how many lines lie between.
  on("*", inSession("changes/diff"), ({ query }) => {
    const path = query.get("path") || "";
    const patch = path === "settings.toml"
      ? "@@ -3,2 +3,2 @@\n retries = 1\n-timeout = 5\n+timeout = 30\n@@ -40,2 +40,3 @@\n [upload]\n+backoff = 2\n retries_max = 4"
      : "@@ -1 +1,2 @@\n hello from the explorer\n+retry uploads up to three times";
    return { ok: true, path, patch, untracked: false };
  });
  on("*", inSession("scm/log"), () => scm.git
    ? {
        is_git: true, branch: scm.branch, ahead: scm.ahead, behind: onMain() ? scm.behind : 0,
        upstream: onMain() ? "origin/main" : null, base: "main", remote: "origin", more: false, commits: scm.commits,
      }
    : { is_git: false, commits: [] });

  // The branch picker, Pull and the branch's pull request, with gh played here.
  on("*", "/v1/workspace-branch", () => ({ branch: scm.git ? scm.branch : null }));
  on("*", "/v1/workspaces/branches", () => ({
    is_git: true, root: PRIMARY_ROOT.path, current: scm.branch, dirty: scm.files.length > 0,
    local: scm.branches.map((name) => {
      const main = name === "main";
      return {
        name, current: name === scm.branch, upstream: main ? "origin/main" : null,
        ahead: main ? scm.ahead : 0, behind: main ? scm.behind : 0, gone: false,
        when: main ? "2 hours ago" : "3 days ago", subject: main ? "Add the upload client" : "Statements page",
      };
    }),
    remote: [{ name: "origin/release", branch: "release" }],
  }));
  on("POST", "/v1/workspaces/switch-branch", ({ body }) => {
    const name = fromOrigin(body.branch);
    if (!scm.branches.includes(name)) scm.branches.unshift(name);
    scm.branch = name;
    return { ok: true, branch: name };
  });
  on("POST", "/v1/workspaces/worktree", ({ body }) => {
    const name = fromOrigin(body.branch);
    return { ok: true, branch: name, path: `${PRIMARY_ROOT.path}-${name.replace(/[^A-Za-z0-9._-]+/g, "-")}` };
  });
  on("POST", inSession("scm/pull"), () => {
    scm.behind = 0;
    return { ok: true };
  });
  on("POST", inSession("scm/pr"), ({ body }) => {
    scm.pr = {
      number: 7, title: String(body.title || ""), url: "https://github.com/test/launch-note/pull/7", state: "OPEN",
      draft: !!body.draft, head: scm.branch, base: String(body.base || "main"), review: "REVIEW_REQUIRED",
      checks: {
        passed: 1, failed: 0, running: 1, verdict: "running",
        items: [
          { name: "lint", state: "passed", url: "https://github.com/test/launch-note/actions/runs/1" },
          { name: "tests", state: "running", url: null },
        ],
      },
    };
    return { ok: true, url: scm.pr.url };
  });
  on("*", inSession("scm/pr"), () => ({ available: true, pr: onMain() ? null : scm.pr }));
  // Pushing moves origin/main to the newest commit.
  on("POST", inSession("scm/push"), () => {
    scm.ahead = 0;
    scm.commits.forEach((c, i) => (c.refs = i === 0 ? ["HEAD -> main", "origin/main"] : c.refs.filter((r) => r !== "origin/main")));
    return { ok: true, remote: "origin", branch: "main" };
  });
  on("POST", inSession("scm/suggest-message"), () => ({ ok: true, message: "Retry uploads up to three times\n\nThe client gave up on the first 503." }));
  on("POST", inSession("scm/review"), () => ({
    ok: true, truncated: false,
    findings: [{ path: "notes.md", line: 2, severity: "risk", body: "Three tries with no wait between them will hit the same outage three times: back off." }],
  }));

  const mark = (paths: (path: string) => boolean, staged: boolean) => scm.files.forEach((f) => paths(f.path) && (f.staged = staged));
  const drop = (paths: (path: string) => boolean) => (scm.files = scm.files.filter((f) => !paths(f.path)));
  // Every file here has one hunk, so a hunk's action acts on its whole file.
  on("POST", inSession("scm/hunk"), ({ body }) => {
    scm.hunks.push({ path: body.path, index: body.index, header: body.header, action: body.action });
    const same = (path: string) => path === body.path;
    if (body.action === "stage") mark(same, true);
    if (body.action === "unstage") mark(same, false);
    if (body.action === "revert") drop(same);
    return { ok: true };
  });
  on("POST", inSession("scm/(stage|unstage|discard|commit)"), ({ match, body }) => {
    const listed: string[] = Array.isArray(body.paths) ? body.paths : [];
    const named = (path: string) => listed.includes(path);
    const verb = match[1];
    if (verb === "stage") mark(named, true);
    if (verb === "unstage") mark(named, false);
    if (verb === "discard") drop(named);
    if (verb !== "commit") return { ok: true };
    if (!String(body.message || "").trim()) return { ok: false, error: "a commit needs a message" };
    scm.files = body.all ? [] : scm.files.filter((f) => !f.staged);
    scm.ahead += 1;
    scm.commits.unshift({ hash: "c3".padEnd(40, "0"), short: "d41f7c2", author: "Tester", when: "just now", subject: String(body.message), refs: ["HEAD -> main"] });
    scm.commits.slice(1).forEach((c) => (c.refs = c.refs.filter((r) => !r.startsWith("HEAD"))));
    return { ok: true, commit: "d41f7c2" };
  });
}
