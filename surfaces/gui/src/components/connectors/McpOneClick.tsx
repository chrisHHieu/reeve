import { useEffect, useRef, useState } from "react";
import {
  connectMcpBacked,
  getConnectAuthorizeUrl,
  getConnectors,
  type Connector,
  type DeviceCodePrompt,
} from "../../api";
import { openExternal } from "../../desktop";
import { Icon } from "../Icon";
import { VariantPicker, initialVariant } from "./VariantPicker";
import "./connect.css";

type Phase = "idle" | "starting" | "waiting" | "failed";

// One-click pane for MCP-BACKED connectors: the sidecar runs a fully LOCAL sign-in
// against the vendor's hosted MCP server — no client secret, no broker, no Reeve
// account. Two shapes come back:
//   - DCR (Atlassian, Notion, Upwork): the browser opens and the user just approves.
//   - device grant (GitHub): the vendor mints a short code the user pastes. GitHub
//     sends no pre-filled URL, so the code is copied to the clipboard and the paste is
//     the one extra step. `res.device` is what distinguishes them.
// Either way, poll until the card flips to connected, then report it.
//
// Shared by both one-click surfaces: this sheet and the card in Settings ▸ Connectors
// (`ConnectSetup`), so the two cannot drift (they did once: the Postman endpoint picker).
export function McpOneClick({
  c,
  onConnected,
  regrant,
  addAccount,
  inline,
}: {
  c: Connector;
  onConnected: (account?: string | null) => void;
  // The account is already connected: done when the sign-in that was started ends (it
  // reads as connected the whole time, so "connected" cannot be the finish line).
  regrant?: boolean;
  /** Adding another account to a connected connector: the ids it has now. It reads as connected
   * before and after, so the finish line is an account that was not there (and it is named). */
  addAccount?: string[];
  /** Drawn inside a card rather than a sheet: no sheet padding. */
  inline?: boolean;
}) {
  const [phase, setPhase] = useState<Phase>("idle");
  const [error, setError] = useState<string | null>(null);
  const [device, setDevice] = useState<DeviceCodePrompt | null>(null);
  const [copied, setCopied] = useState(false);
  const [slow, setSlow] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const waitFocus = useRef<HTMLButtonElement>(null);
  const failFocus = useRef<HTMLButtonElement>(null);
  // Multi-endpoint servers (Postman) are chosen here too, not only on the Connectors
  // page: this pane is the button most people press, and offering the choice on only
  // one of the two surfaces means the other silently takes the default.
  const variants = c.mcp_variants ?? [];
  const [variant, setVariant] = useState(initialVariant(c.mcp_variant, variants));
  const waiting = phase === "waiting";

  // Poll for the flip — and for the failure. Watching only `connected` meant a sign-in
  // that had already failed left this button saying "Check your browser…" for as long as
  // the dialog stayed open, while the reason sat unread on the server (owner-hit
  // 2026-09-08). A dialog that waits on something known to have failed is worse than an
  // error: it tells the user to go look at a browser window that was never opened.
  useEffect(() => {
    if (!waiting) return;
    const t = setInterval(async () => {
      try {
        const list = await getConnectors();
        const row = list.find((x) => x.name === c.name);
        const fresh = addAccount ? newAccount(row, addAccount) : null;
        if (addAccount) {
          if (fresh) onConnected(fresh);
          else if (row?.mcp_error && !row.mcp_authorizing) {
            setPhase("failed");
            setError(row.mcp_error);
          }
        } else if (regrant && row && !row.mcp_authorizing && !row.mcp_error) {
          onConnected(row.account);
        } else if (!regrant && row?.connected) {
          onConnected(row.account);
        } else if (row?.mcp_error && !row.mcp_authorizing) {
          setPhase("failed");
          setError(row.mcp_error);
        }
      } catch {
        /* keep polling */
      }
    }, 2000);
    return () => clearInterval(t);
  }, [waiting, c.name, onConnected, regrant, addAccount]);

  // A sign-in that has taken a while usually means the page opened behind another window, or
  // never opened at all: say so, next to the button that fixes it.
  useEffect(() => {
    if (!waiting) return;
    setSlow(false);
    const t = window.setTimeout(() => setSlow(true), 30_000);
    return () => window.clearTimeout(t);
  }, [waiting]);

  // The button that was pressed goes away with each phase; focus follows to the next action
  // instead of falling to the page (where Escape and Tab would leave the sheet).
  useEffect(() => {
    if (phase === "waiting") waitFocus.current?.focus({ preventScroll: true });
    if (phase === "failed") failFocus.current?.focus({ preventScroll: true });
  }, [phase]);

  const go = async () => {
    setError(null);
    setNote(null);
    setPhase("starting");
    const res = await connectMcpBacked(c.name, variants.length ? variant : undefined).catch(() => ({
      ok: false as const,
      error: "Reeve's local server did not answer.",
      device: undefined,
    }));
    if (!res.ok) {
      setError(res.error || "could not start the connect");
      setPhase("failed");
      return;
    }
    setPhase("waiting");
    if (res.device) {
      setDevice(res.device);
      // The code is on screen and about to be needed in another window — put it on the
      // clipboard so the step is a paste, not a transcription.
      if (res.device.needs_code_entry) {
        void navigator.clipboard
          ?.writeText(res.device.user_code)
          .then(() => setCopied(true))
          .catch(() => {});
      }
      openExternal(res.device.open_url);
    }
  };

  const reopen = async () => {
    setNote(null);
    if (device) {
      openExternal(device.open_url);
      return;
    }
    const url = await getConnectAuthorizeUrl(c.name);
    if (url) openExternal(url);
    else setNote("No sign-in page is waiting any more. Cancel, then connect again.");
  };

  const stop = () => {
    // The browser page may still finish; the list picks that up. Here the pane stops waiting,
    // and focus goes back to the button that starts it again.
    setPhase("idle");
    setDevice(null);
    setCopied(false);
    setNote(null);
    window.requestAnimationFrame(() => failFocus.current?.focus({ preventScroll: true }));
  };

  const body = "connect-body" + (inline ? " inline" : "");

  if (waiting || phase === "starting") {
    const starting = phase === "starting";
    return (
      <div className={body} data-testid={device ? "modal-device-prompt" : "modal-connect-waiting"} aria-busy={!starting}>
        {device ? (
          <>
            <p className="connect-lede">{c.title} opened in your browser. Enter this code there to finish:</p>
            <div className="connect-code" data-testid="modal-device-code">
              {device.user_code}
            </div>
            <p className="connect-small">
              {copied ? "Copied. Paste it at " : "Enter it at "}
              <button type="button" className="connect-link" onClick={() => openExternal(device.open_url)}>
                {device.verification_uri}
              </button>
            </p>
          </>
        ) : null}
        <div className="connect-status" aria-live="polite">
          <span className="connect-spinner" aria-hidden="true" />
          <div className="min-w-0">
            <div className="connect-status-title">
              {starting ? "Opening your browser…" : device ? "Waiting for you to approve…" : "Check your browser…"}
            </div>
            {!starting && !device ? (
              <div className="connect-status-text">
                Sign in to {c.title} and approve access. This updates by itself when you are done.
              </div>
            ) : null}
            {!starting && slow ? (
              <div className="connect-status-text" data-testid="modal-connect-slow">
                Nothing opened, or it is behind another window? Open the sign-in page again.
              </div>
            ) : null}
          </div>
        </div>
        {note ? <p className="connect-small connect-note">{note}</p> : null}
        <div className="connect-actions">
          <button type="button" className="connect-secondary" onClick={stop} data-testid="modal-connect-cancel" disabled={starting}>
            Cancel
          </button>
          <button
            ref={waitFocus}
            type="button"
            className="connect-secondary"
            onClick={() => void reopen()}
            data-testid="modal-connect-reopen"
            disabled={starting}
          >
            <Icon name="externalLink" size={13} /> Open sign-in page
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className={body}>
      <p className="connect-lede">
        {addAccount
          ? `Opens ${c.title} in your browser to sign in to another account. The accounts already here stay connected.`
          : regrant
          ? `Opens ${c.title} in your browser to approve the extra permission the chosen tool groups need. Your existing access stays as it is.`
          : `Opens ${c.title} in your browser — sign in and approve access there. No tokens typed, and no Reeve account needed: the sign-in runs entirely on this computer.`}
      </p>
      <VariantPicker name={c.name} variants={variants} value={variant} onChange={setVariant} />
      {phase === "failed" ? (
        <div className="connect-error" role="alert" data-testid="modal-connect-error">
          <Icon name="warning" size={15} />
          <div className="min-w-0">
            <div className="connect-status-title">Couldn&rsquo;t connect {c.title}</div>
            <div className="connect-status-text">{error}</div>
          </div>
        </div>
      ) : null}
      <button
        ref={failFocus}
        type="button"
        className="connect-primary"
        data-testid="modal-mcp-one-click"
        data-autofocus
        onClick={() => void go()}
      >
        {phase === "failed" ? "Try again" : regrant ? "Grant access" : addAccount ? "Sign in to another account" : `Connect ${c.title}`}
      </button>
      {phase === "failed" ? null : (
        <p className="connect-foot">
          <Icon name="shield" size={13} /> Sign-in runs on this computer · tokens stay here
        </p>
      )}
    </div>
  );
}

/** One account's id and the name it is shown by, across the two row shapes (Gmail and Calendar
 * key by email; the generic accounts layer by `account_id`, with a `name`). */
export function accountKey(a: { email?: string; account_id?: string; name?: string }): { id: string; name: string } {
  const id = a.account_id ?? a.email ?? "";
  return { id, name: a.name || a.email || id };
}

/** The account a connector has now that was not among `before`, by the name it is shown by. */
function newAccount(row: Connector | undefined, before: string[]): string | null {
  for (const a of (row?.accounts ?? []) as { email?: string; account_id?: string; name?: string }[]) {
    const { id, name } = accountKey(a);
    if (id && !before.includes(id)) return name;
  }
  return null;
}
