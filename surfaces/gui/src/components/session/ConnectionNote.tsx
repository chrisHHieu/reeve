// Says so when the conversation's connection is down.
//
// The socket reconnects by itself (live/liveSession.ts), but until it has, the composer's Send
// is disabled and nothing on screen said why: a silent, dead-looking input is the worst answer
// to "the connection dropped". The note is one quiet line above the composer, and it waits a
// moment before appearing so the ordinary half-second of opening a conversation never flashes
// it. What the person typed is kept, and anything they answer meanwhile (an approval, a mode
// change) is held and sent on reconnect, which is the other half of what the line says.
import { useEffect, useState } from "react";

/** How long a connection may be down before the note shows. */
export const CONNECTION_NOTE_DELAY_MS = 1200;

export function ConnectionNote({
  connected,
  gaveUp = false,
  onRetry,
  machine,
}: {
  connected: boolean;
  /** The app stopped trying (the server keeps refusing this conversation). */
  gaveUp?: boolean;
  onRetry?: () => void;
  /** The remote machine that refused to start this conversation, when that is the cause: the
   *  line names it, and the crumb in the top bar says why. */
  machine?: string;
}) {
  const [late, setLate] = useState(false);
  useEffect(() => {
    if (connected) {
      setLate(false);
      return;
    }
    const timer = window.setTimeout(() => setLate(true), CONNECTION_NOTE_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [connected]);
  if (connected || (!late && !gaveUp)) return null;
  return (
    <div
      className="connection-note max-w-[var(--thread-w)] mx-auto mb-1.5 flex items-center gap-2 px-3 py-1.5 rounded-lg text-[12px] text-muted bg-paper"
      role="status"
      aria-live="polite"
      data-testid="connection-note"
    >
      <span
        className={"w-1.5 h-1.5 rounded-full bg-warnInk shrink-0" + (gaveUp ? "" : " animate-pulse")}
        aria-hidden
      />
      {gaveUp ? (
        <>
          <span className="flex-1">
            {machine ? `Reeve could not start on ${machine}.` : "Reeve could not open this conversation."}
          </span>
          <button
            type="button"
            className="shrink-0 text-accent font-medium hover:underline"
            data-testid="connection-retry"
            onClick={onRetry}
          >
            Try again
          </button>
        </>
      ) : (
        <span>Reconnecting — what you answer meanwhile is sent as soon as it is back.</span>
      )}
    </div>
  );
}
