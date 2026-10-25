// What the transcript area shows while a conversation's history is being fetched.
//
// Opening a session with 300 turns used to render the EMPTY-STATE hero first ("Let's build
// something.") and then replace it with the transcript a moment later: the app looked like it
// had forgotten the conversation, then remembered it. A skeleton states the same fact without
// the wrong claim — something is coming, and it is text.
//
// It is deliberately not a spinner: a spinner says "wait", a skeleton says "wait, and this is
// the shape of what arrives".

export function SessionSkeleton() {
  return (
    <div className="transcript session-skeleton" data-testid="session-skeleton" aria-hidden>
      <div className="skeleton-row" style={{ width: "34%" }} />
      <div className="skeleton-bubble" />
      <div className="skeleton-row" style={{ width: "22%" }} />
      <div className="skeleton-bubble" data-tall />
      <div className="skeleton-row" style={{ width: "46%" }} />
      <div className="skeleton-bubble" />
    </div>
  );
}
