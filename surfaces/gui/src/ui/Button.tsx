// The two buttons this app actually needs, in one place.
//
// Controls had drifted: the same "quiet secondary action" was written as four different
// class strings across the transcript, the rail and the composer, and each of them had its
// own hover/disabled/loading behaviour (or none). These are the design system's answer —
// `variant` picks the intent, `size` the density, and nothing else is negotiable. Loading is
// a first-class state rather than a caller swapping the label for "…", because a button that
// changes width when it starts working is the classic layout jump.

import type { ButtonHTMLAttributes, ReactNode } from "react";
import { Icon } from "../components/Icon";
import type { IconName } from "../components/Icon";
import { Tooltip } from "./Tooltip";

type Variant = "primary" | "secondary" | "ghost" | "danger";
type Size = "sm" | "md";

const VARIANT_CLASS: Record<Variant, string> = {
  primary: "ui-btn-primary",
  secondary: "ui-btn-secondary",
  ghost: "ui-btn-ghost",
  danger: "ui-btn-danger",
};

export function Button({
  variant = "secondary",
  size = "md",
  icon,
  loading,
  children,
  className,
  disabled,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: Variant;
  size?: Size;
  icon?: IconName;
  loading?: boolean;
  children?: ReactNode;
}) {
  return (
    <button
      type="button"
      className={`ui-btn ${VARIANT_CLASS[variant]} ui-btn-${size}${className ? ` ${className}` : ""}`}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      data-variant={variant}
      {...rest}
    >
      {loading ? (
        <span className="ui-spinner" aria-hidden />
      ) : (
        icon !== undefined && <Icon name={icon} size={size === "sm" ? 12 : 14} />
      )}
      {children}
    </button>
  );
}

export function IconButton({
  label,
  icon,
  size = "md",
  variant = "ghost",
  className,
  loading,
  ...rest
}: Omit<ButtonHTMLAttributes<HTMLButtonElement>, "aria-label"> & {
  /** Accessible name, and the tooltip. An icon button without one is not a control. */
  label: string;
  icon: IconName;
  size?: Size;
  variant?: Variant;
  loading?: boolean;
}) {
  return (
    <Tooltip label={label}>
      <button
        type="button"
        className={`ui-iconbtn ui-iconbtn-${size} ${VARIANT_CLASS[variant]}${className ? ` ${className}` : ""}`}
        aria-label={label}
        aria-busy={loading || undefined}
        {...rest}
      >
        {loading ? <span className="ui-spinner" aria-hidden /> : <Icon name={icon} size={size === "sm" ? 13 : 15} />}
      </button>
    </Tooltip>
  );
}
