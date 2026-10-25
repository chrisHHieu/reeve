// What a sent message carried, drawn above its bubble and right-aligned with it: the pictures as
// themselves, the files as cards. The bubble keeps only the person's words, and a message that is
// only a picture has no empty bubble under it.
//
// A picture opens large in the same viewer an answer's pictures use (step, zoom, download). A file
// opens in the panel's Files tab from the copy the message kept; one with no kept copy
// (an older conversation, a copy that could not be saved) is a card that says what it was.

import { useState } from "react";
import type { Attachment } from "../../types";
import { openArtifact } from "../Markdown";
import { ImageOverlay } from "../markdown/MarkdownImage";
import { AttachmentCard, AttachmentTile } from "./AttachmentCard";

/** Pictures past this many fold into the last tile's "+N". */
const TILES = 4;
/** Files past this many wait behind "N more files". */
const CARDS = 3;

export function MessageAttachments({ attachments }: { attachments: Attachment[] }) {
  const [viewing, setViewing] = useState<number | null>(null);
  const [allFiles, setAllFiles] = useState(false);
  const pictures = attachments.filter((a) => a.kind === "image" && a.data_url);
  const files = attachments.filter((a) => !(a.kind === "image" && a.data_url));
  if (pictures.length === 0 && files.length === 0) return null;

  const cut = pictures.length > TILES;
  const shown = cut ? pictures.slice(0, TILES) : pictures;
  const extra = pictures.length - (TILES - 1);
  const filesShown = allFiles || files.length <= CARDS + 1 ? files : files.slice(0, CARDS);

  return (
    <div className="msg-attachments" data-testid="message-attachments">
      {shown.length > 0 && (
        <div className={"msg-pictures n" + Math.min(shown.length, TILES)}>
          {shown.map((a, i) => (
            <AttachmentTile
              key={i}
              a={a}
              more={cut && i === TILES - 1 ? extra : undefined}
              onOpen={() => setViewing(i)}
            />
          ))}
        </div>
      )}
      {files.length > 0 && (
        <div className="msg-files">
          {filesShown.map((a, i) => (
            <AttachmentCard key={i} a={a} onOpen={a.path ? () => openArtifact(a.path!) : undefined} />
          ))}
          {filesShown.length < files.length && (
            <button type="button" className="msg-files-more" onClick={() => setAllFiles(true)}>
              {files.length - filesShown.length} more files
            </button>
          )}
        </div>
      )}
      {viewing !== null && (
        <ImageOverlay
          images={pictures.map((a) => ({ src: a.data_url!, alt: a.name }))}
          start={viewing}
          onClose={() => setViewing(null)}
        />
      )}
    </div>
  );
}
