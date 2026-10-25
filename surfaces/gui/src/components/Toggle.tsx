// An on/off switch: a button with role="switch", drawn by the `.tgl` and `.knob` styles. Everything
// comes in through props, so a test finds it by role, reads aria-checked and clicks it. The Sources
// drawer uses it too.

export function Toggle({
  checked,
  onChange,
  disabled,
  title,
  ariaLabel,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  disabled?: boolean;
  title?: string;
  /** What a screen reader calls it, when no visible label is next to it. */
  ariaLabel?: string;
}) {
  const flip = () => {
    if (!disabled) onChange(!checked);
  };
  return (
    <button type="button" role="switch" className={checked ? "tgl on" : "tgl"} aria-checked={checked} aria-label={ariaLabel} disabled={disabled} title={title} onClick={flip}>
      <span className="knob" />
    </button>
  );
}
