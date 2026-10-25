import { fetch, httpBase } from "./base";

// -- project bindings (pass 20) ---------------------------------------

export interface ProjectMenu {
  kind: "memory";
  bound: string | null;
  derived: { kind: "git" | "folder"; label: string; full: string; key: string } | null;
  named: { name: string; key: string }[];
}

export async function getProjectMenu(sessionId: string, kind: "memory"): Promise<ProjectMenu> {
  const r = await fetch(`${httpBase()}/v1/sessions/${sessionId}/project-menu?kind=${kind}`);
  return r.json();
}

export async function setProjectBinding(
  sessionId: string,
  kind: "memory",
  name: string | null,
): Promise<{ ok: boolean; error?: string }> {
  const r = await fetch(`${httpBase()}/v1/sessions/${sessionId}/bindings`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ kind, name }),
  });
  return r.json();
}

export async function nameCurrentProject(
  sessionId: string,
  kind: "memory",
  name: string,
): Promise<{ ok: boolean; error?: string }> {
  const r = await fetch(`${httpBase()}/v1/sessions/${sessionId}/project-name`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ kind, name }),
  });
  return r.json();
}
