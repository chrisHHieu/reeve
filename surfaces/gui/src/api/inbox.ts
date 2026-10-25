import type { GroupedQuestion, QuestionOption } from "../types";
import { fetch, httpBase } from "./base";

// -- the prompts that wait on the person ---------------------------------------
export interface InboxItem {
  id: string;
  session_id: string;
  kind: "approval" | "question" | "notification" | "directory" | "plan";
  title: string;
  body: string;
  state: "pending" | "resolved";
  resolution: string | null;
  inbox: string;
  created_at: string;
  resolved_at: string | null;
  visibility?: "inline" | "inbox";
  // Question metadata (ask_user): quick-reply choices + a free-text escape. Options may be rich
  // {label, description, recommended, preview} objects; `questions` is the grouped form
  // (stepper), whose resolution is a JSON object string keyed by header-or-question.
  options?: QuestionOption[];
  allow_text?: boolean;
  multi?: boolean;
  header?: string;
  questions?: GroupedQuestion[];
  // Kind-specific payload (directory: {path, writable}; …).
  data?: Record<string, any>;
  // Originating-session context (server-joined), so an item says which conversation asked.
  session_title?: string;
  session_agent?: string | null;
  session_workspace?: string | null;
  session_exists?: boolean;
}

export async function getInbox(sessionId?: string, state?: string): Promise<InboxItem[]> {
  const q = new URLSearchParams();
  if (sessionId) q.set("session_id", sessionId);
  if (state) q.set("state", state);
  const res = await fetch(`${httpBase()}/v1/inbox?${q.toString()}`);
  return (await res.json()).items;
}

export async function resolveInboxItem(
  id: string,
  resolution: string,
): Promise<{ ok: boolean }> {
  const res = await fetch(`${httpBase()}/v1/inbox/${encodeURIComponent(id)}/resolve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ resolution }),
  });
  return res.json();
}
