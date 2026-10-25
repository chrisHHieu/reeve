/** "used 12× · last Sep 22", or "not used yet": how often the agent has loaded a skill. */
export function usageLine(row: { uses?: number; last_used_at?: number | null }): string {
  if (!row.uses) return "not used yet";
  const last = typeof row.last_used_at === "number" ? new Date(row.last_used_at * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric" }) : "";
  return last ? `used ${row.uses}× · last ${last}` : `used ${row.uses}×`;
}

/** A file's bytes as base64. FileReader stands in where File.arrayBuffer is missing (some webviews,
 *  jsdom); the bytes are turned to a string in chunks, since one call per byte overflows the stack. */
export async function fileToB64(file: File): Promise<string> {
  const buffer =
    typeof file.arrayBuffer === "function"
      ? await file.arrayBuffer()
      : await new Promise<ArrayBuffer>((resolve, reject) => {
          const reader = new FileReader();
          reader.onload = () => resolve(reader.result as ArrayBuffer);
          reader.onerror = () => reject(reader.error);
          reader.readAsArrayBuffer(file);
        });
  const bytes = new Uint8Array(buffer);
  const CHUNK = 0x8000;
  let binary = "";
  for (let at = 0; at < bytes.length; at += CHUNK) binary += String.fromCharCode(...bytes.subarray(at, at + CHUNK));
  return btoa(binary);
}
