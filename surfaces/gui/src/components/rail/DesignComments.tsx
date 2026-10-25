// Comments on a design, in its tab, as OpenDesign has them (read 2026-10-19): a click
// in Comment mode drops a numbered pin and opens a box right there to say what should change; the
// comment stays on the page, in a dock beside it, until it goes into a message, alone or with
// others. A comment whose element the page no longer has says so instead of pointing nowhere.
import { useEffect, useRef, useState } from "react";
import { Icon } from "../Icon";
import { elementLabel, type DesignPin } from "./designAsks";

/** The box a just-dropped comment is typed in, at the place it was dropped. */
export function CommentPopover({
  pin,
  left,
  top,
  onChange,
  onSend,
  onKeep,
  onCancel,
}: {
  pin: DesignPin;
  left: number;
  top: number;
  onChange: (value: string) => void;
  onSend: () => void;
  onKeep: () => void;
  onCancel: () => void;
}) {
  const box = useRef<HTMLTextAreaElement | null>(null);
  useEffect(() => box.current?.focus(), [pin.n]);
  const said = pin.comment.trim().length > 0;
  return (
    <div
      className="dv-popover"
      role="dialog"
      aria-label={`Comment ${pin.n} on ${elementLabel(pin.el)}`}
      data-testid="design-comment-popover"
      style={{ left, top }}
      onKeyDown={(event) => {
        if (event.key === "Escape") {
          event.preventDefault();
          onCancel();
        } else if (event.key === "Enter" && (event.metaKey || event.ctrlKey) && said) {
          event.preventDefault();
          onSend();
        }
      }}
    >
      <div className="dv-popover-head">
        <span className="page-pin-n" aria-hidden="true">
          {pin.n}
        </span>
        <span className="dv-popover-on" title={pin.el.selector}>
          {elementLabel(pin.el)}
        </span>
      </div>
      <textarea
        ref={box}
        rows={3}
        aria-label={`Comment ${pin.n}`}
        placeholder="What should change here?"
        value={pin.comment}
        data-testid="design-comment-input"
        onChange={(event) => onChange(event.target.value)}
      />
      <div className="dv-popover-foot">
        <button type="button" className="fv-note-btn" onClick={onCancel}>
          Cancel
        </button>
        <button type="button" className="btn sm" disabled={!said} data-testid="design-comment-keep" onClick={onKeep}>
          Keep
        </button>
        <button type="button" className="btn sm primary" disabled={!said} data-testid="design-comment-send" onClick={onSend}>
          Add to message
        </button>
      </div>
    </div>
  );
}

/** The comments kept on a page, beside it: each with its number, its part and its words, picked to
 *  go into a message together. */
export function CommentsDock({
  pins,
  onChange,
  onRemove,
  onSend,
  onClose,
}: {
  pins: DesignPin[];
  onChange: (n: number, value: string) => void;
  onRemove: (n: number) => void;
  /** Put the comments named (or every unsent one with words) in the message. */
  onSend: (only?: number[]) => void;
  onClose: () => void;
}) {
  const [picked, setPicked] = useState<number[]>([]);
  const ready = pins.filter((pin) => !pin.sent && pin.comment.trim());
  const chosen = picked.filter((n) => ready.some((pin) => pin.n === n));
  const toggle = (n: number) => setPicked((current) => (current.includes(n) ? current.filter((x) => x !== n) : [...current, n]));
  return (
    <aside className="dv-dock" aria-label="Comments" data-testid="design-comments-dock">
      <div className="dv-dock-head">
        <span className="dv-dock-title">Comments</span>
        <span className="dv-dock-count">{pins.length}</span>
        <span className="fv-fill" />
        <button type="button" className="artifact-icon-btn" aria-label="Close the comments" onClick={onClose}>
          <Icon name="x" size={13} />
        </button>
      </div>
      {pins.length === 0 ? (
        <p className="dv-dock-empty">Pick Comment, then click a part of the page to say what should change there.</p>
      ) : (
        <ol className="dv-dock-list">
          {pins.map((pin) => (
            <li key={pin.n} className={"dv-comment" + (pin.sent ? " is-sent" : "") + (pin.lost ? " is-lost" : "")} data-testid={`design-comment-${pin.n}`}>
              <div className="dv-comment-head">
                <input
                  type="checkbox"
                  aria-label={`Pick comment ${pin.n}`}
                  disabled={pin.sent || !pin.comment.trim()}
                  checked={chosen.includes(pin.n)}
                  onChange={() => toggle(pin.n)}
                />
                <span className="page-pin-n" aria-hidden="true">
                  {pin.n}
                </span>
                <span className="dv-comment-on" title={pin.el.selector}>
                  {elementLabel(pin.el)}
                </span>
                <button type="button" className="artifact-icon-btn" aria-label={`Remove comment ${pin.n}`} onClick={() => onRemove(pin.n)}>
                  <Icon name="x" size={12} />
                </button>
              </div>
              <textarea
                rows={2}
                aria-label={`Comment ${pin.n}`}
                placeholder="What should change here?"
                value={pin.comment}
                onChange={(event) => onChange(pin.n, event.target.value)}
              />
              {(pin.sent || pin.lost) && (
                <span className="dv-comment-state">
                  {pin.sent ? "In your message" : ""}
                  {pin.sent && pin.lost ? " · " : ""}
                  {pin.lost ? "No longer on the page: the part it was on is gone" : ""}
                </span>
              )}
            </li>
          ))}
        </ol>
      )}
      <div className="dv-dock-foot">
        <button
          type="button"
          className="btn sm primary"
          disabled={ready.length === 0}
          data-testid="design-comments-send"
          onClick={() => {
            onSend(chosen.length ? chosen : undefined);
            setPicked([]);
          }}
        >
          {chosen.length
            ? `Put ${chosen.length} in your message`
            : ready.length
              ? `Put ${ready.length === 1 ? "it" : `all ${ready.length}`} in your message`
              : "Nothing to send"}
        </button>
      </div>
    </aside>
  );
}
