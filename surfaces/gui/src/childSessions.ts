// The parent's own view of its subagent children (step C).
//
// The app-wide `/ws/events` stream announces every child as it is born — `session_created`
// when the manager makes the session, `session_status` busy/idle while its turn runs. That
// is the sibling state the child-session view's Prev/Next walks. The sidebar deliberately
// keeps its roots-only list (the REST listing excludes `kind="subagent"` by default): a
// child is reached through the task row that spawned it, the way the reference design nests it on the
// active path rather than in the top-level session list.

/** One `kind="subagent"` session, as the app heard about it on the event stream. */
export interface ChildSession {
  sessionId: string;
  parentSession: string;
  /** The child's session title — the `"<description> (@<agent> subagent)"`. */
  title: string;
  agent: string;
  status: "busy" | "idle";
}

/**
 * The description inside a child session's title (`"<description> (@<agent> subagent)"`).
 *
 * The crumb's leaf is the description, and Prev/Next only ever saw the child through its
 * `session_created` title — so the suffix has to come off here instead of at each caller.
 */
export function childDescription(title: string): string {
  const match = /^(.*?)\s*\(@[^)]+ subagent\)\s*$/.exec(title);
  return match ? match[1] : title;
}

/**
 * The parent's children, oldest first. Object insertion order IS event order — the map is
 * only ever appended to — so Prev/Next walk the parent's `kind="subagent"` sessions in the
 * order they were spawned, which is the order their task rows appear in the transcript.
 */
export function childrenOf(
  children: Record<string, ChildSession>,
  parentSession: string | undefined,
): ChildSession[] {
  if (!parentSession) return [];
  return Object.values(children).filter((c) => c.parentSession === parentSession);
}

/**
 * The direct child of `rootId` that is ON THE PATH to `activeId`
 * `childSessionOnPath` (`app/src/pages/layout/helpers.ts`).
 *
 * The sidebar nests exactly this child, level by level, so a child is visible while the
 * session on screen is inside it and disappears the moment the reader leaves the path.
 * `children` is the registry the app built from `session_created` / the `/children` seed;
 * walking `parentSession` links rather than scanning for a parent match is what finds a
 * grandchild when only the leaf and the root were heard about.
 */
export function childSessionOnPath(
  children: Record<string, ChildSession>,
  rootId: string,
  activeId?: string,
): ChildSession | undefined {
  if (!activeId || activeId === rootId) return undefined;
  let id: string | undefined = activeId;
  const seen = new Set<string>();
  while (id && !seen.has(id)) {
    seen.add(id);
    const session: ChildSession | undefined = children[id];
    if (!session?.parentSession) return undefined;
    if (session.parentSession === rootId) return session;
    id = session.parentSession;
  }
  return undefined;
}
