// The command at the start of the message box, highlighted where it stands: `/init` in
// the accent, and while nothing is typed after it, its line in a faint ink after it.
//
// A textarea cannot colour part of its text, so this is a layer laid over the box's first line,
// in the box's own font and padding, read from the textarea itself: the name is drawn over the
// textarea's glyphs on the card's own ground (so the two never show through each other), and the
// hint sits where the caret waits after the space. It moves with the box's scroll and takes no
// pointer, so a click still lands in the text.

import { useLayoutEffect, useState, type CSSProperties } from "react";

export function CommandMark({
  textarea,
  name,
  hint,
  scrollTop,
}: {
  textarea: HTMLTextAreaElement | null;
  name: string;
  hint?: string;
  scrollTop: number;
}) {
  const [box, setBox] = useState<CSSProperties | null>(null);
  useLayoutEffect(() => {
    if (!textarea) return;
    const measure = () => {
      const style = getComputedStyle(textarea);
      setBox({
        left: style.paddingLeft,
        right: style.paddingRight,
        top: style.paddingTop,
        font: style.font,
        letterSpacing: style.letterSpacing,
        lineHeight: style.lineHeight,
      });
    };
    measure();
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    observer?.observe(textarea);
    return () => observer?.disconnect();
  }, [textarea]);
  if (!box) return null;
  return (
    <div
      className="cmd-mark"
      data-testid="command-mark"
      aria-hidden
      style={{ ...box, transform: `translateY(${-scrollTop}px)`, overflow: "hidden", textOverflow: "ellipsis" }}
    >
      <span className="cmd-mark-name">/{name}</span>
      {hint ? <span className="cmd-mark-hint"> {hint}</span> : null}
    </div>
  );
}
