// A tool call as one plain sentence. The model gives no purpose with a call (the
// stream carries the name, the arguments and the result), so the sentence is made here from a
// template per tool. `run_shell` is the one exception: its optional `description` is the model's own
// account of why, and is used when it is there. A tool with no template reads "Used <tool> — <args>".

import { shortArgs } from "./components/ApprovalCard";

// The sentence in three parts, so the object can be set apart: "Read " + <b>runbook.md</b> + " from
// the shared folder".
export interface HumanLine {
  pre: string;
  obj?: string;
  post?: string;
}

// What the tool said of this call itself: worked out on the server by its output contract
// (harness/tools/contract.py) and carried on the permission event and the tool-finished event;
// undefined for a tool that declares none. The type lives in cards/types.ts, since it also carries a
// structured `card`, data a surface branches on rather than words a person reads. It is exported from
// here as well because the sentence half is described here, `link` above all: it exists because the
// rest of the system has nothing like it.
export type { Presented } from "./cards/types";
import type { Presented } from "./cards/types";

// Everything a surface knows of the action being decided, in one object. It grew as positional
// arguments ((name, args), then (name, args, category)), and a caller that missed the newest one lost
// it without a sound: the parked Inbox card dropped `category` that way and told people a connector
// call stayed on their computer.
export interface ApprovalSubject {
  name: string;
  args: any;
  category?: string;
  presented?: Presented;
}

type Args = Record<string, any>;

const cut = (text: string, max: number) => (text.length > max ? text.slice(0, max - 1) + "…" : text);
const fileName = (path: string) => path.replace(/\/+$/, "").split("/").pop() || path;
const quoted = (text: unknown, max: number) => `“${cut(String(text ?? ""), max)}”`;
const argsOf = (args: any): Args => (args && typeof args === "object" ? args : {});
const EDIT_TOOLS = new Set(["replace_in_file", "apply_patch", "apply_unified_diff"]);

/** The model's own description of a command, as a clause: " — fetch stock data". */
function described(args: Args): Partial<HumanLine> {
  const text = typeof args.description === "string" ? args.description.trim() : "";
  return text ? { post: ` — ${text[0].toLowerCase()}${text.slice(1)}` } : {};
}

/** A send_message target is "platform:chat" or "platform:chat:thread": the platform by its name, and
 *  the last readable part of the chat. */
function messageTarget(target: string): { platform: string; tail: string } {
  const [platform, chat = ""] = String(target).split(":");
  const names: Record<string, string> = { slack: "Slack", telegram: "Telegram" };
  return { platform: names[platform] || platform, tail: chat.includes("/") ? chat.split("/").pop() || chat : chat };
}

function plan(args: Args): HumanLine {
  // `todos` today; `items` for histories from before the rename (the old key broke Together's
  // GLM-5.2 chat template, harness/tools/todo.py).
  const items: any[] = Array.isArray(args.todos) ? args.todos : Array.isArray(args.items) ? args.items : [];
  if (items.length !== 1) return { pre: `Updated the plan — ${items.length} items` };
  const item = items[0] || {};
  const status = String(item.status || "").replace(/_/g, " ");
  return { pre: "Updated the plan — ", obj: quoted(item.content, 70), ...(status ? { post: ` → ${status}` } : {}) };
}

function webHost(url: unknown): string {
  const raw = String(url ?? "");
  try {
    return new URL(raw).host || raw;
  } catch {
    return raw;
  }
}

