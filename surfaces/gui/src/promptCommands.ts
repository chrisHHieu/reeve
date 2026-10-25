// Prompt-row templates — the local answer to the server-side commands (`/init`,
// `/review`). The server resolves those: the
// popup INSERTs `/name ` into the draft
// and the submit path hands the command + arguments to `session.command`, which expands the
// template with the SAME substitution rules mirrored here:
//
//   · `$1 … $n` are positional; the HIGHEST placeholder present takes the whole remaining
//     tail, every other position takes one argument;
//   · a missing position expands to "";
//   · `$ARGUMENTS` takes the raw argument string;
//   · with neither placeholder form, a non-empty argument tail is appended as a new
//     paragraph;
//   · the result is trimmed.
//
// `/review` is a `review.txt`, adapted to this app (our tool names instead of
// The reference design's). `/init` is Reeve's own (2026-10-06), written from what has been measured about
// instruction files: a generated file that describes the repository lowers the success rate
// and raises cost by a fifth (arXiv 2602.11988), an overview of the layout does not help,
// and what an agent is told it does follow, so a wrong command in the file is run again and
// again. Hence a short file, commands written only once they ran, no tour of the tree, and
// guidance for one directory placed where it loads only there (`.agents/rules`, a nested
// `AGENTS.md`). The tool names it uses are checked against the real registry by
// `tests/test_prompt_commands.py`: the first version told the model to use a tool `ask`
// that this app does not have. No bundled skills are involved. One parser, one registry, one
// prefix test: the send path asks whether a draft IS a prompt command before it is allowed
// to become a message.

export interface PromptCommand {
  name: string;
  description: string;
  template: string;
}

const INIT_TEMPLATE = [
  "Write or update `AGENTS.md` for this repository: the instructions every future session here starts with.",
  "",
  "It is read in full at the start of every session, so each line costs something every time. A line earns its place only if an agent working here would get something wrong without it. Aim for under 60 lines. Past 150, cut.",
  "",
  "What the person asked for (this comes first):",
  "$ARGUMENTS",
  "",
  "## Find out",
  "",
  "Read what decides how work is done here, most telling first: the manifests and lockfiles; the build, test, lint and typecheck configuration; the CI workflows; any task runner; and the instruction files already present (`AGENTS.md`, `CLAUDE.md`, `.agents/rules/`, `.claude/rules/`, `.cursor/rules/`, `.github/copilot-instructions.md`). Where prose and configuration disagree, the configuration is right.",
  "",
  "Do not survey the source tree. An agent lists directories and reads files when it needs them, and a description of the layout is the part of these files that has been measured not to help.",
  "",
  "## Prove the commands",
  "",
  "For each command you mean to write down (build, test, one test, lint, typecheck, format), run it with `run_shell` and write it only if it ran. Write the exact command that worked, with the directory it worked in.",
  "",
  "- Prefer the narrow form: one test file, one package. It is what an agent runs most.",
  "- Do not run anything that deploys, publishes, migrates, deletes, or needs credentials. Leave it out, or list it under \"Not run here\" when the person will need it.",
  "- A command that failed is not written. When it failed for a reason worth knowing (a service has to be up, a variable has to be set), that reason is a gotcha once you have confirmed it.",
  "",
  "## What goes in",
  "",
  "1. One short paragraph: what this repository is for.",
  "2. Commands: the ones you proved.",
  "3. Gotchas: things that are true here and that an agent would not guess. A generated file that must not be edited by hand, a required order of steps, a convention that differs from the language's default, a suite that is slow or needs a service, a directory that looks editable and is not. Confirm each in the repository before you write it.",
  "4. Rules the person, or an instruction file already here, states and that still hold.",
  "",
  "## What stays out",
  "",
  "- anything an agent finds by reading the code: the layout, the list of modules, what a file contains",
  "- advice true of any project",
  "- how a language or a framework works",
  "- anything you could not confirm",
  "",
  "## Put it where it applies",
  "",
  "`AGENTS.md` at the root is for what holds everywhere. Guidance for one part of the repository goes where it is loaded only when work happens there:",
  "",
  "- a rule for some paths: a file `.agents/rules/<name>.md` whose first lines are `---`, `paths:`, one `  - <glob>` line per pattern (for example `  - src/api/**`), and `---`",
  "- conventions for one package: an `AGENTS.md` in that package's own directory",
  "",
  "## If a file is already there",
  "",
  "Improve it in place. Keep what you can confirm, correct what the repository contradicts, and cut what an agent would get right without being told. Two lines that contradict each other are worse than neither: keep the one the repository supports.",
  "",
  "## Ask only what the repository cannot answer",
  "",
  "One short batch with `ask_user`, at most: a team convention nobody wrote down, branch and release rules, a prerequisite that is known but not in the repository. Skip it when nothing is missing.",
  "",
  "## When you are done",
  "",
  "Say how many lines the file has, which commands you ran and proved, and what you left out on purpose.",
].join("\n");

