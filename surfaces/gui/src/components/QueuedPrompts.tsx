// Messages the user wrote while the agent was still working.
//
// The engine reads one turn at a time: a second `user_message` mid-turn is rejected, so the
// composer used to simply refuse the keystroke (`props.running && !props.gateOpen` returned
// early and the draft stayed on screen, looking broken). A sentence typed during a long run
// is a real intent though — usually "while you're at it, also…" — so it is parked here and
// sent the moment the turn ends, in the order it was written.
//
// A queue the user cannot see is indistinguishable from a message that was swallowed, so
// each one waiting is a row of the composer's own card, above the text: what it says, that
// it goes after this turn, a way to hand it to the running turn now, and a way to take it
// back. Rows of the card, not a card of their own (redesign of 2026-10-12): the plan strip,
// the queue and the input used to be three boxes in three styles.

import { useState } from "react";
import type { Attachment, SentCommand } from "../types";
import { Icon } from "./Icon";

export interface QueuedPrompt {
  id: string;
  text: string;
  attachments?: Attachment[];
  skill?: string;
  /** @agent names the draft carried; they ride the send when the queue drains (P4). */
  mentions?: string[];
  /** The slash command it was written with, sent with it when the queue drains. */
  command?: SentCommand;
}

/** How many rows show before the rest fold into one line: the card must not grow without end. */
const SHOWN = 3;

/** A queued message can join the running turn only as plain text: the server refuses a
 * steer that carries files, a skill or a mention (those frame a new turn). */
const plain = (item: QueuedPrompt) =>
  item.skill === undefined && !(item.attachments?.length) && !(item.mentions?.length);

export function QueuedPrompts({
  items,
  onRemove,
  onSendNow,
  when = "after this turn",
}: {
  items: QueuedPrompt[];
  /** When the queue goes: after the turn, or when a `/compact` between turns ends. */
  when?: string;
  onRemove?: (id: string) => void;
  /** Hand a queued message to the running turn. Absent where steering is not offered. */
  onSendNow?: (item: QueuedPrompt) => void;
}) {
  const [all, setAll] = useState(false);
  if (items.length === 0) return null;
  const shown = all ? items : items.slice(0, SHOWN);
  const hidden = items.length - shown.length;
  return (
    <ul className="queued-prompts" data-testid="queued-prompts" role="status" aria-live="polite" aria-label="Queued messages">
      {shown.map((item) => (
        <li className="queued-prompt" key={item.id} data-testid="queued-prompt">
          <Icon name="clock" size={13} className="queued-prompt-icon" />
          {item.skill !== undefined && <span className="queued-prompt-skill">/{item.skill}</span>}
          {item.mentions?.map((name) => (
            <span className="queued-prompt-skill" key={`queued-mention-${name}`}>
              @{name}
            </span>
          ))}
          <span className="queued-prompt-text" title={item.text}>
            {item.text}
          </span>
          {item.attachments !== undefined && item.attachments.length > 0 && (
            <span className="queued-prompt-count">
              {item.attachments.length} file{item.attachments.length === 1 ? "" : "s"}
            </span>
          )}
          <span className="queued-prompt-when" data-testid="queued-prompt-when">
            {when}
          </span>
          {onSendNow !== undefined && plain(item) && (
            <button
              type="button"
              className="queued-prompt-now"
              title="Send into the running turn"
              data-testid="queued-prompt-now"
              onClick={() => onSendNow(item)}
            >
              Send now
            </button>
          )}
          {onRemove !== undefined && (
            <button
              type="button"
              className="queued-prompt-x"
              aria-label="Remove from the queue"
              title="Remove from the queue"
              onClick={() => onRemove(item.id)}
            >
              <Icon name="x" size={12} />
            </button>
          )}
        </li>
      ))}
      {(hidden > 0 || all) && items.length > SHOWN && (
        <li className="queued-prompt queued-prompt-more">
          <span className="queued-prompt-text">{all ? `${items.length} queued, sent in order` : `${hidden} more ${hidden === 1 ? "is" : "are"} queued`}</span>
          <button type="button" className="queued-prompt-now quiet" data-testid="queued-prompts-toggle" aria-expanded={all} onClick={() => setAll((v) => !v)}>
            {all ? "Show fewer" : "Show all"}
          </button>
        </li>
      )}
    </ul>
  );
}
