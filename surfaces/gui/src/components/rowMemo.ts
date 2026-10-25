// Equality for the memo'd transcript rows.
//
// A turn's rows are rebuilt on every render (`buildRows`), and a fold of consecutive calls is a NEW array of
// the SAME tool items each time. Comparing the array by identity would miss every time and the memo would
// be for nothing; comparing the items it holds is what says "this row did not change". Items are replaced
// only when they change (`updateLastTool`, `appendToolOutput` copy the one they touch), so identity of the
// items is both correct and cheap.

export function sameItems<T>(a: readonly T[] | undefined, b: readonly T[] | undefined): boolean {
  if (a === b) return true;
  if (!a || !b || a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
  return true;
}

/** A delegation card's entries: `{tool, approval}` objects built fresh each render around stable items. */
export function sameDelegations<T extends { tool: unknown; approval?: unknown }>(
  a: readonly T[],
  b: readonly T[],
): boolean {
  if (a === b) return true;
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) if (a[i].tool !== b[i].tool || a[i].approval !== b[i].approval) return false;
  return true;
}
