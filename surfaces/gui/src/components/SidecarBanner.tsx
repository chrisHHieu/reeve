import { useEffect, useState } from "react";
import {
  isDesktop,
  onSidecarStatus,
  restartSidecar,
  sidecarStatus,
  type SidecarState,
} from "../desktop";

// The Python sidecar is the whole product: with it gone, every request fails and the
// window would spin forever (or, worse, keep showing a stale transcript while nothing
// works). The shell's supervisor restarts it on the SAME port with the SAME token — the
// SPA's injected endpoints stay valid, so recovery usually needs no reload at all — and
// this banner is how the user learns what is happening. It is deliberately the only
// surface that says "restarting": silent self-healing looks identical to a hang.

export function SidecarBanner() {
  const [state, setState] = useState<SidecarState | null>(null);

  useEffect(() => {
    if (!isDesktop()) return;
    // A reload during a restart would otherwise show nothing: ask once, then follow.
    let live = true;
    sidecarStatus()
      .then((s) => live && s && s.state !== "started" && setState(s))
      .catch(() => {});
    const off = onSidecarStatus((s) => {
      if (!live) return;
      setState(s.state === "started" ? null : s);
    });
    return () => {
      live = false;
      off();
    };
  }, []);

  if (!state) return null;

  const restarting = state.state === "restarting";
  return (
    <div
      className="fixed top-3 left-1/2 -translate-x-1/2 z-[35] w-[360px] rounded-xl border border-line bg-panel shadow-2xl px-4 py-3.5"
      role="status"
      data-testid="sidecar-banner"
    >
      <div className="text-[13px] font-semibold">
        {restarting ? "Reconnecting…" : "The local service stopped"}
      </div>
      <div className="text-[12px] text-muted mt-0.5">
        {restarting
          ? `Reeve's local service stopped unexpectedly — restarting it (${state.attempt}/${state.max_attempts}).`
          : `It could not be restarted${
              state.reason ? ` (${state.reason})` : ""
            }. Your session is safe on disk.`}
      </div>
      {!restarting && (
        <div className="flex items-center gap-2 mt-2.5">
          <button
            className="px-3 py-1.5 rounded-full bg-accent text-white text-[13px]"
            // A stopped server is started again by the shell; reloading the window never did.
            onClick={() => void restartSidecar()}
            data-testid="sidecar-reload"
          >
            Retry now
          </button>
        </div>
      )}
    </div>
  );
}