const DONE: Record<string, (a: Args) => HumanLine> = {
  run_shell: (a) => ({ pre: a.run_in_background ? "Started in the background: " : "Ran ", obj: cut(String(a.command ?? ""), 60), ...described(a) }),
  shell_task_output: () => ({ pre: "Checked on a background command" }),
  shell_task_kill: () => ({ pre: "Stopped a background command" }),
  read_file: (a) => ({ pre: "Read ", obj: fileName(String(a.path ?? "a file")) }),
  write_file: (a) => ({ pre: "Wrote ", obj: fileName(String(a.path ?? "a file")) }),
  grep: (a) => ({ pre: "Searched the code for ", obj: quoted(a.pattern, 40) }),
  git_log: () => ({ pre: "Looked through recent git history" }),
  todo_write: plan,
  send_message: (a) => {
    const { platform, tail } = messageTarget(String(a.target ?? ""));
    return tail ? { pre: `Sent a ${platform} message to `, obj: tail } : { pre: "Sent a message" };
  },
  web_search: (a) => ({ pre: "Searched the web — ", obj: quoted(a.query, 60) }),
  web_fetch: (a) => ({ pre: "Read a web page — ", obj: cut(webHost(a.url), 50) }),
  // The row's label already says "SubAgent Explore"; what follows is the task alone, without quotes
  // (owner, 2026-09-09, so it matches the row).
  explore: (a) => ({ pre: "", obj: cut(String(a.task ?? a.prompt ?? ""), 70) }),
  // The trust line: the transcript always shows when a skill's instructions
  // were taken up, whether the model chose it or the person ran it with /skill.
  load_skill: (a) => ({ pre: "Used skill: ", obj: String(a.name ?? "") }),
  ask_user: () => ({ pre: "Asked you a question" }),
  propose_plan: () => ({ pre: "Proposed a plan" }),
  request_directory: (a) => ({ pre: "Asked for folder access — ", obj: String(a.path ?? "") }),
};

/** What a finished call did, in the past tense. */
export function humanizeTool(name: string, args: any): HumanLine {
  const a = argsOf(args);
  if (EDIT_TOOLS.has(name)) return { pre: "Edited ", obj: a.path ? fileName(String(a.path)) : "files" };
  const template = DONE[name];
  if (template) return template(a);
  const rest = cut(shortArgs(a), 80);
  return { pre: `Used ${name}`, ...(rest ? { post: ` — ${rest}` } : {}) };
}

/** The approval card's headline: the action being decided, as a request.
 *
 *  The tool's own contract wins when it declared one: it is written beside the tool, in the words
 *  of what that tool does, and it reaches tools this file has never heard of, every MCP tool, every
 *  connector, every toolkit. The names below are only what this file happens to know (the rest used
 *  to read "Use mcp__jira__create_issue"); they are the fallback for tools without a contract, and
 *  should shrink as contracts arrive. */
export function approvalTitle(subject: ApprovalSubject): HumanLine {
  const summary = subject.presented?.summary?.trim();
  return summary ? { pre: summary } : humanizeApprovalTitle(subject.name, subject.args);
}

function sendTo(what: string, args: Args): HumanLine {
  const { tail } = messageTarget(String(args.target ?? ""));
  return tail ? { pre: `Send ${what} to `, obj: tail } : { pre: `Send ${what}` };
}

const ASKED: Record<string, (a: Args) => HumanLine> = {
  write_file: (a) => ({ pre: "Write ", obj: fileName(String(a.path ?? "a file")) }),
  // A command leads with the model's own description: "Run a command — fetch stock data".
  run_shell: (a) => ({ pre: "Run a command", ...described(a) }),
  send_message: (a) => sendTo("a message", a),
  send_file: (a) => sendTo("a file", a),
  create_scheduled_task: (a) => (a.title ? { pre: "Create the automation ", obj: quoted(a.title, 60) } : { pre: "Create an automation" }),
  // "Add", never "install", and the place is "your skills".
  save_skill: (a) => (a.name ? { pre: "Add skill ", obj: String(a.name), post: " to your skills" } : { pre: "Add a skill to your skills" }),
};

export function humanizeApprovalTitle(name: string, args: any): HumanLine {
  const a = argsOf(args);
  if (EDIT_TOOLS.has(name)) return { pre: "Edit ", obj: a.path ? fileName(String(a.path)) : "files" };
  return ASKED[name]?.(a) ?? { pre: `Use ${name}` };
}

/** An approval whose call never ran (most often declined): what the agent meant to do. */
export function humanizeAsk(name: string, args: any): HumanLine {
  const a = argsOf(args);
  if (EDIT_TOOLS.has(name)) return { pre: "Wanted to edit ", obj: a.path ? fileName(String(a.path)) : "files" };
  if (name === "run_shell") return { pre: "Wanted to run ", obj: cut(String(a.command ?? ""), 60) };
  if (name === "write_file") return { pre: "Wanted to write ", obj: fileName(String(a.path ?? "a file")) };
  if (name === "send_message") {
    const { platform, tail } = messageTarget(String(a.target ?? ""));
    return tail ? { pre: "Wanted to message ", obj: tail, post: ` on ${platform}` } : { pre: "Wanted to send a message" };
  }
  return { pre: `Wanted to use ${name}` };
}
