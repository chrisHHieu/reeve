// A built-in tool's arguments and result as labelled lines instead of a JSON tree.
//
// Owner rule (2026-09-28): what Reeve itself provides renders as UI, and only an MCP
// server's payload — whose shape nobody here knows — keeps the raw JSON view. The
// interactive asks (a question, a folder, a plan) get their own words on top of this in
// `interaction.ts`; everything else falls back to the key/value lines built here.

export interface FieldRow {
  label: string;
  value: string;
  /** Colours the value: a granted/answered outcome, a refusal, or a note that it is empty. */
  tone?: "ok" | "danger" | "muted";
  /** A path, id or command — read in the mono face. */
  mono?: boolean;
}

/** `subagent_type` → "Subagent type", `maxResults` → "Max results". */
export function humanKey(key: string): string {
  const spaced = key
    .replace(/[_-]+/g, " ")
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .trim()
    .toLowerCase();
  return spaced ? spaced[0].toUpperCase() + spaced.slice(1) : key;
}

/** JSON text parsed, anything else returned as it came. */
export function parseMaybeJson(value: unknown): unknown {
  if (typeof value !== "string") return value;
  const text = value.trim();
  if (!(text.startsWith("{") || text.startsWith("["))) return value;
  try {
    return JSON.parse(text);
  } catch {
    return value;
  }
}

const MONO_KEYS = /(^|_)(path|paths|file|dir|directory|cwd|command|cmd|url|id|pattern|glob)$/i;

function scalar(value: unknown): string | null {
  if (value === null || value === undefined) return null;
  if (typeof value === "string") return value;
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "number") return String(value);
  if (Array.isArray(value) && value.every((v) => v === null || typeof v !== "object")) {
    return value.map((v) => scalar(v) ?? "").filter(Boolean).join(", ");
  }
  return null;
}

/** An object's keys as lines, one level deep; a deeper value is shown compactly. */
export function fieldsOf(value: unknown): FieldRow[] | null {
  const parsed = parseMaybeJson(value);
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) return null;
  const rows: FieldRow[] = [];
  for (const [key, raw] of Object.entries(parsed as Record<string, unknown>)) {
    if (key.startsWith("_")) continue; // engine-side card metadata, not the call's content
    if (raw === null || raw === undefined) {
      // "No value" is an answer (`goal status` → `{"goal": null}`), so it gets a line. It used
      // to fall through to `Object.entries(null)` below and throw during render; with nothing
      // above the row to catch it, one such result blanked the whole app on every reload of
      // its session (14715fae-61f, 2026-10-04).
      rows.push({ label: humanKey(key), value: "None", tone: "muted" });
      continue;
    }
    const text = scalar(raw);
    if (text === "") continue;
    const shown =
      text ??
      (Array.isArray(raw)
        ? `${raw.length} item${raw.length === 1 ? "" : "s"}`
        : Object.entries(raw as Record<string, unknown>)
            .map(([k, v]) => `${humanKey(k)}: ${scalar(v) ?? "…"}`)
            .join(" · "));
    if (!shown) continue;
    rows.push({ label: humanKey(key), value: shown, ...(MONO_KEYS.test(key) ? { mono: true } : {}) });
  }
  return rows;
}