const REVIEW_TEMPLATE = [
  "You are a code reviewer. Your job is to review code changes and provide actionable feedback.",
  "",
  "---",
  "",
  "Input: $ARGUMENTS",
  "",
  "---",
  "",
  "## Determining What to Review",
  "",
  "Based on the input provided, determine which type of review to perform:",
  "",
  "1. **No arguments (default)**: Review all uncommitted changes",
  "   - Run: `git diff` for unstaged changes",
  "   - Run: `git diff --cached` for staged changes",
  "   - Run: `git status --short` to identify untracked (net new) files",
  "",
  "2. **Commit hash** (40-char SHA or short hash): Review that specific commit",
  "   - Run: `git show $ARGUMENTS`",
  "",
  "3. **Branch name**: Compare current branch to the specified branch",
  "   - Run: `git diff $ARGUMENTS...HEAD`",
  "",
  '4. **PR URL or number** (contains "github.com" or "pull" or looks like a PR number): Review the pull request',
  "   - Run: `gh pr view $ARGUMENTS` to get PR context",
  "   - Run: `gh pr diff $ARGUMENTS` to get the diff",
  "",
  "Use the `run_shell` tool for the commands above, and `git log` through it for commit history.",
  "Use best judgement when processing input.",
  "",
  "---",
  "",
  "## Gathering Context",
  "",
  "**Diffs alone are not enough.** After getting the diff, read the entire file(s) being modified to understand the full context. Code that looks wrong in isolation may be correct given surrounding logic—and vice versa.",
  "",
  "- Use the diff to identify which files changed",
  "- Use `git status --short` to identify untracked files, then read their full contents",
  "- Use `read_file` to read the full file and understand existing patterns, control flow, and error handling",
  "- Use `grep` to check how similar code is handled elsewhere in the repo",
  "- Check for existing style guide or conventions files (CONVENTIONS.md, AGENTS.md, .editorconfig, etc.)",
  "",
  "---",
  "",
  "## What to Look For",
  "",
  "**Bugs** - Your primary focus.",
  "- Logic errors, off-by-one mistakes, incorrect conditionals",
  "- If-else guards: missing guards, incorrect branching, unreachable code paths",
  "- Edge cases: null/empty/undefined inputs, error conditions, race conditions",
  "- Security issues: injection, auth bypass, data exposure",
  "- Broken error handling that swallows failures, throws unexpectedly or returns error types that are not caught.",
  "",
  "**Structure** - Does the code fit the codebase?",
  "- Does it follow existing patterns and conventions?",
  "- Are there established abstractions it should use but doesn't?",
  "- Excessive nesting that could be flattened with early returns or extraction",
  "",
  "**Performance** - Only flag if obviously problematic.",
  "- O(n²) on unbounded data, N+1 queries, blocking I/O on hot paths",
  "",
  "**Behavior Changes** - If a behavioral change is introduced, raise it (especially if it's possibly unintentional).",
  "",
  "---",
  "",
  "## Before You Flag Something",
  "",
  "**Be certain.** If you're going to call something a bug, you need to be confident it actually is one.",
  "",
  "- Only review the changes - do not review pre-existing code that wasn't modified",
  "- Don't flag something as a bug if you're unsure - investigate first",
  "- Don't invent hypothetical problems - if an edge case matters, explain the realistic scenario where it breaks",
  "- If you need more context to be sure, use the tools below to get it",
  "",
  "**Don't be a zealot about style.** When checking code against conventions:",
  "",
  "- Verify the code is *actually* in violation. Don't complain about else statements if early returns are already being used correctly.",
  '- Some "violations" are acceptable when they\'re the simplest option. A `let` statement is fine if the alternative is convoluted.',
  "- Excessive nesting is a legitimate concern regardless of other style choices.",
  "- Don't flag style preferences as issues unless they clearly violate established project conventions.",
  "",
  "---",
  "",
  "## Tools",
  "",
  "Use these to inform your review:",
  "",
  "- **task subagents** - Spawn a `task` when you need how existing code handles similar problems; check patterns, conventions, and prior art before claiming something doesn't fit.",
  "- **web_search** - Verify correct usage of libraries/APIs before flagging something as wrong, or research best practices if you're unsure about a pattern.",
  "",
  'If you\'re uncertain about something and can\'t verify it with these tools, say "I\'m not sure about X" rather than flagging it as a definite issue.',
  "",
  "---",
  "",
  "## Output",
  "",
  "1. If there is a bug, be direct and clear about why it is a bug.",
  "2. Clearly communicate severity of issues. Do not overstate severity.",
  "3. Critiques should clearly and explicitly communicate the scenarios, environments, or inputs that are necessary for the bug to arise. The comment should immediately indicate that the issue's severity depends on these factors.",
  "4. Your tone should be matter-of-fact and not accusatory or overly positive. It should read as a helpful AI assistant suggestion without sounding too much like a human reviewer.",
  "5. Write so the reader can quickly understand the issue without reading too closely.",
  '6. AVOID flattery, do not give any comments that are not helpful to the reader. Avoid phrasing like "Great job ...", "Thanks for ...".',
].join("\n");

