// The Browser tab body: a controlled HTTP(S) iframe carrier. History (the URL plus back/forward
// availability) lives in the parent tab object; this view owns the address draft, the sandbox, the
// page width and what it knows of the page before and while it loads (workspace panel redesign,
// 2026-10-11): a mistyped address is said under the box, a local dev server that does not answer
// says so (and the tab loads it once it does), a site that refuses frames is named instead of a
// blank pane, and a page that takes long offers Stop.
//
// The bar is drawn as OpenDesign's browser (the owner's pick, 2026-10-19): the address in a round
// field, then a picture of the page into the message, the page's look into the message (its
// colours, type and shape, read by Reeve's own browser), and a menu with icons. The
// framed page belongs to another site, so whatever is read of it is read by Reeve's browser,
// loading the same address signed out of everything.
import { useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";
import { browserCapture, browserFrameCheck, revealDownload } from "../../api";
import { BROWSER_PARTITION, canEmbedBrowser, openExternal } from "../../desktop";
import { displayHost, isLocalUrl, normalizeAddress } from "./browserUrl";
import { sameAddress, useBrowserPane } from "./browserPane";
import { copyPicture } from "./DesignActions";
import { attachToComposer } from "./designAsks";
import { Icon, type IconName } from "../Icon";
import "./browser.css";

export const BROWSER_SANDBOX = "allow-scripts allow-forms allow-same-origin allow-popups allow-popups-to-escape-sandbox";

interface Props {
  /** "" = start page (no document). */
  url: string;
  canGoBack: boolean;
  canGoForward: boolean;
  /** Commit a new address; the parent appends history and truncates forward. */
  onNavigate: (url: string) => void;
  onBack: () => void;
  onForward: () => void;
  /** "Open a terminal" for a dev server that is not running yet. */
  onOpenTerminal?: () => void;
  /** Forget this tab's back and forward. */
  onClearHistory?: () => void;
  /** The tab's id: in the desktop shell, the page is a browser of its own kept under it. */
  tabId?: string;
  /** The page went somewhere by itself (a link clicked in it): the parent records it. */
  onVisited?: (url: string) => void;
  /** The page's own title, as it changes. */
  onTitle?: (title: string) => void;
}

/** The width Reeve's browser reads the page at, for each width the tab shows it at. */
const READ_WIDTH: Record<"fit" | "phone" | "tablet", number> = { fit: 1280, phone: 390, tablet: 768 };

type Width = "fit" | "phone" | "tablet";
type Frame = "checking" | "loading" | "loaded" | "slow" | "stopped" | "refused" | "blocked";

const WIDTH_PX: Record<Width, string> = { fit: "100%", phone: "390px", tablet: "768px" };
/** How long a page may take before the tab says it is still waiting. */
const SLOW_MS = 5000;
/** How often a local address that did not answer is asked again. */
const RETRY_MS = 3000;

/** Whether something answers at a local address: a no-cors fetch rejects when nothing listens. */
async function answers(url: string): Promise<boolean> {
  try {
    await fetch(url, { mode: "no-cors", cache: "no-store" });
    return true;
  } catch {
    return false;
  }
}

function askReeve(text: string) {
  window.dispatchEvent(new CustomEvent("reeve-insert-text", { detail: { text } }));
}

function MenuItem({
  icon,
  label,
  testid,
  disabled,
  onClick,
}: {
  icon: IconName;
  label: string;
  testid: string;
  disabled?: boolean;
  onClick: () => void;
}) {
  return (
    <button type="button" role="menuitem" className="browser-menu-item" data-testid={testid} disabled={disabled} onClick={onClick}>
      <Icon name={icon} size={15} />
      <span>{label}</span>
    </button>
  );
}

export function BrowserView({
  url,
  canGoBack,
  canGoForward,
  onNavigate,
  onBack,
  onForward,
  onOpenTerminal,
  onClearHistory,
  tabId,
  onVisited,
  onTitle,
}: Props) {
  const [draft, setDraft] = useState(url);
  const [invalid, setInvalid] = useState<string | null>(null);
  const [sandboxed, setSandboxed] = useState(true);
  const [askSandbox, setAskSandbox] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);
  const [width, setWidth] = useState<Width>("fit");
  const [menuOpen, setMenuOpen] = useState(false);
  const [frame, setFrame] = useState<Frame>("checking");
  const [blockedBy, setBlockedBy] = useState("");
  const [asked, setAsked] = useState(false);
  const menuRef = useRef<HTMLDivElement | null>(null);
  // What Reeve's browser is doing for the tab, and what it last did (a picture, a file saved).
  const [working, setWorking] = useState<"shot" | "brief" | "copy" | "save" | null>(null);
  const [note, setNote] = useState<{ text: string; saved?: string } | null>(null);
  // In the desktop app the page is a browser of its own, a `<webview>` (browserPane.ts).
  const embedded = canEmbedBrowser() && !!tabId;
  // The address the page last finished loading at, as the page reported it: when the tab's
  // address follows the page there (a link clicked in it), it is already loaded.
  const paneLoaded = useRef("");

  useEffect(() => {
    setDraft(url);
    setInvalid(null);
    setAsked(false);
    setNote(null);
  }, [url]);

  useEffect(() => {
    if (!note) return;
    const timer = window.setTimeout(() => setNote(null), note.saved ? 12000 : 6000);
    return () => window.clearTimeout(timer);
  }, [note]);

  /** Ask Reeve's browser for the page: a picture into the message or onto the clipboard, its
   *  look into the message, or its HTML saved. */
  const read = async (what: "shot" | "brief" | "copy" | "save") => {
    if (!url || working) return;
    setMenuOpen(false);
    setWorking(what);
    const at = READ_WIDTH[width];
    const name = displayHost(url);
    try {
      // A picture of the page in the desktop app is the page as the person sees it, signed in;
      // Reeve's own browser would load it again, signed out.
      const seen = embedded && (what === "shot" || what === "copy") ? await pane.capture() : null;
      const done = seen
        ? { ok: true as const, png: seen, error: undefined, brief: undefined, saved: undefined, name: undefined, folder: undefined }
        : await browserCapture(url, what === "brief" ? "brief" : what === "save" ? "html" : "view", at).catch(() => null);
      if (!done?.ok) {
        setNote({ text: `Reeve's browser could not read ${name}: ${done?.error || "the server did not answer"}.` });
        return;
      }
      if (what === "shot" && done.png) {
        attachToComposer([{ kind: "image", name: `${name} · ${at} px`, mime: "image/png", data_url: done.png }]);
        setNote({ text: `A picture of ${name} is in your message.` });
      } else if (what === "copy" && done.png) {
        await copyPicture(done.png);
        setNote({ text: `Copied a picture of ${name}.` });
      } else if (what === "brief" && done.brief) {
        attachToComposer([{ kind: "text", name: `${name} · its look`, text: done.brief }]);
        setNote({ text: `The look of ${name} is in your message.` });
      } else if (what === "save" && done.saved) {
        setNote({ text: `Saved ${done.name} to ${done.folder}.`, saved: done.saved });
      }
    } catch (error) {
      setNote({ text: `Nothing was copied: ${error instanceof Error ? error.message : "the clipboard refused"}.` });
    } finally {
      setWorking(null);
    }
  };

  useEffect(() => {
    if (!menuOpen) return;
    const close = (event: MouseEvent) => {
      if (!menuRef.current?.contains(event.target as Node)) setMenuOpen(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMenuOpen(false);
    };
    window.addEventListener("mousedown", close);
    window.addEventListener("keydown", escape);
    return () => {
      window.removeEventListener("mousedown", close);
      window.removeEventListener("keydown", escape);
    };
  }, [menuOpen]);

  // What is known of the page before it is framed: a local address must answer, an outside one
  // must allow frames. Either way the tab loads it as soon as it can.
  useEffect(() => {
    if (!url) return;
    let alive = true;
    let retry: number | undefined;
    setFrame("checking");
    if (isLocalUrl(url)) {
      const probe = async () => {
        const up = await answers(url);
        if (!alive) return;
        if (up) setFrame(embedded && paneLoaded.current && sameAddress(paneLoaded.current, url) ? "loaded" : "loading");
        else {
          setFrame("refused");
          retry = window.setTimeout(probe, RETRY_MS);
        }
      };
      void probe();
    } else if (embedded) {
      // A browser of its own opens what refuses a frame: nothing to ask first.
      setFrame(paneLoaded.current && sameAddress(paneLoaded.current, url) ? "loaded" : "loading");
    } else {
      browserFrameCheck(url)
        .then((check) => {
          if (!alive) return;
          if (check?.ok && check.frameable === false) {
            setBlockedBy(check.reason || "");
            setFrame("blocked");
          } else setFrame("loading");
        })
        .catch(() => alive && setFrame("loading"));
    }
    return () => {
      alive = false;
      window.clearTimeout(retry);
    };
  }, [url, reloadKey, embedded]);

  useEffect(() => {
    if (frame !== "loading") return;
    const timer = window.setTimeout(() => setFrame((current) => (current === "loading" ? "slow" : current)), SLOW_MS);
    return () => window.clearTimeout(timer);
  }, [frame]);

  const submit = () => {
    const parsed = normalizeAddress(draft);
    if (parsed === null) {
      setInvalid(draft.trim());
      return;
    }
    setInvalid(null);
    onNavigate(parsed);
  };

  const onAddressKeyDown = (event: ReactKeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Enter") {
      event.preventDefault();
      submit();
    }
  };

  const hasDocument = url !== "";
  const host = hasDocument ? displayHost(url) : "";
  const framed = hasDocument && (frame === "loading" || frame === "loaded" || frame === "slow");
  const pane = useBrowserPane({
    id: tabId,
    url,
    onVisited: (next) => {
      if (!sameAddress(next, url)) onVisited?.(next);
    },
    onLoading: (loading, at) => {
      paneLoaded.current = loading ? "" : at;
      setFrame(loading ? "loading" : "loaded");
    },
    onTitle,
    onError: (message) => setNote({ text: `The page could not open here: ${message}` }),
  });
  const reload = () => (embedded ? pane.reload() : setReloadKey((key) => key + 1));

  return (
    <div className="browser-view" data-testid="browser-view">
      <div className="browser-toolbar">
        <button
          type="button"
          className="browser-tool"
          data-testid="browser-back"
          title="Back"
          aria-label="Back"
          disabled={!canGoBack}
          onClick={onBack}
        >
          <Icon name="arrowLeft" size={14} />
        </button>
        <button
          type="button"
          className="browser-tool"
          data-testid="browser-forward"
          title="Forward"
          aria-label="Forward"
          disabled={!canGoForward}
          onClick={onForward}
        >
          <Icon name="arrowRight" size={14} />
        </button>
        <button
          type="button"
          className="browser-tool"
          data-testid="browser-reload"
          title="Reload"
          aria-label="Reload"
          disabled={!hasDocument}
          onClick={reload}
        >
          {/* Turns while the page loads, the way a refresh does. */}
          <Icon
            name="rotateCw"
            size={14}
            running={hasDocument && (frame === "checking" || frame === "loading" || frame === "slow")}
          />
        </button>
        <div className={"browser-address" + (invalid !== null ? " bad" : "") + (draft.trim() !== url ? " dirty" : "")}>
          <Icon name="globe" size={14} className="browser-address-mark" />
          <input
            data-testid="browser-address"
            value={draft}
            placeholder="Type an address, like localhost:3000"
            spellCheck={false}
            aria-label="Address"
            aria-invalid={invalid !== null}
            aria-describedby={invalid !== null ? "browser-address-error" : undefined}
            onChange={(event) => {
              setDraft(event.target.value);
              if (invalid !== null) setInvalid(null);
            }}
            onKeyDown={onAddressKeyDown}
          />
          <button type="button" className="browser-tool browser-go" data-testid="browser-go" title="Go" aria-label="Go" onClick={submit}>
            <Icon name="cornerDownLeft" size={14} />
          </button>
        </div>
        <button
          type="button"
          className="browser-tool"
          data-testid="browser-shoot"
          title="Put a picture of this page in your message"
          aria-label="Put a picture of this page in your message"
          disabled={!hasDocument || working !== null}
          onClick={() => void read("shot")}
        >
          <Icon name={working === "shot" ? "loader" : "camera"} size={15} running={working === "shot"} />
        </button>
        <button
          type="button"
          className="browser-tool"
          data-testid="browser-brief"
          title="Put this page's look in your message: its colours, type and shape"
          aria-label="Put this page's look in your message"
          disabled={!hasDocument || working !== null}
          onClick={() => void read("brief")}
        >
          <Icon name={working === "brief" ? "loader" : "lightbulb"} size={15} running={working === "brief"} />
        </button>
        <div className="browser-menu-wrap" ref={menuRef}>
          <button
            type="button"
            className="browser-tool"
            data-testid="browser-more"
            aria-label="Page options"
            aria-haspopup="menu"
            aria-expanded={menuOpen}
            title="Page options"
            onClick={() => setMenuOpen((open) => !open)}
          >
            <Icon name="moreHorizontal" size={16} />
          </button>
          {menuOpen && (
            <div className="browser-menu" role="menu" aria-label="Page options" data-testid="browser-menu">
              <MenuItem icon="image" label="Copy a picture of the page" testid="browser-copy-shot" disabled={!hasDocument || working !== null} onClick={() => void read("copy")} />
              <MenuItem
                icon="rotateCw"
                label="Reload"
                testid="browser-menu-reload"
                disabled={!hasDocument}
                onClick={() => {
                  reload();
                  setMenuOpen(false);
                }}
              />
              <MenuItem
                icon="copy"
                label="Copy address"
                testid="browser-copy-url"
                disabled={!hasDocument}
                onClick={() => {
                  void navigator.clipboard?.writeText(url);
                  setMenuOpen(false);
                }}
              />
              <MenuItem
                icon="externalLink"
                label="Open in your browser"
                testid="browser-external"
                disabled={!hasDocument}
                onClick={() => {
                  openExternal(url);
                  setMenuOpen(false);
                }}
              />
              <div className="browser-menu-sep" />
              <div className="browser-menu-head">Page width</div>
              <div className="seg seg-words browser-width" role="group" aria-label="Page width">
                {(["fit", "phone", "tablet"] as Width[]).map((w) => (
                  <button
                    key={w}
                    type="button"
                    className={width === w ? "on" : ""}
                    aria-pressed={width === w}
                    data-testid={`browser-width-${w}`}
                    onClick={() => setWidth(w)}
                  >
                    {w === "fit" ? "Fit" : w === "phone" ? "Phone 390" : "Tablet 768"}
                  </button>
                ))}
              </div>
              <div className="browser-menu-sep" />
              <MenuItem icon="download" label="Save the page's HTML" testid="browser-save" disabled={!hasDocument || working !== null} onClick={() => void read("save")} />
              <MenuItem icon="lightbulb" label="Put its look in your message" testid="browser-brief-menu" disabled={!hasDocument || working !== null} onClick={() => void read("brief")} />
              <MenuItem
                icon="messageSquare"
                label="Ask Reeve about this page"
                testid="browser-ask"
                disabled={!hasDocument}
                onClick={() => {
                  askReeve(`About this page: ${url}\n\n`);
                  setAsked(true);
                  setMenuOpen(false);
                }}
              />
              <MenuItem
                icon="history"
                label="Clear this tab's history"
                testid="browser-clear-history"
                disabled={!onClearHistory || (!canGoBack && !canGoForward)}
                onClick={() => {
                  onClearHistory?.();
                  setMenuOpen(false);
                }}
              />
              <div className="browser-menu-sep" />
              {embedded ? (
                <MenuItem
                  icon="trash"
                  label="Clear cookies and site data"
                  testid="browser-clear-data"
                  disabled={!hasDocument}
                  onClick={() => {
                    setMenuOpen(false);
                    void pane
                      .clearData()
                      .then(() => setNote({ text: "Cleared the cookies and data of the pages opened here. The app's own are untouched." }))
                      .catch((error) => setNote({ text: `Nothing was cleared: ${error instanceof Error ? error.message : String(error)}` }));
                  }}
                />
              ) : (
                <MenuItem
                  icon="shield"
                  label={sandboxed ? "Run without the sandbox…" : "Turn the sandbox back on"}
                  testid="browser-sandbox"
                  onClick={() => {
                    setMenuOpen(false);
                    if (sandboxed) setAskSandbox(true);
                    else setSandboxed(true);
                  }}
                />
              )}
            </div>
          )}
        </div>
        {hasDocument && (frame === "checking" || frame === "loading" || frame === "slow") && (
          <span className="browser-loading" role="progressbar" aria-label="Loading" />
        )}
      </div>
      {invalid !== null && (
        <div className="browser-error" id="browser-address-error" data-testid="browser-error" role="alert">
          “{invalid}” is not a web address. Try <span className="mono">localhost:3000</span> or{" "}
          <span className="mono">https://example.com</span>.
        </div>
      )}
      {!sandboxed && (
        <div className="browser-warning" data-testid="browser-sandbox-warning" role="status">
          <span>This page runs without the sandbox.</span>
          <button type="button" className="browser-inline" onClick={() => setSandboxed(true)}>
            Turn the sandbox back on
          </button>
        </div>
      )}
      {asked && (
        <div className="browser-note" role="status" data-testid="browser-asked">
          Added {host} to your message.
        </div>
      )}
      {note && (
        <div className="browser-note browser-outcome" role="status" data-testid="browser-outcome">
          <span>{note.text}</span>
          {note.saved && (
            <button type="button" className="browser-inline" data-testid="browser-outcome-reveal" onClick={() => void revealDownload(note.saved!)}>
              Show in folder
            </button>
          )}
        </div>
      )}
      {!hasDocument ? (
        <div className="browser-start" data-testid="browser-start">
          <p className="browser-start-note">
            Type an address above, like localhost:3000. Pages open here in a sandbox. Anything else opens in your own browser from the ⋯ menu.
          </p>
        </div>
      ) : frame === "refused" ? (
        <div className="browser-state" data-testid="browser-refused">
          <div className="browser-state-title">Nothing is running on {host}</div>
          <div className="browser-state-text">Start it in a terminal, then try again. This tab loads it by itself once it answers.</div>
          <div className="browser-state-actions">
            {onOpenTerminal && (
              <button type="button" className="browser-primary" onClick={onOpenTerminal}>
                Open a terminal
              </button>
            )}
            <button type="button" className="browser-secondary" onClick={() => setReloadKey((key) => key + 1)}>
              Try again
            </button>
          </div>
        </div>
      ) : frame === "blocked" ? (
        <div className="browser-state" data-testid="browser-blocked">
          <div className="browser-state-title">{host} won’t open inside Reeve</div>
          <div className="browser-state-text">
            The site asks browsers not to show it inside another app{blockedBy ? ` (${blockedBy})` : ""}.
          </div>
          <div className="browser-state-actions">
            <button type="button" className="browser-primary" onClick={() => openExternal(url)}>
              Open in your browser
            </button>
          </div>
        </div>
      ) : frame === "stopped" ? (
        <div className="browser-state" data-testid="browser-stopped">
          <div className="browser-state-title">Stopped loading {host}</div>
          <div className="browser-state-actions">
            <button type="button" className="browser-secondary" onClick={() => setReloadKey((key) => key + 1)}>
              Load it again
            </button>
          </div>
        </div>
      ) : (
        <div className="browser-stage" data-width={width}>
          {framed && embedded && (
            <webview
              ref={pane.attach}
              className="browser-native"
              data-testid="browser-native"
              src={pane.startAt(url)}
              partition={BROWSER_PARTITION}
              allowpopups="true"
              style={{ width: WIDTH_PX[width] }}
            />
          )}
          {framed && !embedded && (
            <iframe
              key={reloadKey}
              className="browser-frame"
              data-testid="browser-frame"
              src={url}
              title={url}
              style={{ width: WIDTH_PX[width] }}
              sandbox={sandboxed ? BROWSER_SANDBOX : undefined}
              referrerPolicy="no-referrer"
              onLoad={() => setFrame("loaded")}
            />
          )}
          {frame === "slow" && (
            <div className="browser-slow" role="status" data-testid="browser-slow">
              <span>Still waiting for {host}…</span>
              <button type="button" className="browser-secondary" onClick={() => setFrame("stopped")}>
                Stop
              </button>
            </div>
          )}
        </div>
      )}
      {askSandbox && (
        <div className="fm-dialog-backdrop" onClick={() => setAskSandbox(false)}>
          <div
            className="fm-dialog"
            role="alertdialog"
            aria-modal="true"
            aria-labelledby="browser-sandbox-title"
            data-testid="browser-sandbox-dialog"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="fm-dialog-title" id="browser-sandbox-title">
              Run {host || "pages"} without the sandbox?
            </div>
            <div className="fm-dialog-hint">
              The page could then read this app’s storage and open windows. Only do this for a page you are building
              yourself. The sandbox comes back when you leave this tab.
            </div>
            <div className="fm-dialog-actions">
              <button type="button" className="btn sm" autoFocus onClick={() => setAskSandbox(false)}>
                Keep the sandbox
              </button>
              <button
                type="button"
                className="btn sm primary"
                data-testid="browser-sandbox-confirm"
                onClick={() => {
                  setSandboxed(false);
                  setAskSandbox(false);
                }}
              >
                Run without it
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
