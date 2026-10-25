import { useEffect, useId, useRef, useState } from "react";
import { connectConnector, type Connector } from "../../api";
import { ConnectorBadge } from "../../connectors/ConnectorIcon";
import { Dialog } from "../../ui";
import { Icon } from "../Icon";
import { ConnectSetup } from "./ConnectSetup";
import { McpOneClick } from "./McpOneClick";
import { WhatsAppPairing } from "./WhatsAppPairing";
import "./connect.css";

export { McpOneClick };

// Every connection is added here and nowhere else: the connector page's
// header button and the list's Connect both open this sheet. A connector that can connect two ways
// gets a One click | Manual switch; one with a single way shows its ConnectSetup straight away.
//
// The sheet shows each of a connect's four moments (the owner's ask, 2026-10-10): ready (what is
// about to happen), waiting on the browser (with a way to open the page again, or to stop), failed
// (why, where the button was, and Try again), and done (a check and the account, then it closes by
// itself). It used to close the moment a poll saw the connection, so success looked just like the
// window being dismissed.

const FIELD = "w-full h-[34px] px-3 rounded-lg border border-line bg-panel text-[13px] text-ink outline-none transition-colors hover:border-lineStrong focus:border-accent";

/** How long the "Connected" moment stays before the sheet closes itself. */
const DONE_CLOSES_AFTER_MS = 1800;

export function AddConnectionModal({
  c,
  title,
  regrant,
  addAccount,
  onClose,
  onChanged,
}: {
  c: Connector;
  title?: string; // "Add a workspace", say; "Connect <title>" when not given
  /** Another account for a connector that has some: the ids it has now (see McpOneClick). */
  addAccount?: string[];
  // Ask an account already connected for what its chosen tool groups need: only the one-click
  // sign-in, no switch, and done when the sign-in is.
  regrant?: boolean;
  onClose: () => void;
  onChanged: () => void;
}) {
  // One click is OAuth on this computer against the vendor's hosted MCP server. Beside
  // manual fields (Atlassian) it is one of two ways; alone (Upwork) it is the whole flow.
  const oneClick = !!c.one_click;
  const twoModes = !regrant && !addAccount && oneClick && c.fields.length > 0;
  const [pane, setPane] = useState<"one" | "manual">("one");
  const [done, setDone] = useState<{ account: string | null } | null>(null);
  const headingId = useId();

  const finished = (account?: string | null) => {
    onChanged();
    setDone({ account: account ?? null });
  };

  // The latest close, read when the timer fires. Every caller passes an inline `onClose`, so
  // with it in the effect's deps each parent render restarted the 1.8 s wait, and a parent that
  // rendered more often than that never let the sheet close ("Account added" stayed up and its
  // scrim took every click; e2e accounts-page, 2026-10-15, passed once the wait stopped restarting).
  const close = useRef(onClose);
  close.current = onClose;
  useEffect(() => {
    if (!done) return;
    const t = window.setTimeout(() => close.current(), DONE_CLOSES_AFTER_MS);
    return () => window.clearTimeout(t);
  }, [done]);

  const heading =
    title || (regrant ? `More access for ${c.title}` : addAccount ? `Add a ${c.title} account` : `Connect ${c.title}`);

  return (
    <Dialog onClose={onClose} labelledBy={headingId} testId="add-connection-modal">
      <div className="connect-head">
        <ConnectorBadge connector={c} size={34} title={c.title} />
        <h2 className="connect-title" id={headingId}>
          {done ? (regrant ? "Access updated" : addAccount ? "Account added" : `Connected to ${c.title}`) : heading}
        </h2>
        <button type="button" className="connect-close" aria-label="Close" onClick={onClose}>
          <Icon name="x" size={15} />
        </button>
      </div>

      {done ? (
        <ConnectDone c={c} account={done.account} regrant={regrant} onClose={onClose} />
      ) : twoModes ? (
        <>
          <div className="connect-tabs-row">
            <div className="connect-tabs" role="tablist" aria-label="How to connect">
              {(["one", "manual"] as const).map((p) => (
                <button
                  key={p}
                  type="button"
                  role="tab"
                  aria-selected={pane === p}
                  data-testid={`modal-pane-${p}`}
                  className={"connect-tab" + (pane === p ? " on" : "")}
                  onClick={() => setPane(p)}
                >
                  {p === "one" ? "One click" : "Manual"}
                </button>
              ))}
            </div>
          </div>
          {pane === "one" ? (
            <McpOneClick c={c} onConnected={finished} />
          ) : (
            <div className="connect-manual">
              <ConnectSetup c={c} onConnected={() => finished(null)} manualOnly />
            </div>
          )}
        </>
      ) : oneClick ? (
        /* One-click with no manual fields (Upwork, Docs/Sheets/Slides): it IS the flow. */
        <McpOneClick c={c} regrant={regrant} addAccount={addAccount} onConnected={finished} />
      ) : c.auth === "pairing" ? (
        /* A personal WhatsApp number links by the phone, not by a field. */
        <WhatsAppPairing c={c} onConnected={() => finished(null)} />
      ) : c.name === "slack" ? (
        <SlackManual onConnected={() => finished(null)} />
      ) : (
        <div className="connect-manual">
          {/* The manual fields, for everything without a sign-in of its own. */}
          <ConnectSetup c={c} onConnected={() => finished(null)} />
        </div>
      )}
    </Dialog>
  );
}

