// One attached file as a card: the file's own mark (the Files panel's), its name, and what it is in
// a word with its size or length. The composer's cards can be taken off; a sent message's open the
// file. A picture is not a card: it is a tile (AttachmentTile), drawn as itself.

import type { Attachment } from "../../types";
import { FileIcon } from "../FileIcon";
import { Icon } from "../Icon";
import { attachmentMeta } from "./attachmentFacts";

export function AttachmentCard({
  a,
  state,
  reason,
  onRemove,
  onOpen,
}: {
  a: Attachment;
  /** Still being read, or refused (with why). */
  state?: "reading" | "refused";
  reason?: string;
  onRemove?: () => void;
  onOpen?: () => void;
}) {
  const meta = state === "reading" ? "Reading…" : state === "refused" ? reason ?? "Will not be sent" : attachmentMeta(a);
  const body = (
    <>
      <span className="att-card-mark" aria-hidden>
        {state === "refused" ? <Icon name="warning" size={16} /> : <FileIcon path={a.name} size={18} />}
      </span>
      <span className="att-card-text">
        <span className="att-card-name" title={a.name}>
          {a.name}
        </span>
        {/* Under the pointer an openable card says what a click does, in place of its facts. */}
        <span className="att-card-meta">{meta}</span>
        {onOpen && <span className="att-card-open">Open</span>}
      </span>
    </>
  );
  const cls = "att-card" + (state ? ` ${state}` : "") + (onOpen ? " openable" : "");
  return (
    <div className={cls} data-testid="attachment-card" data-state={state}>
      {onOpen ? (
        <button type="button" className="att-card-main" onClick={onOpen} aria-label={`Open ${a.name}`}>
          {body}
        </button>
      ) : (
        <span className="att-card-main">{body}</span>
      )}
      {onRemove && (
        <button
          type="button"
          className="att-card-x"
          onClick={onRemove}
          aria-label={`Remove ${a.name}`}
          title="Remove"
        >
          <Icon name="x" size={11} />
        </button>
      )}
    </div>
  );
}

/** A picture: drawn as itself, opened large on a click; the × shows on the one under the pointer. */
export function AttachmentTile({
  a,
  onOpen,
  onRemove,
  more,
  className,
}: {
  a: Attachment;
  onOpen?: () => void;
  onRemove?: () => void;
  /** The last tile of a cut run says how many more there are, and opens the viewer at itself. */
  more?: number;
  className?: string;
}) {
  return (
    <div className={"att-tile" + (className ? ` ${className}` : "")} data-testid="attachment-tile">
      <button
        type="button"
        className="att-tile-open"
        onClick={onOpen}
        aria-label={more ? `Show all pictures (${more} more)` : `Open ${a.name}`}
        title={a.name}
      >
        <img src={a.data_url} alt={a.name} draggable={false} />
        {more ? <span className="att-tile-more">+{more}</span> : null}
      </button>
      {onRemove && (
        <button type="button" className="att-tile-x" onClick={onRemove} aria-label={`Remove ${a.name}`} title="Remove">
          <Icon name="x" size={10} />
        </button>
      )}
    </div>
  );
}
