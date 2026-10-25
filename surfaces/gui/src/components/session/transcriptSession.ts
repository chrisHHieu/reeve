// The conversation a transcript row belongs to. A row that draws one of the session's files (a
// design page's peek) needs it, and the transcript hands rows their items, not the
// session. Empty outside a conversation's column (tests, the child-session preview).
import { createContext } from "react";

export const TranscriptSession = createContext<string>("");