/** The done moment: a check, who is signed in, and what changes now. Closes itself. */
function ConnectDone({
  c,
  account,
  regrant,
  onClose,
}: {
  c: Connector;
  account: string | null;
  regrant?: boolean;
  onClose: () => void;
}) {
  return (
    <div className="connect-body connect-done" role="status" data-testid="modal-connected">
      <span className="connect-done-mark" aria-hidden="true">
        <Icon name="check" size={20} />
      </span>
      <p className="connect-lede">
        {account ? (
          <>
            Signed in as <span className="connect-strong">{account}</span>.{" "}
          </>
        ) : null}
        {regrant
          ? "The tool groups you chose can be used now."
          : `${c.title}'s tools are ready in your next conversation.`}
      </p>
      <button type="button" className="connect-primary" data-autofocus data-testid="modal-connect-done" onClick={onClose}>
        Done
      </button>
    </div>
  );
}

/** Slack through the person's own app: its bot token and its Socket Mode app token. */
function SlackManual({ onConnected }: { onConnected: () => void }) {
  const [tokens, setTokens] = useState({ bot: "", app: "" });
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const bot = tokens.bot.trim();
  const app = tokens.app.trim();
  const connect = async () => {
    setChecking(true);
    setError(null);
    const answer = await connectConnector("slack", { bot_token: bot, app_token: app });
    setChecking(false);
    if (answer.ok) onConnected();
    else setError(answer.error || "could not connect");
  };
  const tokenField = (which: "bot" | "app", label: string, hint: string) => (
    <input
      className={FIELD}
      type="password"
      aria-label={label}
      placeholder={hint}
      value={tokens[which]}
      spellCheck={false}
      onChange={(e) => setTokens((was) => ({ ...was, [which]: e.target.value }))}
    />
  );
  return (
    <div className="connect-body">
      <ol className="connect-steps">
        <li>Create an app at api.slack.com/apps</li>
        <li>Turn on Socket Mode, give it bot scopes, install it in your workspace</li>
        <li>Paste both tokens</li>
      </ol>
      {tokenField("bot", "Bot token", "Bot token · xoxb-…")}
      {tokenField("app", "App token", "App token · xapp-…")}
      {error && (
        <div className="connect-error" role="alert">
          <Icon name="warning" size={15} />
          <div className="connect-status-text">{error}</div>
        </div>
      )}
      <button type="button" className="connect-primary" onClick={connect} disabled={checking || !bot || !app}>
        {checking ? "Checking the tokens…" : "Connect"}
      </button>
      <p className="connect-small connect-warn">One mode at a time — this pauses any relay workspaces.</p>
    </div>
  );
}
