// The interactive asks, read back as what happened: the question and its answer, the folder
// and whether it was granted, the plan's verdict. Their generic row showed the call's
// arguments and result as JSON (owner, 2026-09-28: "plan, asked, folder accept render
// thuần chuỗi json").

import type { ToolItem } from "./cardModels";
import { parseMaybeJson, type FieldRow } from "./fields";

type Dict = Record<string, unknown>;

function dict(value: unknown): Dict | null {
  const parsed = parseMaybeJson(value);
  return typeof parsed === "object" && parsed !== null && !Array.isArray(parsed)
    ? (parsed as Dict)
    : null;
}

function text(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

/** The call's result: the live wire's JSON preview, or a replay's model-facing text. */
function resultOf(item: ToolItem): { data: Dict | null; raw: string } {
  const raw = typeof item.preview === "string" ? item.preview.trim() : "";
  const data = dict(raw);
  // A live preview can be a JSON string holding the text.
  if (data === null && raw.startsWith('"')) {
    const inner = parseMaybeJson(raw);
    if (typeof inner === "string") return { data: dict(inner), raw: inner.trim() };
  }
  return { data, raw };
}

/** Answers keyed by header-or-question (a grouped ask), as "question → answer" pairs. */
export function answerPairs(answer: string): Array<{ question: string; answer: string }> | null {
  const parsed = dict(answer);
  if (parsed === null) return null;
  const source = dict(parsed.answers) ?? parsed;
  const pairs = Object.entries(source)
    .filter(([, v]) => typeof v === "string" || typeof v === "number")
    .map(([question, v]) => ({ question, answer: String(v) }));
  return pairs.length ? pairs : null;
}

export function askFields(item: ToolItem): FieldRow[] {
  const args = dict(item.args) ?? {};
  const rows: FieldRow[] = [];
  const grouped = Array.isArray(args.questions) ? (args.questions as unknown[]) : [];
  const questions = grouped
    .map((q) => dict(q))
    .filter((q): q is Dict => q !== null)
    .map((q) => ({ key: text(q.header) || text(q.question), question: text(q.question) }));
  if (!questions.length && text(args.question)) questions.push({ key: "", question: text(args.question) });

  const { data, raw } = resultOf(item);
  const answers = dict(data?.answers);
  const single = text(data?.answer);
  const error = text(data?.error);
  const pending = item.status === "…";

  questions.forEach((q, index) => {
    rows.push({ label: questions.length > 1 ? `Question ${index + 1}` : "Question", value: q.question });
    const answer = answers
      ? text(answers[q.key]) || text(answers[q.question]) || text(Object.values(answers)[index])
      : index === 0
        ? single
        : "";
    if (answer) rows.push({ label: "Answer", value: answer, tone: "ok" });
  });
  if (!questions.length && raw && data === null) rows.push({ label: "Answer", value: raw });
  if (!pending && !single && !answers) {
    rows.push({ label: "Answer", value: error || "No answer", tone: error ? "danger" : "muted" });
  }
  return rows;
}

export function folderFields(item: ToolItem): FieldRow[] {
  const args = dict(item.args) ?? {};
  const { data, raw } = resultOf(item);
  const rows: FieldRow[] = [];
  const path = text(data?.path) || text(args.path);
  rows.push({ label: "Folder", value: path || "Chosen by you", mono: !!path, ...(path ? {} : { tone: "muted" as const }) });
  const wantsPrimary = args.primary === true || args.primary === "true";
  const wantsWrite = args.writable === true || args.writable === "true";
  rows.push({
    label: "Asked for",
    value: wantsPrimary ? "Make it the workspace" : wantsWrite ? "Read and write" : "Read only",
  });
  if (text(args.reason)) rows.push({ label: "Why", value: text(args.reason) });
  if (item.status === "…") return rows;

  let granted: boolean | null = typeof data?.granted === "boolean" ? (data.granted as boolean) : null;
  let writable = data?.writable === true;
  if (granted === null && raw) {
    // A replay carries the model-facing line: "granted: <path> (writable)", "Error: …".
    granted = raw.toLowerCase().startsWith("granted");
    writable = /\(.*writable.*\)\s*$/i.test(raw);
  }
  const why = text(data?.note) || text(data?.reason) || text(data?.error) || (granted ? "" : raw.replace(/^error:\s*/i, ""));
  if (granted) {
    const access = data?.primary === true ? "now the workspace" : writable ? "read and write" : "read only";
    rows.push({ label: "Result", value: `Granted · ${access}`, tone: "ok" });
  } else {
    rows.push({ label: "Result", value: "Not granted", tone: "danger" });
  }
  if (why) rows.push({ label: "Note", value: why, tone: "muted" });
  return rows;
}

/** Whether the plan was shown with nobody asked: full access waits on no one, and
 * the plan is not approved. `approved_by` is how a transcript before 2026-10-18 said it. */
export function planNotAsked(item: ToolItem): boolean {
  if (item.status === "…") return false;
  const { data } = resultOf(item);
  return data?.not_asked === "full access" || (data?.approved === true && data.approved_by === "full access");
}

/** The plan's verdict, under the plan itself. Empty while it waits for one. */
export function planVerdict(item: ToolItem): FieldRow[] {
  if (item.status === "…") return [];
  const { data, raw } = resultOf(item);
  if (data === null) return raw ? [{ label: "Verdict", value: raw }] : [];
  if (planNotAsked(item)) {
    return [{ label: "Verdict", value: "Not asked · Full access", tone: "ok" }];
  }
  if (data.approved === true) {
    const mode = text(data.mode);
    return [{ label: "Verdict", value: mode ? `Approved · ${mode.replace(/[-_]/g, " ")}` : "Approved", tone: "ok" }];
  }
  if (data.approved === false) {
    const rows: FieldRow[] = [{ label: "Verdict", value: "Changes requested", tone: "danger" }];
    const feedback = text(data.feedback);
    if (feedback) rows.push({ label: "Feedback", value: feedback });
    return rows;
  }
  const error = text(data.error);
  return error ? [{ label: "Verdict", value: error, tone: "danger" }] : [];
}
