import { fetch, httpBase } from "./base";

// -- Memory settings: the learning switch, auto memory, and SOUL.md. The memory
// itself is files — see `auto-memory.ts` (a project's notes, or `scope: "user"`).

export interface MemorySettings {
  // Learning about the person: while on, the user memory is writable and extraction may
  // add to it. Off stops new saves; what is known is still used.
  enabled: boolean;
  // The user's SOUL.md — identity + instructions they write. Stored as a real file on
  // this machine (see `soul_path`); injected verbatim at the start of every session.
  user_rules: string;
  soul_path?: string;
  // Auto memory (Claude-Code-shaped): a machine-local per-project folder of .md notes the
  // the agent writes itself.
  auto_memory: boolean;
}

export async function getMemorySettings(): Promise<MemorySettings> {
  const res = await fetch(`${httpBase()}/v1/memory/settings`);
  return res.json();
}

export async function setMemorySettings(
  patch: Partial<MemorySettings>,
): Promise<MemorySettings & { ok?: boolean; error?: string }> {
  const res = await fetch(`${httpBase()}/v1/memory/settings`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  return res.json();
}
