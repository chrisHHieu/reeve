import { useEffect, useRef, useState } from "react";
import { checkForUpdate, clearPendingUpdate, downloadUpdate, installUpdate, isDesktop, type UpdateInfo } from "../desktop";

// The update offer, in the desktop shell only (the browser build never draws it).
//
// It asks rather than installing behind the person's back: replacing the app mid-session would end
// the turn their agent is running, and an app that changes itself quietly does not fit a local-first
// promise. "Later" puts off that version for as long as this run lasts; the check comes back every 30
// minutes, so an app left open for weeks still hears of the next release.
//
// The first check waits until boot has settled (the splash and the session restore have the first
// seconds). When a release is offered its files are fetched straight away, so "Restart to
// update" is instant instead of a download of minutes; if that fetch fails the button downloads
// when pressed. Whether the files are genuine is checked further down: the shell's updater compares
// each one with the sha512 the release lists (electron/updates.ts).

const FIRST_CHECK_MS = 15_000;
const RECHECK_MS = 30 * 60_000;

// downloading: fetching in the background, the button waits
// ready: the files are in the shell and installing is instant
// fallback: the fetch failed and the button downloads on click, as before
type Phase = "downloading" | "ready" | "fallback" | "installing" | "error";

export function UpdateBanner() {
  const [update, setUpdate] = useState<UpdateInfo | null>(null);
  const [phase, setPhase] = useState<Phase>("downloading");
  // The version put off with "Later", for this run only (nothing in localStorage): a restart offers
  // it again, and a newer release found later is offered whatever was put off.
  const putOff = useRef<string | null>(null);
  // The version being, or already, fetched, so a check that finds the same release again neither
  // starts another download nor resets the button.
  const fetching = useRef<string | null>(null);

  const offer = (found: UpdateInfo) => {
    if (found.version === putOff.current) return;
    setUpdate((shown) => (shown?.version === found.version ? shown : found));
    if (fetching.current === found.version) return;
    const version = (fetching.current = found.version);
    setPhase("downloading");
    // Each outcome checks it is still about this version: a newer release may have replaced it.
    downloadUpdate()
      .then(() => fetching.current === version && setPhase("ready"))
      .catch(() => fetching.current === version && setPhase("fallback"));
  };

  useEffect(() => {
    if (!isDesktop()) return;
    const check = () =>
      checkForUpdate()
        .then((found) => found && offer(found))
        .catch(() => {});
    const first = setTimeout(check, FIRST_CHECK_MS);
    const again = setInterval(check, RECHECK_MS);
    return () => {
      clearTimeout(first);
      clearInterval(again);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (!update) return null;

  const install = async () => {
    setPhase("installing");
    try {
      await installUpdate(); // on success the app restarts; nothing comes after
    } catch {
      setPhase("error");
    }
  };

  const later = () => {
    putOff.current = update.version;
    setUpdate(null);
    // The fetched files are tens of MB and a release put off may wait for weeks; they are let go,
    // and fetched again if the person changes their mind.
    fetching.current = null;
    clearPendingUpdate().catch(() => {});
  };

  const busy = phase === "downloading" || phase === "installing";

  return (
    // Over the sidebar column (the owner's call): its 300px less the 12px margins, low enough
    // to sit just above the account row (about 57px). z-[35] keeps it above the account menu's
    // click-away layer (z-30), so it can be clicked, and below the menu itself (z-40): a status card
    // must never cover a menu that is open.
    <div className="fixed bottom-[64px] left-3 z-[35] w-[276px] rounded-xl border border-line bg-panel shadow-2xl px-4 py-3.5" role="status" data-testid="update-banner">
      <div className="text-[13px] font-semibold">Update available</div>
      <div className="text-[12px] text-muted mt-0.5">Reeve v{update.version} is ready to install.</div>
      {phase === "error" && <div className="text-[12px] text-warnInk mt-1.5">The update couldn't be installed — it will be offered again next launch.</div>}
      <div className="flex items-center gap-2 mt-2.5">
        <button className="px-3 py-1.5 rounded-full bg-accent text-white text-[13px] disabled:opacity-50" onClick={install} disabled={busy} data-testid="update-install">
          {busy ? "Downloading…" : "Restart to update"}
        </button>
        <button className="px-2 py-1.5 text-[13px] text-faint hover:text-muted" onClick={later} disabled={phase === "installing"} data-testid="update-later">
          Later
        </button>
      </div>
    </div>
  );
}
