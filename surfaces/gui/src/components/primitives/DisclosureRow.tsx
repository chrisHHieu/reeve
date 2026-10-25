// A row that opens and closes, and the whole row is the target.
//
// Three details carry the whole interaction, and each of them is one a native <details>
// gets differently:
//
//   `expandOnRowClick` makes the ROW the disclosure, not a chevron. A tool row is a
//   single line of text with a small icon at its start, and asking someone to hit a 14px
//   glyph to see a command's output is the reason `<details>` transcripts feel hostile.
//   The row therefore behaves like a button: role, tabIndex, aria-expanded, and Enter and
//   Space handled explicitly, because a div is not a button and a browser will not do it.
//
//   `keepContentWhenOpen` keeps the collapsed summary inline while the body shows. Without
//   it, opening a row makes its own title disappear and the reader loses the thing they
//   clicked on, which for a tool row is the only line naming what ran.
//
//   `previewChevron` swaps the leading icon for a chevron on HOVER. The resting row stays
//   undistracted (an icon that says what the tool is), and the affordance appears exactly
//   when a pointer is over it asking to be shown more.
//
// The expanded body is a SIBLING of the header, never a child of it: a click anywhere in
// the body — copying a command, selecting a diff line — must not fold the row back up.

import type { ReactNode } from "react";
import { Disclosure } from "../../ui";

export function DisclosureRow({
  icon,
  title,
  open,
  expandable,
  onToggle,
  expandOnRowClick,
  previewChevron,
  keepContentWhenOpen,
  collapsedContent,
  children,
  className,
  rowClassName,
  leadingClassName,
  titleClassName,
}: {
  icon: ReactNode;
  title: string;
  open: boolean;
  expandable: boolean;
  onToggle: () => void;
  /** Makes the complete title row the disclosure target. */
  expandOnRowClick?: boolean;
  /** Replaces the collapsed leading icon with a chevron on hover. Defaults to `expandable`. */
  previewChevron?: boolean;
  /** Keeps `collapsedContent` inline while open. */
  keepContentWhenOpen?: boolean;
  collapsedContent?: ReactNode;
  children?: ReactNode;
  className?: string;
  rowClassName?: string;
  leadingClassName?: string;
  titleClassName?: string;
}) {
  // A row with nothing to show is not a disclosure, and must not claim to be one: no
  // button role, no aria-expanded, no pointer cursor. Otherwise every row in the
  // transcript announces itself as expandable and half of them do nothing.
  const rowExpands = expandable && expandOnRowClick === true;
  const hoverChevron = previewChevron ?? expandable;

  const leading = (
    <span className={leadingClassName} data-disclosure-leading>
      {expandable && open ? (
        <IconChevron open />
      ) : expandable && hoverChevron && rowExpands ? (
        <span className="disclosure-leading-pair">
          <span className="disclosure-icon-idle">{icon}</span>
          <IconChevron />
        </span>
      ) : (
        icon
      )}
    </span>
  );

  return (
    <div className={className} data-disclosure data-open={open || undefined}>
      <div
        className={rowClassName}
        data-disclosure-row={rowExpands || undefined}
        data-expandable={expandable || undefined}
        role={rowExpands ? "button" : undefined}
        tabIndex={rowExpands ? 0 : undefined}
        aria-expanded={rowExpands ? open : undefined}
        onClick={rowExpands ? onToggle : undefined}
        onKeyDown={
          rowExpands
            ? (event) => {
                if (event.key !== "Enter" && event.key !== " ") return;
                // A div pretending to be a button must also stop the page from scrolling
                // on Space, which is what the browser does with it by default.
                event.preventDefault();
                onToggle();
              }
            : undefined
        }
      >
        {expandable && !rowExpands ? (
          <button
            type="button"
            className={leadingClassName}
            aria-expanded={open}
            aria-label={title}
            data-disclosure-toggle
            onClick={(event) => {
              // The chevron is one target among several on the row; activating it must not
              // also count as activating whatever the row itself does.
              event.stopPropagation();
              onToggle();
            }}
          >
            {leading}
          </button>
        ) : (
          leading
        )}
        <span className={titleClassName}>{title}</span>
        {(keepContentWhenOpen === true || !open) && collapsedContent}
      </div>
      <Disclosure open={open} className="disclosure-body">
        {children}
      </Disclosure>
    </div>
  );
}

function IconChevron({ open }: { open?: boolean }) {
  return (
    <svg
      aria-hidden="true"
      width={14}
      height={14}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.7}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={"disclosure-chevron" + (open ? " disclosure-chevron-open" : "")}
    >
      <path d="M9 6l6 6-6 6" />
    </svg>
  );
}
