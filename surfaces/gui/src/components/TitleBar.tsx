import { isDesktop, platformOS, minimizeWindow, toggleMaximizeWindow, closeWindow } from "../desktop";

/** The – □ × cluster, fixed to the window's top-right corner. Rendered frameless-only:
 * the surfaces' own top bars (session topbar, page shells) stay the topmost chrome, and
 * these float over whatever sits at the corner. */
export function WindowControls() {
  if (!isDesktop() || platformOS() === "macos") return null;
  return (
    <div className="window-controls">
      <button aria-label="Minimize" title="Minimize" onClick={minimizeWindow}>
        <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true">
          <path d="M0 5h10" stroke="currentColor" strokeWidth="1" />
        </svg>
      </button>
      <button aria-label="Maximize" title="Maximize / restore" onClick={toggleMaximizeWindow}>
        <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true">
          <rect x="0.5" y="0.5" width="9" height="9" fill="none" stroke="currentColor" strokeWidth="1" />
        </svg>
      </button>
      <button aria-label="Close" title="Close" className="titlebar-close" onClick={closeWindow}>
        <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true">
          <path d="M0 0l10 10M10 0L0 10" stroke="currentColor" strokeWidth="1" />
        </svg>
      </button>
    </div>
  );
}
