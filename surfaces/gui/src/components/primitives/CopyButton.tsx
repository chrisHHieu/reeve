// Copy-to-clipboard, once, for every card that offers it.
//
// There were two inline copies of this before (the message bubble's meta strip and the
// right rail) and the five card blocks would have made it seven. The behaviour worth
// keeping identical everywhere is the one the bubble established: "Copied" appears only
// after the write RESOLVES. WebKit rejects a clipboard write outside a trusted gesture,
// and a button that says "Copied" after a silent no-op is worse than one that says
// nothing.

import { useRef, useState } from "react";
import { Icon } from "../Icon";

export function CopyButton({
  text,
  label = "Copy",
  copiedLabel = "Copied",
  className,
  iconOnly = false,
}: {
  text: string;
  label?: string;
  copiedLabel?: string;
  className?: string;
  /** No visible label — for placements (the markdown code block) where the button is an
   *  icon revealed on hover. The accessible name is still the label. */
  iconOnly?: boolean;
}) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<number | null>(null);
  const copy = () => {
    navigator.clipboard
      ?.writeText(text)
      .then(() => {
        setCopied(true);
        if (timer.current !== null) window.clearTimeout(timer.current);
        timer.current = window.setTimeout(() => setCopied(false), 1200);
      })
      .catch(() => {});
  };
  return (
    <button
      type="button"
      onClick={copy}
      // The label is the accessible name AND the visible one; a card footer has no room
      // for an icon plus text, and an icon alone would be a button with no name.
      aria-label={copied ? copiedLabel : label}
      title={copied ? copiedLabel : label}
      className={
        (iconOnly
          ? "inline-flex flex-none items-center justify-center rounded text-faint transition-colors "
          : "inline-flex flex-none items-center gap-1 rounded px-1.5 py-0.5 text-[11px] leading-none text-faint transition-colors hover:text-muted ") +
        (className || "")
      }
      data-testid="copy-button"
      data-copied={copied || undefined}
    >
      <Icon name={copied ? "check" : "copy"} size={iconOnly ? 14 : 11} />
      {!iconOnly && (copied ? copiedLabel : label)}
    </button>
  );
}
