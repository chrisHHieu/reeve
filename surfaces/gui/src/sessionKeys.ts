// The keyboard rule that is not the composer's: Escape stops the running turn.
//
// It lives in its own module because the interesting part is the two refusals, and a refusal
// inside a `useEffect` in a 2,600-line file is a rule nobody can test. Both refusals are
// deliberate: an input with focus means Escape belongs to the user's own editing (the slash
// popup, a draft), and a gate means the engine is suspended waiting for an answer — there is
// nothing to stop, and the card's buttons are the decision.

export interface EscapeState {
  running: boolean;
  gateOpen: boolean;
}

export interface FocusTarget {
  tag: string;
  contentEditable: boolean;
}

const TEXT_ENTRY = new Set(["TEXTAREA", "INPUT"]);

export function shouldStopOnEscape(
  event: Pick<KeyboardEvent, "key" | "defaultPrevented">,
  state: EscapeState,
  active: FocusTarget | null,
): boolean {
  if (event.key !== "Escape" || event.defaultPrevented) return false;
  if (!state.running || state.gateOpen) return false;
  if (active !== null && (TEXT_ENTRY.has(active.tag) || active.contentEditable)) return false;
  return true;
}
