// The child session whose tab is on screen in the workspace panel, or null.
//
// The panel owns its tabs and tells the app which child it is showing; the chat's rows for
// that child (the Delegated list, the roster above the composer) mark themselves from here.
// A context, because those rows sit under memoized transcript rows that a prop would have to
// be threaded through, and re-rendered for.
import { createContext, useContext } from "react";

export const ShownChildContext = createContext<string | null>(null);

/** True when `childId` is the child the panel is showing. */
export function useIsShownChild(childId: string | null | undefined): boolean {
  const shown = useContext(ShownChildContext);
  return !!childId && shown === childId;
}
