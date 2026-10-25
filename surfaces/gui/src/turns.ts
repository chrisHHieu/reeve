// What counts as the PERSON speaking — the GUI half of harness/turns.py.
//
// The backend learned this lesson and wrote it down: `role === "user"` stopped being a
// reliable landmark the moment something other than a person could put content in front of
// the model. `read_image` was the first — the image an agent asks to LOOK at rides in a
// user-role message, because no provider portably accepts an image inside a tool result.
//
// Eight Python landmarks were routed through `is_user_turn`. Two here were not, and both
// failed exactly as that module's docstring predicted:
//
//   · the transcript rendered the agent's own image as a bubble from the user — "tôi có
//     gửi đâu" (owner-hit 2026-08-26), the surface claiming the person had done something
//     they had not;
//   · the replayed plan strip cleared mid-turn, because an attachment looked like the
//     start of a new turn.
//
// The tag is the whole distinction, and it travels on the message, so the rule is one
// predicate both sides read rather than a guard per landmark.

import type { ConversationMessage } from "./api";

/** Set by the engine on a message it put in front of the model on a TOOL's behalf; holds
 *  the id of the call that asked for it. Must match `ATTACHED_BY` in harness/turns.py. */
export const ATTACHED_BY = "_attached_by";

/** True when this message is the person actually saying something. */
export function isUserTurn(m: ConversationMessage | null | undefined): boolean {
  if (!m || typeof m !== "object") return false;
  return m.role === "user" && !attachedBy(m);
}

/** The tool call this message was attached for, or null when a person sent it. */
export function attachedBy(m: ConversationMessage | null | undefined): string | null {
  if (!m || typeof m !== "object") return null;
  const id = (m as Record<string, unknown>)[ATTACHED_BY];
  return typeof id === "string" && id ? id : null;
}
