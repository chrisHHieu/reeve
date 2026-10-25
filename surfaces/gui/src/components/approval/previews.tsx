import { useRef, useState } from "react";
import type { HumanLine } from "../../humanize";
import { useAutoHeight } from "../../ui/useAutoHeight";
import { DecisionBody } from "../decision/Decision";
import { Icon } from "../Icon";

// What an approval shows of the action asked about, taken from the call's arguments: the file or
// the message does not exist yet, so nothing else could show it (the mock's note).

// A preview is cut at five lines and at a number of characters: a one-paragraph Slack digest has no
// line breaks at all and once stretched the card to the height of the transcript. 320, not 420: the
// card is as wide as the composer now (decision-wrap), about a tenth narrower than the column it
// used to fill, and 420 characters wrapped past the clamp's ~200px.
const PREVIEW_LINES = 5;
const PREVIEW_CHARS = 320;

/** The card's title, its object in bold. */
export function TitleText({ line }: { line: HumanLine }) {
  return (
    <span className="approval-title">
      {line.pre}
      {line.obj && <b>{line.obj}</b>}
      {line.post}
    </span>
  );
}

/** The thing asked about, in the card's inset block: cut short, opened where it is, and scrolled
 *  inside the block when long, so the card never grows past the window. */
export function PreviewBlock({ text, mono = true }: { text: string; mono?: boolean }) {
  const [whole, setWhole] = useState(false);
  // Opening or closing glides to the new height (the lines used to appear or vanish in one frame).
  const block = useRef<HTMLDivElement>(null);
  useAutoHeight(block);
  const lines = text.split("\n");
  const long = lines.length > PREVIEW_LINES || text.length > PREVIEW_CHARS;
  let shown = text;
  if (long && !whole) {
    shown = lines.slice(0, PREVIEW_LINES).join("\n");
    if (shown.length > PREVIEW_CHARS) shown = shown.slice(0, PREVIEW_CHARS).trimEnd() + "…";
  }
  return (
    <>
      <div ref={block} className={`dec-b ${mono ? "code" : "prose"}${whole ? " all" : ""}`} data-testid="approval-preview">
        {shown}
      </div>
      {long && (
        <button type="button" className="dec-more" aria-expanded={whole} onClick={() => setWhole((was) => !was)}>
          {whole ? "Show less" : lines.length > PREVIEW_LINES ? `Show all ${lines.length} lines` : "Show the full message"}
        </button>
      )}
    </>
  );
}

/** A message about to be sent: quoted whole when it is one short line, the cut-short block otherwise. */
export function MessagePreview({ text, label }: { text: string; label?: string }) {
  if (text.length > 220 || text.includes("\n")) return <PreviewBlock text={text} mono={false} />;
  return (
    <DecisionBody>
      {label ? `${label}: ` : ""}“{text}”
    </DecisionBody>
  );
}

/** What a saved skill is reviewed by: its description, all its instructions
 *  (cut short, opened, scrolled), every file it brings, and the line that always says where it is
 *  added and when it can be used. The parked card shows the same, word for word: one decision, one
 *  way of putting it. */
export function SaveSkillPreview({ args }: { args: any }) {
  const files: unknown[] = Array.isArray(args?.files) ? args.files : [];
  return (
    <>
      {args?.description && <p className="dec-note">{String(args.description)}</p>}
      {args?.instructions && <PreviewBlock text={String(args.instructions)} mono={false} />}
      {files.length > 0 && (
        <div data-testid="skill-bundle-files">
          {files.map((file, i) => (
            <span className="approval-filechip" key={i}>
              <span className="ico">
                <Icon name="file" size={13} />
              </span>
              {String(file).split(/[\\/]/).pop() || String(file)}
            </span>
          ))}
        </div>
      )}
      <p className="dec-note">Approving adds it to your skills on this computer — usable in every conversation from then on.</p>
    </>
  );
}