export const PROMPT_COMMANDS: PromptCommand[] = [
  { name: "init", description: "write a short AGENTS.md from commands that ran", template: INIT_TEMPLATE },
  {
    name: "review",
    description: "review changes [commit|branch|pr], defaults to uncommitted",
    template: REVIEW_TEMPLATE,
  },
];

// The argument grammar (): quoted strings group, `[Image N]`
// stays one token, everything else splits on whitespace.
const ARGS_RE = /(?:\[Image\s+\d+\]|"[^"]*"|'[^']*'|[^\s"']+)/gi;
const QUOTE_TRIM_RE = /^["']|["']$/g;
const PLACEHOLDER_RE = /\$(\d+)/g;

/** One argument token per quote-aware word, quotes stripped. */
export function parsePromptArgs(input: string): string[] {
  return (input.match(ARGS_RE) ?? []).map((arg) => arg.replace(QUOTE_TRIM_RE, ""));
}

/** Expand a prompt-command template with the exact substitution semantics. */
export function resolvePromptTemplate(template: string, args: string): string {
  const parsed = parsePromptArgs(args);
  const placeholders = template.match(PLACEHOLDER_RE) ?? [];
  let last = 0;
  for (const item of placeholders) {
    const value = Number(item.slice(1));
    if (value > last) last = value;
  }

  const withArgs = template.replace(PLACEHOLDER_RE, (_match, index: string) => {
    const position = Number(index);
    const argIndex = position - 1;
    if (argIndex >= parsed.length) return "";
    if (position === last) return parsed.slice(argIndex).join(" ");
    return parsed[argIndex];
  });
  const usesArgumentsPlaceholder = template.includes("$ARGUMENTS");
  // The `replaceAll("$ARGUMENTS", arguments)` (); the regex form is
  // the same substitution for our TS lib target (both expand `$&` in the inserted args).
  let resolved = withArgs.replace(/\$ARGUMENTS/g, args);

  if (placeholders.length === 0 && !usesArgumentsPlaceholder && args.trim()) {
    resolved = resolved + "\n\n" + args;
  }
  return resolved.trim();
}

/** The prompt command whose name matches, case-insensitively. */
export function promptCommandFor(name: string): PromptCommand | undefined {
  const lower = name.toLowerCase();
  return PROMPT_COMMANDS.find((command) => command.name === lower);
}

/** The command + raw arguments a draft carries when it IS `/init …` / `/review …`; null when
 *  it is a plain message. Matches only the first token. */
export function promptCommandInput(
  text: string,
): { command: PromptCommand; args: string } | null {
  const trimmed = text.trim();
  const match = /^\/(\S+)(?:\s+([\s\S]*))?$/.exec(trimmed);
  if (!match) return null;
  const command = promptCommandFor(match[1]);
  if (!command) return null;
  return { command, args: (match[2] ?? "").trim() };
}
